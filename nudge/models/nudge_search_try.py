import logging

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError

from ..llm import embedder

_logger = logging.getLogger(__name__)

SEARCHED = ("nudge.doc.passage", "nudge.source.passage")
SHOWN = 10
EXCERPT = 300


class NudgeSearchTry(models.TransientModel):
    _name = "nudge.search.try"
    _description = "Try Search by Meaning"

    @api.depends()
    def _compute_display_name(self):
        for record in self:
            record.display_name = _("Try Search by Meaning")

    query = fields.Char(string="Look for")
    status = fields.Char(compute="_compute_status")
    howto = fields.Text(string="How to start it", compute="_compute_status")
    model_ready = fields.Boolean(compute="_compute_status")
    doc_passages = fields.Integer(string="Documentation Passages", compute="_compute_status")
    source_passages = fields.Integer(string="Company File Passages", compute="_compute_status")
    indexed = fields.Integer(string="Indexed", compute="_compute_status")
    waiting = fields.Integer(string="Waiting to Be Indexed", compute="_compute_status")
    last_indexed = fields.Datetime(compute="_compute_status")
    message = fields.Char(readonly=True)
    line_ids = fields.One2many("nudge.search.try.line", "wizard_id", string="Passages Found", readonly=True)

    def _check_manager(self):
        if not self.env.user.has_group("nudge.group_nudge_manager"):
            raise AccessError(_("Only Nudge managers try the search by meaning."))

    @api.depends_context("uid")
    def _compute_status(self):
        # sudo: the index is read for counts only; the passages shown are read as the person
        Embedding = self.env["nudge.embedding"].sudo()
        _ok, missing = Embedding._dependencies()
        name = Embedding._model_name()
        spec = embedder.spec_of(name)
        directory = Embedding._model_dir()
        downloaded = embedder.files_present(directory)
        state = Embedding._download_state()
        indexed, waiting = Embedding._counts()
        self.env.cr.execute("SELECT max(write_date) FROM nudge_embedding WHERE model_name = %s", (name,))
        last = self.env.cr.fetchone()[0]
        counts = {model: self.env[model].sudo().search_count([]) for model in SEARCHED}
        size = round(sum(item["size"] for item in spec["files"]) / 1048576)
        if missing:
            status = _("Off: the Python packages %s are not installed on the server.", ", ".join(missing))
            howto = _(
                "Ask your administrator to run: pip install %s. Then open Settings, Nudge, Local Search Model "
                "and press Download.", " ".join(missing))
        elif state.get("state") == "running":
            status = _("The model %s is downloading now.", spec["title"])
            howto = _("Wait for the download to end (Settings, Nudge, Local Search Model), then search.")
        elif not downloaded:
            status = _("Off: the model %(title)s is not downloaded.", title=spec["title"])
            howto = _(
                "An administrator opens Settings, Nudge, Local Search Model and presses Download "
                "(%(size)s MB, kept in the filestore). The passages are then indexed by the scheduled "
                "action in the background.", size=size)
        else:
            status = _("On: the model %s is downloaded.", spec["title"])
            howto = ""
        for wizard in self:
            wizard.status = status
            wizard.howto = howto
            wizard.model_ready = downloaded and not missing
            wizard.doc_passages = counts["nudge.doc.passage"]
            wizard.source_passages = counts["nudge.source.passage"]
            wizard.indexed = indexed
            wizard.waiting = waiting
            wizard.last_indexed = last

    def _found(self, query):
        """:return: ``[(score, record)]`` best first, among the passages this person may read, or
        None when the model is off. By meaning alone: no provider is called."""
        Embedding = self.env["nudge.embedding"]
        scored = []
        for res_model in SEARCHED:
            scores = Embedding._semantic(res_model, query)
            if scores is None:
                return None
            # As the person: the rules of the company files apply
            best = sorted(scores, key=scores.get, reverse=True)[:SHOWN * 3]
            records = self.env[res_model].search([("id", "in", best)])
            scored += [(scores[record.id], record) for record in records]
        scored.sort(key=lambda item: (-item[0], item[1]._name, item[1].id))
        return scored[:SHOWN]

    def _label(self, record):
        if record._name == "nudge.doc.passage":
            parts = [record.title or record.page_id.display_name, record.heading]
            return _("Odoo documentation: %s", " > ".join(part for part in parts if part))
        text = record.source_id.display_name or ""
        if record.page:
            text = _("%(file)s, page %(page)s", file=text, page=record.page)
        return _("Company file: %s", text)

    def action_search(self):
        self.ensure_one()
        self._check_manager()
        query = " ".join((self.query or "").split())
        found = self._found(query) if query else []
        if not query:
            message = _("Write what to look for.")
        elif found is None:
            message = _(
                "The search by meaning is off, so nothing was searched: %s", self.status or "")
        elif not found:
            message = _("No passage is near the meaning of this text.")
        else:
            message = ""
        self.write({
            "message": message,
            "line_ids": [Command.clear()] + [Command.create({
                "source": self._label(record), "score": round(score, 3),
                "excerpt": " ".join((record.body or "").split())[:EXCERPT],
                "res_model": record._name, "res_id": record.id,
            }) for score, record in (found or [])],
        })
        return self._reopen()

    def _reopen(self):
        return {
            "type": "ir.actions.act_window", "name": _("Try Search by Meaning"),
            "res_model": self._name, "res_id": self.id, "view_mode": "form", "target": "current",
        }

    @api.model
    def action_open(self):
        self._check_manager()
        return self.create({})._reopen()


class NudgeSearchTryLine(models.TransientModel):
    _name = "nudge.search.try.line"
    _description = "Try Search by Meaning, One Passage"
    _order = "score desc, id"

    wizard_id = fields.Many2one("nudge.search.try", required=True, ondelete="cascade")
    source = fields.Char()
    score = fields.Float(digits=(3, 3))
    excerpt = fields.Text()
    res_model = fields.Char()
    res_id = fields.Integer()
