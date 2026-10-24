"""Search by meaning, on the CPU of the Odoo server, with no AI provider.

An optional small model (multilingual-e5-small, run by ONNX Runtime) turns
the passages of the documentation, the company files and the remembered
answers into vectors, kept in this table; a question is turned into a vector
too and compared with them. The keyword search stays the first step: its hits
are re-ranked by meaning, and when it finds little or nothing (a question in
Arabic about an English page) the vectors are searched themselves.

Everything is optional. Without onnxruntime, tokenizers or the downloaded
model, nothing here does anything and the searches are what they were. The
model is a file in the filestore, put there by an administrator from the
Settings; the text never leaves the server, so private mode does not touch it.

Not covered: a search by meaning in the records of other models (partners,
products, tickets...).
"""
import json
import logging
import os
import re
import shutil
import time

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import config, sql

from ..llm import embedder

_logger = logging.getLogger(__name__)

MODEL_PARAM = "nudge.embedding_model"
PATH_PARAM = "nudge.embedding_path"
DOWNLOAD_PARAM = "nudge.embedding_download"
# What is indexed, in this order; the SQL says which rows are worth a vector
INDEXED = {
    "nudge.memory": "t.state = 'approved' AND t.active",
    "nudge.source.passage": "TRUE",
    "nudge.doc.passage": "TRUE",
}
# How much of the keyword score and of the meaning make the score of a passage
KEYWORD_WEIGHT = 0.35
INDEX_BATCH = 16
# Seconds of one pass of the cron: it goes on at the next one (a worker has about 120 s)
CRON_BUDGET = 45
DOWNLOAD_BUDGET = 45
IDLE_PARAM = "nudge.embedding_idle_minutes"
VERSION_SEQUENCE = "nudge_embedding_version_seq"
# A model name is one folder name: no path in it
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
MAX_SCANNED = 60000
# Kept in memory by each worker: the documentation and the company files, the two
# big ones. ``{(dbname, model name, version): {res_model: (ids, matrix)}}``; never
# shared between databases, and a new version of the index drops the old entries
CACHED = ("nudge.doc.passage", "nudge.source.passage")
_cache = {}


class NudgeEmbedding(models.Model):
    _name = "nudge.embedding"
    _description = "Nudge Search Vector"

    model_name = fields.Char(string="Model", required=True, index=True, readonly=True)
    res_model = fields.Char(string="Record Model", required=True, readonly=True)
    res_id = fields.Integer(string="Record", required=True, readonly=True)
    chunk = fields.Integer(readonly=True, default=0)
    vector = fields.Binary(attachment=False, readonly=True)

    def init(self):
        # The version of the index: a sequence, so that changing it touches neither the ORM
        # cache of the workers nor a row that two transactions wait for
        self.env.cr.execute(f"CREATE SEQUENCE IF NOT EXISTS {VERSION_SEQUENCE}")
        if not sql.table_exists(self.env.cr, self._table):
            return  # the table is made by the update of the module, which has not run yet
        sql.create_index(
            self.env.cr, "nudge_embedding_record_idx", self._table,
            ["res_model", "res_id", "model_name"])

    # ------------------------------------------------------------------
    # The model on this server
    # ------------------------------------------------------------------
    @api.model
    def _model_name(self):
        # sudo: a parameter of the whole server
        name = (self.env["ir.config_parameter"].sudo().get_param(MODEL_PARAM) or "").strip()
        if not name:
            return embedder.DEFAULT_MODEL
        if not SAFE_NAME.fullmatch(name) or ".." in name:
            _logger.warning("Nudge: %r is not a name that can be a folder: the default model is used", name)
            return embedder.DEFAULT_MODEL
        return name

    @api.model
    def _root(self):
        """:return: the folder that holds the models Nudge downloads"""
        return os.path.join(config.filestore(self.env.cr.dbname), "nudge_embeddings")

    @api.model
    def _own_dir(self):
        """:return: the folder of the model in the filestore, which is always inside
        :meth:`_root`"""
        root = os.path.realpath(self._root())
        directory = os.path.realpath(os.path.join(root, self._model_name()))
        if os.path.commonpath([root, directory]) != root or directory == root:
            raise UserError(_("The name of the model is not a valid folder name."))
        return directory

    @api.model
    def _model_dir(self):
        """:return: the folder of the model: the one set in the parameter, or its
        place in the filestore"""
        path = (self.env["ir.config_parameter"].sudo().get_param(PATH_PARAM) or "").strip()
        if path:
            return path
        return self._own_dir()

    @api.model
    def _dependencies(self):
        return embedder.dependencies()

    @api.model
    def _embedder(self):
        """:return: what turns texts into vectors, or None when the feature is off.
        The tests put a small function of their own here."""
        # sudo: a parameter of the whole server
        idle = self.env["ir.config_parameter"].sudo().get_param(IDLE_PARAM) or ""
        return embedder.load(self._model_name(), self._model_dir(), int(idle) if idle.isdigit() else None)

    @api.model
    def _is_ready(self):
        return self._embedder() is not None

    # ------------------------------------------------------------------
    # Searching
    # ------------------------------------------------------------------
    @api.model
    def _semantic(self, res_model, query, ids=None, subquery=None, params=()):
        """The meaning of ``query`` against the vectors of ``res_model``.

        :param ids: the records to compare with
        :param subquery: or SQL selecting their ids, with its ``params``
        :return: ``{id: score}`` from 0 to 1, only the ones above the floor of the model;
            None when the feature is off. Who may see what is the caller's to decide
            in ``ids`` or ``subquery``: this reads the vectors only.
        """
        engine = self._embedder()
        if engine is None:
            return None
        query = " ".join((query or "").split())[:500]
        if not query or (ids is not None and not ids):
            return {}
        try:
            vector = engine.embed([query], kind="query")[0]
        except Exception:  # noqa: BLE001 - a search must never fail because of the model
            _logger.exception("Nudge: the local search model failed on a question")
            return None
        if res_model in CACHED:
            cached = self._cached(res_model)
            if cached is not None:
                wanted = self._wanted_ids(ids, subquery, params)
                return self._scores_from(engine, vector, cached, wanted)
        where, values = "", [self._model_name(), res_model]
        if ids is not None:
            where, values = "AND e.res_id IN %s", values + [tuple(ids)]
        elif subquery:
            where, values = f"AND e.res_id IN ({subquery})", values + list(params)
        self.env.cr.execute(f"""
            SELECT e.res_id, e.vector FROM nudge_embedding e
            WHERE e.model_name = %s AND e.res_model = %s AND e.chunk = 0 {where}
            LIMIT {MAX_SCANNED}
        """, values)
        scores = {}
        for res_id, cosine in embedder.cosines(vector, self.env.cr.fetchall()):
            score = embedder.scale(cosine, engine.floor, engine.ceiling)
            if score > 0:
                scores[res_id] = score
        return scores

    @api.model
    def _wanted_ids(self, ids, subquery, params):
        """:return: the set of ids to compare with, or None for all of them"""
        if ids is not None:
            return set(ids)
        if subquery:
            self.env.cr.execute(subquery, list(params))
            return {row[0] for row in self.env.cr.fetchall()}
        return None

    @api.model
    def _scores_from(self, engine, vector, cached, wanted):
        keys, matrix = cached
        scores = {}
        for key, cosine in embedder.cosines_of(vector, keys, matrix):
            if wanted is None or key in wanted:
                score = embedder.scale(cosine, engine.floor, engine.ceiling)
                if score > 0:
                    scores[key] = score
        return scores

    @api.model
    def _version(self):
        self.env.cr.execute(f"SELECT last_value, is_called FROM {VERSION_SEQUENCE}")
        value, called = self.env.cr.fetchone()
        return f"{value}.{int(called)}"

    @api.model
    def _bump(self):
        """The index changed: every worker reads it again at its next question. A step of a
        sequence: no ORM cache is cleared and no row is locked. Called once for a whole
        indexing, not for each batch."""
        self.env.cr.execute(f"SELECT nextval('{VERSION_SEQUENCE}')")
        # A worker that asked between the change and its commit read the old index under the
        # new version: it reads again once the change is visible
        pool = self.pool
        self.env.cr.postcommit.add(lambda: self._bump_after_commit(pool))

    @api.model
    def _bump_after_commit(self, pool):
        try:
            with pool.cursor() as cr:
                cr.execute(f"SELECT nextval('{VERSION_SEQUENCE}')")
        except Exception:  # noqa: BLE001 - the next change makes it right
            _logger.info("Nudge: the version of the index could not be moved after the commit")

    @api.model
    def _cached(self, res_model):
        """:return: ``(ids, matrix)`` of all the vectors of ``res_model``, held by this
        worker and read once per version of the index; None when there are too many"""
        db = self.env.cr.dbname
        key = (db, self._model_name(), self._version())
        for old in [k for k in _cache if k[0] == db and k != key]:
            del _cache[old]
        held = _cache.setdefault(key, {})
        if res_model not in held:
            self.env.cr.execute(
                "SELECT e.res_id, e.vector FROM nudge_embedding e"
                " WHERE e.model_name = %s AND e.res_model = %s AND e.chunk = 0 LIMIT %s",
                (self._model_name(), res_model, MAX_SCANNED + 1))
            rows = self.env.cr.fetchall()
            held[res_model] = embedder.build_matrix(rows) if len(rows) <= MAX_SCANNED else False
        return held[res_model] or None

    @api.model
    def _blend(self, res_model, query, keyword, scan=None, ids=None):
        """Re-rank the hits of a keyword search by meaning.

        :param keyword: ``{id: score from 0 to 1}`` of the keyword hits
        :param scan: ``(sql, params)`` selecting the ids that may be found by
            meaning alone, when the keywords found too little
        :return: ``{id: score}`` blended, or None when the feature is off
        """
        pool = set(keyword) | set(ids or ())
        meaning = self._semantic(res_model, query, ids=sorted(pool)) if pool else {}
        if meaning is None:
            return None
        if scan:
            wider = self._semantic(res_model, query, subquery=scan[0], params=scan[1]) or {}
            wider.update(meaning)
            meaning = wider
        blended = {}
        for key in set(keyword) | set(meaning):
            blended[key] = (1 - KEYWORD_WEIGHT) * meaning.get(key, 0.0) + KEYWORD_WEIGHT * keyword.get(key, 0.0)
        return blended

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------
    @api.model
    def _pending(self, res_model, limit):
        """:return: ids of ``res_model`` that have no vector of the current model yet"""
        table = self.env[res_model]._table
        self.env.cr.execute(f"""
            SELECT t.id FROM {table} t
            LEFT JOIN nudge_embedding e ON e.res_model = %s AND e.res_id = t.id AND e.model_name = %s
            WHERE e.id IS NULL AND {INDEXED[res_model]}
            ORDER BY t.id LIMIT %s
        """, (res_model, self._model_name(), limit))
        return [row[0] for row in self.env.cr.fetchall()]

    @api.model
    def _index_ids(self, res_model, ids):
        """Make and keep the vectors of ``ids``. Returns how many were made."""
        engine = self._embedder()
        if engine is None or not ids:
            return 0
        # sudo: the vectors are made once, for everybody; who may find which record
        # is decided at the search, from the records the person may read. The
        # documentation is the same for all; the files and the answers are
        # filtered at the search by their group
        records = self.env[res_model].sudo().browse(ids).exists()
        texts = [record._embedding_text() for record in records]
        vectors = engine.embed(texts, kind="passage")
        model_name = self._model_name()
        self.env.cr.executemany(
            "INSERT INTO nudge_embedding (model_name, res_model, res_id, chunk, vector, create_date, write_date)"
            " VALUES (%s, %s, %s, 0, %s, now() at time zone 'utc', now() at time zone 'utc')",
            [(model_name, res_model, record.id, embedder.pack(vector))
             for record, vector in zip(records, vectors)])
        return len(records)

    @api.model
    def _drop_stale(self):
        """Forget the vectors of records that are gone and of other models."""
        cr = self.env.cr
        cr.execute("DELETE FROM nudge_embedding WHERE model_name != %s", (self._model_name(),))
        dropped = cr.rowcount
        for res_model, condition in INDEXED.items():
            table = self.env[res_model]._table
            cr.execute(f"""
                DELETE FROM nudge_embedding e WHERE e.res_model = %s
                AND NOT EXISTS (SELECT 1 FROM {table} t WHERE t.id = e.res_id AND {condition})
            """, (res_model,))
            dropped += cr.rowcount
        if dropped:
            self._bump()
        return dropped

    @api.model
    def _forget(self, res_model, ids):
        """The text of these records changed: their vectors are made again."""
        if ids:
            self.env.cr.execute(
                "DELETE FROM nudge_embedding WHERE res_model = %s AND res_id IN %s", (res_model, tuple(ids)))
            if self.env.cr.rowcount:
                self._bump()

    @api.model
    def _commit(self):
        # Tests run in one transaction, which they undo
        if not self.env.context.get("nudge_keep_transaction"):
            self.env.cr.commit()

    @api.model
    def _index_some(self, budget=CRON_BUDGET):
        """Index what has no vector yet, memory first, until ``budget`` seconds have gone.

        :return: how many vectors were made
        """
        if not self._is_ready():
            return 0
        started, made = time.monotonic(), 0
        dropped = self._drop_stale()
        for res_model in INDEXED:
            while time.monotonic() - started < budget:
                ids = self._pending(res_model, INDEX_BATCH)
                if not ids:
                    break
                made += self._index_ids(res_model, ids)
                # A batch at a time, so that a stopped server loses nothing
                self._commit()
        if made:
            # Once for the whole pass: the workers read the index again at their next question
            self._bump()
            self._commit()
        elif dropped:
            self._commit()
        return made

    @api.model
    def _counts(self):
        """:return: ``(indexed, to index)`` over everything that is indexed"""
        indexed = todo = 0
        for res_model, condition in INDEXED.items():
            table = self.env[res_model]._table
            self.env.cr.execute(f"SELECT count(*) FROM {table} t WHERE {condition}")
            total = self.env.cr.fetchone()[0]
            self.env.cr.execute(
                "SELECT count(*) FROM nudge_embedding WHERE model_name = %s AND res_model = %s",
                (self._model_name(), res_model))
            done = min(total, self.env.cr.fetchone()[0])
            indexed, todo = indexed + done, todo + total - done
        return indexed, todo

    # ------------------------------------------------------------------
    # Download, from the Settings
    # ------------------------------------------------------------------
    @api.model
    def _download_state(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(DOWNLOAD_PARAM)
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {}

    @api.model
    def _set_download_state(self, **state):
        """Written when the download starts, fails or ends, never while it runs: a parameter
        written empties the caches of every worker"""
        self.env["ir.config_parameter"].sudo().set_param(DOWNLOAD_PARAM, json.dumps(state) if state else False)

    @api.model
    def _downloaded(self, files):
        """:return: how many bytes of ``files`` are on the disk, whole or half made: how far the
        download is, read where it writes"""
        directory = self._model_dir()
        done = 0
        for item in files:
            for name in (item["name"], item["name"] + ".part"):
                path = os.path.join(directory, name)
                if os.path.isfile(path):
                    done += min(os.path.getsize(path), item["size"])
                    break
        return done

    @api.model
    def _check_admin(self):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only an administrator manages the local search model."))

    @api.model
    def _status(self):
        """:return: one line for the Settings: what the local search model is"""
        _ok, missing = self._dependencies()
        spec = embedder.spec_of(self._model_name())
        directory = self._model_dir()
        state = self._download_state()
        from odoo import _  # noqa: PLC0415
        if missing:
            return _(
                "Off. To use it, install the Python packages %s on the server (pip install %s), "
                "then download the model.", ", ".join(missing), " ".join(missing))
        if state.get("state") == "running":
            total = state.get("total") or 1
            return _("Downloading %(title)s: %(percent)s%% of %(size)s MB.",
                     title=spec["title"], percent=min(100, int(100 * self._downloaded(spec["files"]) / total)),
                     size=round(total / 1048576))
        if state.get("state") == "error":
            return _("The download failed: %s", state.get("error") or "")
        if not embedder.files_present(directory):
            size = round(sum(item["size"] for item in spec["files"]) / 1048576)
            return _(
                "Off: the model %(title)s is not downloaded (%(size)s MB in the filestore, from %(url)s).",
                title=spec["title"], size=size, url=spec["source"])
        mb = round(embedder.size_on_disk(directory) / 1048576)
        if not self._is_ready():
            return _("The model in %s could not be loaded; see the server log.", directory)
        indexed, todo = self._counts()
        return _(
            "On: %(title)s, %(mb)s MB. %(indexed)s passages and answers indexed, %(todo)s waiting.",
            title=spec["title"], mb=mb, indexed=indexed, todo=todo)

    @api.model
    def action_download(self):
        self._check_admin()
        _ok, missing = self._dependencies()
        if missing:
            raise UserError(_(
                "Install the Python packages %s on the server first (pip install %s).",
                ", ".join(missing), " ".join(missing)))
        spec = embedder.spec_of(self._model_name())
        if not spec["files"]:
            raise UserError(_(
                "The model %s is not one Nudge downloads: put its model.onnx and tokenizer.json in %s.",
                self._model_name(), self._model_dir()))
        self._set_download_state(state="running", total=sum(item["size"] for item in spec["files"]))
        self._start()
        return self._notify(_("The download started in the background."))

    @api.model
    def _start(self):
        self.env.ref("nudge.cron_embedding")._trigger()

    @api.model
    def _notify(self, message):
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"type": "success", "message": message, "next": {"type": "ir.actions.act_window_close"}},
        }

    @api.model
    def action_remove(self):
        self._check_admin()
        embedder.forget()
        directory = self._model_dir()
        # Only the folder Nudge made, and only inside its own place: a folder the
        # administrator pointed to is theirs
        if not (self.env["ir.config_parameter"].sudo().get_param(PATH_PARAM) or "").strip():
            self._own_dir()  # refuses a folder outside the root
            shutil.rmtree(directory, ignore_errors=True)
        else:
            for file in [item["name"] for item in embedder.spec_of(self._model_name())["files"]] + ["model.onnx"]:
                try:
                    os.remove(os.path.join(directory, file))
                except OSError:
                    pass
        self._bump()
        self.env.cr.execute("DELETE FROM nudge_embedding")
        self._set_download_state()
        return self._notify(_("The local search model was removed."))

    @api.model
    def action_reindex(self):
        self._check_admin()
        if not self._is_ready():
            raise UserError(_("The local search model is not ready."))
        self._bump()
        self.env.cr.execute("DELETE FROM nudge_embedding")
        self._start()
        return self._notify(_("The passages will be indexed again in the background."))

    @api.model
    def _download_some(self):
        """Go on with the download asked for in the Settings. Returns whether the model is there."""
        state = self._download_state()
        if state.get("state") != "running":
            return embedder.files_present(self._model_dir())
        spec = embedder.spec_of(self._model_name())
        try:
            for item in spec["files"]:  # NP-15
                self.env["nudge.egress"].check(item["url"], "model")
            # How far it is, the Settings read on the disk (_downloaded)
            finished = embedder.download(spec["files"], self._model_dir(), budget=DOWNLOAD_BUDGET)
        except Exception as error:  # noqa: BLE001 - said in the Settings, not lost in a log
            _logger.warning("Nudge: the download of the local search model failed: %s", error)
            self._set_download_state(state="error", error=str(error)[:300])
            return False
        if not finished:
            self._start()
            return False
        self._set_download_state()
        embedder.forget()
        return True

    @api.model
    def _cron_work(self):
        """The cron: the download if one was asked for, then the indexing, resumable."""
        if self._download_state().get("state") == "running":
            if not self._download_some():
                self._commit()
                return
        if self._is_ready():
            if self._index_some():
                self._start()
