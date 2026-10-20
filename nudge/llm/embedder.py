"""A small multilingual embedding model that runs on the CPU of the Odoo server.

Nothing here is imported at module load: ``onnxruntime`` and ``tokenizers``
are optional (the manifest names neither), so the module installs and works
without them and the feature is simply off. The model is a file an
administrator downloads on demand into the filestore; no AI provider is
involved and no text leaves the server.
"""
import array
import hashlib
import logging
import math
import os
import threading
import time

_logger = logging.getLogger(__name__)

# The model Nudge knows how to download: an int8 ONNX export of
# multilingual-e5-small (MIT licence), 384 numbers per text, 100 languages.
# The size and the SHA-256 of each file are checked after the download.
DEFAULT_MODEL = "multilingual-e5-small"
_HF = "https://huggingface.co/Xenova/multilingual-e5-small/resolve/main/"
MODELS = {
    DEFAULT_MODEL: {
        "title": "multilingual-e5-small (int8)",
        "source": "https://huggingface.co/Xenova/multilingual-e5-small",
        "dimension": 384,
        # e5 models want these prefixes: a question and a text are not alike
        "query_prefix": "query: ",
        "passage_prefix": "passage: ",
        # Cosines below ``floor`` mean "not about the same thing", ``ceiling``
        # and above "the same thing"
        "floor": 0.77,
        "ceiling": 0.90,
        "files": [
            {"name": "model.onnx", "url": _HF + "onnx/model_int8.onnx", "size": 118054593,
             "sha256": "4d24e2bc01a447951524466ef533e52944bf48509e6552810bcee1a2711cb02c"},
            {"name": "tokenizer.json", "url": _HF + "tokenizer.json", "size": 17082730,
             "sha256": "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"},
        ],
    },
}
# What a folder the administrator filled by hand is taken for
GENERIC = {"dimension": 0, "query_prefix": "", "passage_prefix": "", "floor": 0.5, "ceiling": 0.9}
MAX_TOKENS = 256
BATCH = 16


def dependencies():
    """:return: ``(ok, missing)``: whether onnxruntime and tokenizers can be
    imported, and the names of those that cannot"""
    missing = []
    for name in ("onnxruntime", "tokenizers"):
        try:
            __import__(name)
        except Exception:  # noqa: BLE001 - a broken wheel is as missing as none
            missing.append(name)
    return not missing, missing


def spec_of(name):
    return MODELS.get(name) or dict(GENERIC, title=name, files=[], source="")


def files_present(directory):
    """:return: whether ``model.onnx`` and ``tokenizer.json`` are in ``directory``"""
    return bool(directory) and all(
        os.path.isfile(os.path.join(directory, file)) for file in ("model.onnx", "tokenizer.json"))


def size_on_disk(directory):
    total = 0
    if directory and os.path.isdir(directory):
        for file in os.listdir(directory):
            path = os.path.join(directory, file)
            if os.path.isfile(path):
                total += os.path.getsize(path)
    return total


# ----------------------------------------------------------------------
# Vectors
# ----------------------------------------------------------------------
def pack(vector):
    """:return: the bytes (float32) that are kept for ``vector``"""
    return array.array("f", vector).tobytes()


def unpack(blob):
    values = array.array("f")
    values.frombytes(bytes(blob))
    return values


def scale(cosine, floor, ceiling):
    """:return: a cosine as a score from 0 to 1: ``floor`` and below is 0"""
    if ceiling <= floor:
        return 0.0
    return min(1.0, max(0.0, (cosine - floor) / (ceiling - floor)))


def cosines(query, rows):
    """:param query: normalised vector
    :param rows: ``[(key, blob)]`` of normalised vectors
    :return: ``[(key, cosine)]``; with numpy when it is there, plain Python otherwise"""
    if not rows:
        return []
    try:
        import numpy  # noqa: PLC0415
    except ImportError:
        numpy = None
    dimension = len(query)
    usable = [(key, bytes(blob)) for key, blob in rows if len(bytes(blob)) == dimension * 4]
    if not usable:
        return []
    if numpy is not None:
        matrix = numpy.frombuffer(b"".join(blob for _key, blob in usable), dtype=numpy.float32)
        scores = matrix.reshape(len(usable), dimension) @ numpy.asarray(query, dtype=numpy.float32)
        return [(key, float(score)) for (key, _blob), score in zip(usable, scores)]
    return [(key, sum(a * b for a, b in zip(query, unpack(blob)))) for key, blob in usable]


def build_matrix(rows):
    """:param rows: ``[(key, blob)]``
    :return: ``(keys, matrix)``: a float32 numpy array when numpy is there, a list of
        float arrays otherwise"""
    rows = [(key, bytes(blob)) for key, blob in rows]
    try:
        import numpy  # noqa: PLC0415
    except ImportError:
        return [key for key, _blob in rows], [unpack(blob) for _key, blob in rows]
    if not rows:
        return [], numpy.zeros((0, 0), dtype=numpy.float32)
    size = len(rows[0][1]) // 4
    rows = [(key, blob) for key, blob in rows if len(blob) == size * 4]
    matrix = numpy.frombuffer(b"".join(blob for _key, blob in rows), dtype=numpy.float32)
    return [key for key, _blob in rows], matrix.reshape(len(rows), size)


def cosines_of(query, keys, matrix):
    """:return: ``[(key, cosine)]`` of ``query`` with the rows of a ``build_matrix``"""
    if not len(keys):
        return []
    if isinstance(matrix, list):
        return [(key, sum(a * b for a, b in zip(query, row))) for key, row in zip(keys, matrix)
                if len(row) == len(query)]
    import numpy  # noqa: PLC0415
    if matrix.shape[1] != len(query):
        return []
    scores = matrix @ numpy.asarray(query, dtype=numpy.float32)
    return list(zip(keys, scores.tolist()))


# ----------------------------------------------------------------------
# The model
# ----------------------------------------------------------------------
class Embedder:
    """Turns texts into normalised vectors with an ONNX model and its tokenizer."""

    def __init__(self, name, directory):
        import onnxruntime  # noqa: PLC0415
        from tokenizers import Tokenizer  # noqa: PLC0415
        self.name = name
        self.directory = directory
        self.spec = spec_of(name)
        options = onnxruntime.SessionOptions()
        # The Odoo server has other work to do: a few threads, never all of them
        options.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2) // 2))
        self.session = onnxruntime.InferenceSession(
            os.path.join(directory, "model.onnx"), options, providers=["CPUExecutionProvider"])
        self.inputs = {item.name for item in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(os.path.join(directory, "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=MAX_TOKENS)
        pad_id = self.tokenizer.token_to_id("<pad>") or 0
        self.tokenizer.enable_padding(pad_id=pad_id, pad_token="<pad>")
        self.floor = self.spec["floor"]
        self.ceiling = self.spec["ceiling"]
        self._lock = threading.Lock()

    def embed(self, texts, kind="passage"):
        """:param kind: "query" for what a person asked, "passage" for what is searched
        :return: one normalised vector (list of floats) per text"""
        import numpy  # noqa: PLC0415
        prefix = self.spec.get(f"{kind}_prefix", "")
        vectors = []
        for start in range(0, len(texts), BATCH):
            encoded = self.tokenizer.encode_batch(
                [prefix + (text or "") for text in texts[start:start + BATCH]])
            ids = numpy.array([item.ids for item in encoded], dtype=numpy.int64)
            mask = numpy.array([item.attention_mask for item in encoded], dtype=numpy.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = numpy.zeros_like(ids)
            with self._lock:
                hidden = self.session.run(None, feed)[0]
            weights = mask[:, :, None].astype(numpy.float32)
            pooled = (hidden * weights).sum(axis=1) / numpy.maximum(weights.sum(axis=1), 1e-9)
            pooled /= numpy.maximum(numpy.linalg.norm(pooled, axis=1, keepdims=True), 1e-9)
            vectors.extend(row.tolist() for row in pooled)
        return vectors


_cache = {}
# One model per process, shared by every database served by it, loaded on the first
# question and dropped after ``IDLE_MINUTES`` without one: the session and the tokenizer
# hold about 450 MB of memory while they are loaded
IDLE_MINUTES = 15
_state = {"used": 0.0, "idle": IDLE_MINUTES * 60, "timer": None}
_guard = threading.Lock()


def _key(name, directory):
    """What tells one loaded model from another. A model Nudge downloads is the same file
    in every database (size and checksum are checked), so databases share it whatever
    their filestore; a folder filled by hand is told by its place and its date."""
    path = os.path.join(directory, "model.onnx")
    if name in MODELS:
        return (name, os.path.getsize(path))
    return (name, os.path.realpath(directory), os.path.getmtime(path))


def _unload_when_idle():
    with _guard:
        _state["timer"] = None
        if not _cache:
            return
        left = _state["used"] + _state["idle"] - time.monotonic()
        if left > 1:
            _arm(left)
            return
        _cache.clear()
    import gc  # noqa: PLC0415
    gc.collect()
    _logger.info("Nudge: the local search model was unloaded after %s idle minutes", round(_state["idle"] / 60))


def _arm(seconds):
    timer = threading.Timer(seconds, _unload_when_idle)
    timer.daemon = True
    _state["timer"] = timer
    timer.start()


def load(name, directory, idle_minutes=None):
    """:param idle_minutes: minutes without a question after which the model is dropped
    :return: the embedder of the model in ``directory``, kept for the next call;
    ``None`` when the packages or the files are missing"""
    ok, _missing = dependencies()
    if not ok or not files_present(directory):
        return None
    if idle_minutes:
        _state["idle"] = max(1, idle_minutes) * 60
    key = _key(name, directory)
    with _guard:
        if key not in _cache:
            try:
                _cache.clear()
                _cache[key] = Embedder(name, directory)
            except Exception:  # noqa: BLE001
                _logger.exception("Nudge: the local search model in %s could not be loaded", directory)
                return None
        _state["used"] = time.monotonic()
        if _state["timer"] is None:
            _arm(_state["idle"])
        return _cache[key]


def forget():
    with _guard:
        _cache.clear()


# ----------------------------------------------------------------------
# Downloading
# ----------------------------------------------------------------------
def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url, dest, offset=0, timeout=30):
    """Write the bytes of ``url`` to ``dest`` from ``offset``, yielding the
    size written so far after each block. Replaced in the tests."""
    import requests  # noqa: PLC0415
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with requests.get(url, stream=True, timeout=timeout, headers=headers) as response:
        response.raise_for_status()
        if offset and response.status_code != 206:
            offset = 0
        with open(dest, "ab" if offset else "wb") as out:
            written = offset
            for block in response.iter_content(1 << 18):
                out.write(block)
                written += len(block)
                yield written


def download(files, directory, progress=None, budget=None, fetcher=None):
    """Download ``files`` into ``directory``, resuming a half-made file and
    checking size and SHA-256 of each before it gets its name.

    :param progress: called with ``(bytes done of all, bytes of all)``
    :param budget: seconds after which to stop, to go on at the next call
    :return: True when every file is there and right; False when the time ran out
    :raise ValueError: when a file is not what was expected
    """
    fetcher = fetcher or fetch
    os.makedirs(directory, exist_ok=True)
    total = sum(item["size"] for item in files)
    started = time.monotonic()
    done_before = 0
    for item in files:
        final = os.path.join(directory, item["name"])
        if os.path.isfile(final) and os.path.getsize(final) == item["size"]:
            done_before += item["size"]
            continue
        part = final + ".part"
        offset = os.path.getsize(part) if os.path.isfile(part) else 0
        if offset > item["size"]:
            os.remove(part)
            offset = 0
        if offset < item["size"]:
            for written in fetcher(item["url"], part, offset):
                if progress:
                    progress(done_before + written, total)
                if budget and time.monotonic() - started > budget and written < item["size"]:
                    return False
        if not os.path.isfile(part) or os.path.getsize(part) != item["size"] \
                or sha256_of(part) != item["sha256"]:
            if os.path.isfile(part):
                os.remove(part)
            raise ValueError(
                f"{item['name']} is not the file that was expected (size or checksum); it was removed.")
        os.replace(part, final)
        done_before += item["size"]
    return True
