"""The official documentation of Odoo, kept in the database for Nudge to
search: every app, Community and Enterprise, for the version in use.

The pages come with the module, in data/odoo_docs.jsonl.gz, built by
scripts/build_odoo_docs.py from https://github.com/odoo/documentation
(CC BY-SA 4.0). They are loaded when Nudge is installed or updated, and
again when the file changes. Postgres searches them: a text vector kept by
the database itself, no embedding, no service outside.
"""
import gzip
import hashlib
import json
import logging

from odoo import api, fields, models
from odoo.tools import file_path, sql

from ..tools import noai_commands

_logger = logging.getLogger(__name__)

CORPUS = "nudge/data/odoo_docs.jsonl.gz"
CORPUS_PARAM = "nudge.odoo_docs_hash"
MAX_RESULTS = 5
# What a passage of the app on the screen is worth against the others: 2.5, as in the documentation of 17 and 18
# Studio's approval rules rank twice those of Time Off for "approval", and the screen of Time Off must still win
SCREEN_BOOST = 2.5
MAX_CHARS = 6000
MAX_QUERY_CHARS = 200
PAGES_PER_BATCH = 40
HINT_PAGES = 6


def _module_marks(modules):
    """Technical names kept as ' sale sale_management ': found with ilike ' sale '."""
    return f" {' '.join(modules)} " if modules else ""


class NudgeDocPage(models.Model):
    _name = "nudge.doc.page"
    _description = "Odoo Documentation Page"
    _order = "category, app, path"
    _rec_name = "title"

    path = fields.Char(required=True, readonly=True, index=True)
    url = fields.Char(string="URL", required=True, readonly=True)
    category = fields.Char(required=True, readonly=True, index=True)
    app = fields.Char(required=True, readonly=True, index=True)
    modules = fields.Char(
        readonly=True,
        help="Technical names of the modules the page is about, between spaces.")
    title = fields.Char(required=True, readonly=True)
    description = fields.Text(readonly=True)
    version = fields.Char(readonly=True)
    passage_ids = fields.One2many("nudge.doc.passage", "page_id", string="Passages")
    passage_count = fields.Integer(string="Number of Passages", compute="_compute_passage_count")

    _path_unique = models.Constraint("unique(path)", "A page of the documentation is loaded once.")

    @api.depends("passage_ids")
    def _compute_passage_count(self):
        for page in self:
            page.passage_count = len(page.passage_ids)

    def action_open(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": self.url, "target": "new"}

    # ----- Loading -----

    @api.model
    def _corpus_path(self):
        return file_path(CORPUS)

    @api.model
    def _corpus_digest(self):
        with open(self._corpus_path(), "rb") as corpus:
            return hashlib.sha256(corpus.read()).hexdigest()

    @api.model
    def _is_loaded(self):
        """:return: whether the file that comes with the module is in the database"""
        Param = self.env["ir.config_parameter"].sudo()
        return Param.get_param(CORPUS_PARAM) == self._corpus_digest() and bool(
            self.sudo().search_count([]))

    @api.model
    def _load_bundled(self, force=False):
        """Load the documentation that comes with the module, when the file
        changed since the last loading, or when asked.

        :return: the number of pages loaded, or False when nothing was done
        """
        if not force and self._is_loaded():
            return False
        Page = self.sudo()
        Page.search([]).unlink()
        self.env["nudge.embedding"]._bump()
        count = 0
        batch = []
        with gzip.open(self._corpus_path(), "rt", encoding="utf-8") as corpus:
            head = json.loads(corpus.readline())
            version = head.get("version", "")
            for line in corpus:
                page = json.loads(line)
                batch.append({
                    "path": page["path"],
                    "url": page["url"],
                    "category": page["category"],
                    "app": page["app"],
                    "modules": _module_marks(page.get("modules")),
                    "title": page["title"],
                    "description": page.get("description") or False,
                    "version": version,
                    "passage_ids": [
                        (0, 0, {
                            "sequence": position,
                            "heading": section.get("heading") or False,
                            "anchor": section.get("anchor") or False,
                            "body": section["text"],
                            "title": page["title"],
                            "app": page["app"],
                            "modules": _module_marks(page.get("modules")),
                        })
                        for position, section in enumerate(page["sections"], start=1)
                    ],
                })
                if len(batch) >= PAGES_PER_BATCH:
                    Page.create(batch)
                    count += len(batch)
                    batch = []
        if batch:
            Page.create(batch)
            count += len(batch)
        self.env["ir.config_parameter"].sudo().set_param(CORPUS_PARAM, self._corpus_digest())
        _logger.info("Nudge: %s pages of the Odoo %s documentation loaded", count, version)
        return count

    def action_reload(self):
        self.env["nudge.profile"].check_access("write")
        count = self._load_bundled(force=True)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": self.env._("%s pages of the documentation loaded.", count),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    @api.model
    def _summary(self):
        """:return: one line for the settings: what is loaded"""
        page = self.sudo().search([], limit=1)
        if not page:
            return self.env._("The documentation is not loaded.")
        return self.env._(
            "%(count)s pages of the official documentation of Odoo %(version)s.",
            count=self.sudo().search_count([]), version=page.version or "")

    # ----- For the screen -----

    @api.model
    def _modules_of_model(self, model_name):
        """:return: the technical module that defines ``model_name``, as a
        list; the ones that only extend it say nothing of its app"""
        if not model_name or model_name not in self.env:
            return []
        model = self.env["ir.model"].sudo().search([("model", "=", model_name)], limit=1)
        if not model:
            return []
        # The first module to have loaded the model is the one that defines it
        data = self.env["ir.model.data"].sudo().search_read(
            [("model", "=", "ir.model"), ("res_id", "=", model.id)], ["module"],
            order="id", limit=1)
        return [entry["module"] for entry in data if entry["module"]]

    @api.model
    def _pages_for_modules(self, modules, limit=HINT_PAGES):
        """:return: pages about ``modules``, the index pages of the app first"""
        if not modules:
            return self.browse()
        domain = ["|"] * (len(modules) - 1) + [("modules", "ilike", f" {module} ") for module in modules]
        pages = self.sudo().search(domain, order="path")
        # The page of the app itself comes before its chapters
        pages = pages.sorted(key=lambda page: (page.path.count("/"), page.path))
        return pages[:limit]

    @api.model
    def _hint_for_model(self, model_name):
        """:return: titles and links of the pages about the app of ``model_name``,
        for the context of a question; [] when there is nothing to say"""
        pages = self._pages_for_modules(self._modules_of_model(model_name))
        return [{"title": page.title, "url": page.url} for page in pages]


class NudgeDocPassage(models.Model):
    _name = "nudge.doc.passage"
    _description = "Odoo Documentation Passage"
    _order = "page_id, sequence"
    _rec_name = "heading"

    page_id = fields.Many2one(
        "nudge.doc.page", required=True, index=True, ondelete="cascade", readonly=True)
    sequence = fields.Integer(readonly=True)
    heading = fields.Char(readonly=True)
    anchor = fields.Char(readonly=True)
    body = fields.Text(required=True, readonly=True)
    # Of the page, kept here for the search
    title = fields.Char(readonly=True)
    app = fields.Char(readonly=True, index=True)
    modules = fields.Char(readonly=True)

    def init(self):
        # The text vector is kept by Postgres from the title, the heading and
        # the body: what the ORM never reads or writes
        self.env.cr.execute("""
            ALTER TABLE nudge_doc_passage
            ADD COLUMN IF NOT EXISTS search_vector tsvector
            GENERATED ALWAYS AS (
                setweight(to_tsvector('english', coalesce(title, '') || ' ' || coalesce(heading, '')), 'A')
                || setweight(to_tsvector('english', coalesce(body, '')), 'B')
            ) STORED
        """)
        sql.create_index(
            self.env.cr, "nudge_doc_passage_search_vector_idx", self._table,
            ["search_vector"], method="gin")

    @api.model
    def _search_text(self, query, app=None, modules=None, limit=MAX_RESULTS):
        """:param query: words, as in the documentation, in English
        :param app: folder of the documentation to look in, such as "sales"
        :param modules: technical modules of the screen the person is on; a
            passage about them comes before one that matches as well
        :return: passages, best match first
        """
        query = " ".join((query or "").split())[:MAX_QUERY_CHARS]
        if not query:
            return self.browse()
        # An Arabic question looks for the English pages by the English words of what it names (NP-12)
        keywords = query
        if noai_commands.bridge.has_arabic(query):
            # sudo: the words the companies of the person added are Nudge's own configuration
            words = noai_commands.bridge.terms(query, self.env["nudge.lexicon.entry"].sudo()._pairs())
            keywords = " or ".join(words)
        params = [keywords]
        app_filter = ""
        if app:
            # The pages of an app folder carry its name as their category;
            # the pages right under it carry their own name as app
            app_filter = "AND (p.app = %s OR g.category = %s)"
            params += [app, app]
        # A wide pool, so that a passage about the app on the screen can come forward
        params.append(limit * 10)
        self.env.cr.execute(f"""
            SELECT p.id, ts_rank_cd(p.search_vector, q) AS rank, p.modules
            FROM nudge_doc_passage p
            JOIN nudge_doc_page g ON g.id = p.page_id, websearch_to_tsquery('english', %s) q
            WHERE p.search_vector @@ q {app_filter}
            ORDER BY rank DESC, p.id
            LIMIT %s
        """, params)
        rows = self.env.cr.fetchall() if keywords else []
        marks = [f" {module} " for module in (modules or [])]
        # By meaning too, when the local search model is there: the keyword hits
        # are re-ranked, and when they are few (none, for a question in Arabic)
        # the passages whose vector is near that of the question are added
        top = max((row[1] for row in rows), default=0) or 1.0
        scan = None
        if len(rows) < limit * 3:
            scan = ("SELECT p.id FROM nudge_doc_passage p JOIN nudge_doc_page g ON g.id = p.page_id "
                    f"WHERE TRUE {app_filter}", [app, app] if app else [])
        blended = self.env["nudge.embedding"]._blend(
            self._name, query, {row[0]: row[1] / top for row in rows}, scan=scan)
        if blended is not None:
            known = {row[0]: row[2] for row in rows}
            for passage in self.sudo().browse([key for key in blended if key not in known]).exists():
                known[passage.id] = passage.modules

            def blended_score(item):
                boost = SCREEN_BOOST if any(mark in (known.get(item[0]) or "") for mark in marks) else 1.0
                return (-item[1] * boost, item[0])

            return self.browse([key for key, _score in sorted(blended.items(), key=blended_score)[:limit]]).exists()
        if not rows:
            return self.browse()

        def score(row):
            passage_id, rank, page_modules = row
            boost = SCREEN_BOOST if any(mark in (page_modules or "") for mark in marks) else 1.0
            return (-rank * boost, passage_id)

        rows.sort(key=score)
        return self.browse([row[0] for row in rows[:limit]])

    def unlink(self):
        self.env["nudge.embedding"]._bump()
        return super().unlink()

    def _embedding_text(self):
        self.ensure_one()
        return f"{self.title or ''}. {self.heading or ''}. {self.body or ''}"[:1500]

    def _source(self):
        self.ensure_one()
        return f"{self.page_id.url}#{self.anchor}" if self.anchor else self.page_id.url

    def _render(self, max_chars=MAX_CHARS):
        """:return: the passages as text for the model, each with the link of
        its page, within a size"""
        parts, size = [], 0
        for passage in self:
            heading = passage.title + (f" ▸ {passage.heading}" if passage.heading else "")
            head = f"## {heading}\n"
            tail = f"\nSource: {passage._source()}"
            text = head + passage.body + tail
            if size + len(text) > max_chars:
                if not parts:
                    # The first passage at least, cut short, with its source
                    room = max(0, max_chars - len(head) - len(tail))
                    parts.append(head + passage.body[:room].rstrip() + tail)
                break
            parts.append(text)
            size += len(text)
        return "\n\n".join(parts)
