"""What Nudge asks an Ollama server besides chatting with it.

Ollama answers chats on its OpenAI-compatible address (``…/v1``), which the
OpenAI-compatible adapter speaks. Its own API, next to it (``…/api``), tells
more: which models use tools or see pictures, how much context the server
gives a loaded model, and makes a copy of a model with more context. With no
Odoo import: the provider decides what to do with the answers.
"""
import re

import requests

from . import base

CONNECT_TIMEOUT = 5
READ_TIMEOUT = 20
# Making a model with more context copies no weights: seconds, not minutes
CREATE_TIMEOUT = 120
# The context Nudge gives a model it prepares is a multiple of this: the
# memory of a context grows with it, and a laptop's card has little to spare
CONTEXT_STEP = 4096
# A model prepared for Nudge answers from what it is given: less inventive
TEMPERATURE = 0.4
TAG = re.compile(r"^[A-Za-z0-9_.-]+$")


def root_of(base_url):
    """:return: the address of the server, without the ``/v1`` of the
    OpenAI-compatible API"""
    url = (base_url or "").strip().rstrip("/")
    return url[:-3] if url.endswith("/v1") else url


def _headers(api_key):
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _call(method, base_url, path, api_key="", payload=None, timeout=READ_TIMEOUT):
    """:return: the JSON answer
    :raise base.AdapterError: what went wrong, in the words of the adapters"""
    try:
        response = requests.request(
            method, root_of(base_url) + path, json=payload, headers=_headers(api_key),
            timeout=(CONNECT_TIMEOUT, timeout))
    except requests.exceptions.ReadTimeout as exc:
        raise base.AdapterError(base.ERROR_TIMEOUT, type(exc).__name__) from exc
    except requests.exceptions.RequestException as exc:
        raise base.AdapterError(base.ERROR_NETWORK, type(exc).__name__) from exc
    try:
        if response.status_code == 404:
            raise base.AdapterError(base.ERROR_MODEL_NOT_FOUND, _detail(response))
        if response.status_code in (401, 403):
            raise base.AdapterError(base.ERROR_AUTH, _detail(response))
        if response.status_code != 200:
            raise base.AdapterError(base.ERROR_BAD_REQUEST, _detail(response))
        try:
            return response.json()
        except ValueError as exc:
            raise base.AdapterError(base.ERROR_BAD_REQUEST, "Unexpected response") from exc
    finally:
        response.close()


def _detail(response):
    try:
        body = response.json()
    except ValueError:
        return (response.text or "")[:300]
    return str(body.get("error") if isinstance(body, dict) else body)[:300]


def version(base_url, api_key=""):
    """:return: the version of Ollama answering at ``base_url``, or "" when
    what answers there is not Ollama"""
    try:
        data = _call("GET", base_url, "/api/version", api_key, timeout=CONNECT_TIMEOUT)
    except Exception:  # noqa: BLE001
        # Whatever answers there, or does not: not Ollama, and fetching the
        # models goes on
        return ""
    return str(data.get("version") or "") if isinstance(data, dict) else ""


def describe(base_url, model, api_key=""):
    """:return: what a model can do: ``{"tools", "vision", "thinking",
    "context_max", "size"}``"""
    data = _call("POST", base_url, "/api/show", api_key, {"model": model})
    capabilities = set(data.get("capabilities") or [])
    info = data.get("model_info") or {}
    context_max = 0
    for key, value in info.items():
        if key.endswith(".context_length") and isinstance(value, int):
            context_max = max(context_max, value)
    details = data.get("details") or {}
    size = " ".join(filter(None, (details.get("parameter_size"), details.get("quantization_level"))))
    # A model made for Nudge says its context in its parameters: num_ctx 32768
    match = re.search(r"^num_ctx\s+(\d+)", data.get("parameters") or "", flags=re.M)
    return {
        "tools": "tools" in capabilities,
        "vision": "vision" in capabilities,
        "thinking": "thinking" in capabilities,
        "context_max": context_max,
        "context_set": int(match.group(1)) if match else 0,
        "size": size,
    }


def loaded_context(base_url, model, api_key=""):
    """:return: the context the server gives ``model`` now that it is loaded,
    0 when it is not loaded or the server does not say"""
    data = _call("GET", base_url, "/api/ps", api_key, timeout=CONNECT_TIMEOUT)
    wanted = {model, model if ":" in model else f"{model}:latest"}
    for item in data.get("models") or []:
        if item.get("name") in wanted or item.get("model") in wanted:
            return int(item.get("context_length") or 0)
    return 0


def name_for_nudge(model):
    """:return: the name of the copy of ``model`` prepared for Nudge"""
    name, _sep, tag = model.partition(":")
    tag = tag or "latest"
    if tag.endswith("-nudge"):
        return model
    return f"{name}:{tag}-nudge"


def context_for(needed, context_max):
    """:return: the context to give a model: what Nudge needs, rounded up to
    a step, never more than the model knows"""
    steps = -(-needed // CONTEXT_STEP)
    context = steps * CONTEXT_STEP
    return min(context, context_max) if context_max else context


def prepare(base_url, model, context, api_key=""):
    """Make a copy of ``model`` that the server runs with ``context`` tokens.
    No weights are copied: Ollama shares them.
    :return: the name of the copy"""
    target = name_for_nudge(model)
    if not TAG.match(target.partition(":")[2]):
        raise base.AdapterError(base.ERROR_BAD_REQUEST, f"{model!r} cannot be named for Nudge")
    parameters = {"num_ctx": int(context), "temperature": TEMPERATURE}
    _call("POST", base_url, "/api/create", api_key,
          {"model": target, "from": model, "parameters": parameters, "stream": False},
          timeout=CREATE_TIMEOUT)
    return target


def memory(base_url, model, api_key="", load=False):
    """:param load: load the model first, when it is not
    :return: ``(size, on_gpu)``: the bytes the loaded model takes, and those on
    the graphics card; ``(0, 0)`` when it is not loaded or the server does not
    say. What is not on the card runs on the processor, several times slower."""
    if load:
        _call("POST", base_url, "/api/generate", api_key,
              {"model": model, "prompt": "", "keep_alive": "10m", "stream": False}, timeout=CREATE_TIMEOUT)
    data = _call("GET", base_url, "/api/ps", api_key, timeout=CONNECT_TIMEOUT)
    wanted = {model, model if ":" in model else f"{model}:latest"}
    for item in data.get("models") or []:
        if item.get("name") in wanted or item.get("model") in wanted:
            return int(item.get("size") or 0), int(item.get("size_vram") or 0)
    return 0, 0


def on_processor(size, on_gpu):
    """:return: the share of a loaded model that runs on the processor, in
    percent; None when the server did not say"""
    if not size:
        return None
    if on_gpu >= size * 0.98:
        return 0
    return round(100 * (size - on_gpu) / size)
