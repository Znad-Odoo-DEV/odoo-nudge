"""The company's own files, as a source Nudge answers from.

A manager uploads the policies, procedures, price lists and catalogues of
the business once. Each file is read on the server, cut into passages, and
searched by Postgres when someone asks about the company's own rules; the
answer cites the file and the page. A file may be kept for some groups: the
payroll policy is for HR.
"""
import logging
import re

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import sql

from ..llm import attachments

_logger = logging.getLogger(__name__)

PASSAGE_CHARS = 1200
MAX_PASSAGES = 2000
MAX_RESULTS = 6
MAX_CHARS = 6000
MAX_QUERY_CHARS = 200
PARAGRAPH = re.compile(r"\n\s*\n")


def split_passages(pages):
    """:param pages: list of (page number, text)
    :return: list of (page number, text) of about PASSAGE_CHARS each, cut at
        paragraphs, never inside a word"""
    passages = []
    for number, text in pages:
        current = ""
        for paragraph in PARAGRAPH.split(text or ""):
            paragraph = " ".join(paragraph.split())
            if not paragraph:
                continue
            while len(paragraph) > PASSAGE_CHARS:
                cut = paragraph.rfind(" ", 0, PASSAGE_CHARS)
                cut = cut if cut > PASSAGE_CHARS // 2 else PASSAGE_CHARS
                if current:
                    passages.append((number, current))
                    current = ""
                passages.append((number, paragraph[:cut].strip()))
                paragraph = paragraph[cut:].strip()
            if current and len(current) + len(paragraph) + 1 > PASSAGE_CHARS:
                passages.append((number, current))
                current = paragraph
            else:
                current = f"{current} {paragraph}".strip()
        if current:
            passages.append((number, current))
    return passages[:MAX_PASSAGES]


class NudgeSource(models.Model):
    _name = "nudge.source"
    _description = "Nudge Company File"
    _order = "name"

    name = fields.Char(required=True)
    purpose = fields.Char(
        string="What It Is For",
        help="One line Nudge reads to know when to look in it: \"HR policy: leave, working "
             "hours, end of service\".")
    file = fields.Binary(string="File", attachment=True)
    file_name = fields.Char()
    mimetype = fields.Char(readonly=True)
    group_ids = fields.Many2many(
        "res.groups", string="For the Groups",
        help="Who may be answered from this file. Empty: everybody who uses Nudge.")
    active = fields.Boolean(default=True)
    state = fields.Selection(
        [("empty", "No File"), ("ready", "Ready"), ("failed", "Could Not Be Read")],
        default="empty", readonly=True)
    error = fields.Char(readonly=True)
    passage_ids = fields.One2many("nudge.source.passage", "source_id", readonly=True)
    passage_count = fields.Integer(compute="_compute_passage_count")
    pages = fields.Integer(readonly=True)
    chars = fields.Integer(string="Characters", readonly=True)

    @api.depends("passage_ids")
    def _compute_passage_count(self):
        for source in self:
            source.passage_count = len(source.passage_ids)

    @api.model_create_multi
    def create(self, values_list):
        sources = super().create(values_list)
        sources._read_file()
        return sources

    def write(self, values):
        result = super().write(values)
        if "file" in values or "file_name" in values:
            self._read_file()
        return result

    def action_read_again(self):
        self._read_file()

    def _read_file(self):
        """Read the file into passages; a file that cannot be read says why."""
        Passage = self.env["nudge.source.passage"].sudo()
        for source in self:
            source.sudo().passage_ids.unlink()
            data = source.with_context(bin_size=False).file
            if not data:
                source.sudo().write({"state": "empty", "error": False, "pages": 0, "chars": 0, "mimetype": False})
                continue
            import base64  # noqa: PLC0415
            raw = base64.b64decode(data)
            name = source.file_name or source.name
            kind = attachments.kind_of(name, "")
            try:
                if kind == "pdf":
                    pages = attachments.pdf_page_texts(raw)
                    mimetype = "application/pdf"
                elif kind == "text":
                    pages = [(1, raw.decode("utf-8", errors="replace"))]
                    mimetype = "text/plain"
                else:
                    raise attachments.Unreadable("Only PDF and text files can be read.")
                if len(raw) > attachments.MAX_BYTES:
                    raise attachments.Unreadable("The file is too large.")
            except attachments.Unreadable as error:
                source.sudo().write({"state": "failed", "error": str(error), "pages": 0, "chars": 0})
                continue
            passages = split_passages(pages)
            if not passages:
                source.sudo().write({
                    "state": "failed", "pages": len(pages), "chars": 0,
                    "error": "The file holds no text to read: a scanned PDF needs to be converted to text first.",
                })
                continue
            Passage.create([
                {"source_id": source.id, "sequence": index, "page": page, "body": body}
                for index, (page, body) in enumerate(passages)
            ])
            source.sudo().write({
                "state": "ready", "error": False, "mimetype": mimetype,
                "pages": len(pages), "chars": sum(len(body) for _page, body in passages),
            })

    @api.model
    def _for_person(self):
        """:return: the files this person may be answered from"""
        return self.search([("state", "=", "ready")])

    @api.model
    def _get_text(self):
        """:return: the files this person may be answered from, in words, for
        the system prompt; empty when there are none"""
        lines = []
        for source in self._for_person():
            line = f"- {source.name}"
            if source.purpose:
                line += f": {' '.join(source.purpose.split())}"
            lines.append(line)
        if not lines:
            return ""
        return (
            "## Company files\n"
            "Files the people who run this database uploaded for you, which search_company_files "
            "reads. For the company's own rules, policies, prices, procedures and products, look "
            "there first and cite the file and the page.\n" + "\n".join(lines[:40]))


class NudgeSourcePassage(models.Model):
    _name = "nudge.source.passage"
    _description = "Nudge Company File Passage"
    _order = "source_id, sequence"
    _rec_name = "source_id"

    source_id = fields.Many2one("nudge.source", required=True, index=True, ondelete="cascade", readonly=True)
    sequence = fields.Integer(readonly=True)
    page = fields.Integer(readonly=True)
    body = fields.Text(required=True, readonly=True)

    def init(self):
        # Kept by Postgres from the body; "simple" because the files may be in
        # any language: words are matched as they are written
        self.env.cr.execute("""
            ALTER TABLE nudge_source_passage
            ADD COLUMN IF NOT EXISTS search_vector tsvector
            GENERATED ALWAYS AS (to_tsvector('simple', coalesce(body, ''))) STORED
        """)
        sql.create_index(
            self.env.cr, "nudge_source_passage_search_vector_idx", self._table,
            ["search_vector"], method="gin")

    @api.model
    def _search_text(self, query, limit=MAX_RESULTS):
        """:return: passages of the files this person may read, best first"""
        query = " ".join((query or "").split())[:MAX_QUERY_CHARS]
        sources = self.env["nudge.source"]._for_person()
        if not query or not sources:
            return self.browse()
        self.env.cr.execute("""
            SELECT p.id, ts_rank_cd(p.search_vector, q) AS rank
            FROM nudge_source_passage p, websearch_to_tsquery('simple', %s) q
            WHERE p.search_vector @@ q AND p.source_id IN %s
            ORDER BY rank DESC, p.id
            LIMIT %s
        """, (query, tuple(sources.ids), limit * 3))
        rows = self.env.cr.fetchall()
        if not rows:
            # Words written another way: the start of a word, any language
            words = [word for word in query.split() if len(word) >= 3][:5]
            if words:
                domain = [("source_id", "in", sources.ids)]
                for word in words:
                    domain.append(("body", "ilike", word))
                return self.search(domain, limit=limit)
            return self.browse()
        return self.browse([row[0] for row in rows[:limit]])

    def _render(self, max_chars=MAX_CHARS):
        parts, size = [], 0
        for passage in self:
            head = f"## {passage.source_id.name}, page {passage.page}\n"
            tail = f"\nSource: {passage.source_id.name}, page {passage.page}"
            text = head + passage.body + tail
            if size + len(text) > max_chars:
                if not parts:
                    room = max(0, max_chars - len(head) - len(tail))
                    parts.append(head + passage.body[:room].rstrip() + tail)
                break
            parts.append(text)
            size += len(text)
        return "\n\n".join(parts)
