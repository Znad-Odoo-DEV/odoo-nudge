import base64
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .nudge_recipe import KINDS, MODELS, canonical

FLAGS = {
    "with_tours": "tour", "with_rules": "rule", "with_presets": "preset", "with_health": "health",
    "with_lexicon": "lexicon", "with_memories": "memory", "with_files": "file",
    "with_screens": "screen", "with_prompts": "prompt", "with_skills": "skill",
}


class NudgeRecipeExport(models.TransientModel):
    _name = "nudge.recipe.export"
    _description = "Export a Nudge Recipe"

    with_tours = fields.Boolean(string="Recorded tours", default=True)
    with_rules = fields.Boolean(string="Rule tasks (no AI)", default=True)
    with_presets = fields.Boolean(string="Task and digest presets", default=True)
    with_health = fields.Boolean(string="Health check settings", default=True)
    with_lexicon = fields.Boolean(string="Command words of the company", default=True)
    with_memories = fields.Boolean(string="Remembered answers (approved)", default=True)
    with_files = fields.Boolean(string="Company files (their description)", default=True)
    with_screens = fields.Boolean(string="Key screens of the training of new users", default=True)
    with_prompts = fields.Boolean(string="Prompts (shared ones)", default=True)
    include_files = fields.Boolean(
        string="Include the files themselves",
        help="Only the files that are public go in. The others come with their description only.")
    only_model = fields.Char(readonly=True)
    only_ids = fields.Char(readonly=True)
    file = fields.Binary(string="Recipe", readonly=True, attachment=False)
    file_name = fields.Char(readonly=True)
    item_count = fields.Integer(readonly=True)
    state = fields.Selection([("choose", "Choose"), ("done", "Done")], default="choose")

    @api.model
    def _open_for(self, records):
        """:return: the window of the export, with ``records`` (a selection in a list) as the only thing in it"""
        context = {}
        if records:
            kinds = [kind for kind, model in MODELS.items() if model == records._name]
            for flag, kind in FLAGS.items():
                context[f"default_{flag}"] = kind in kinds
            context.update(default_only_model=records._name, default_only_ids=json.dumps(records.ids))
        return {
            "type": "ir.actions.act_window", "res_model": self._name, "view_mode": "form",
            "target": "new", "name": _("Export a recipe"), "context": context,
        }

    def action_export(self):
        self.ensure_one()
        kinds = [kind for flag, kind in FLAGS.items() if self[flag]]
        only = (self.only_model, json.loads(self.only_ids)) if self.only_model else None
        package = self.env["nudge.recipe"]._export(kinds, files=self.include_files, only=only)
        self.write({
            "file": base64.b64encode(canonical(package).encode("utf-8")), "file_name": "nudge_recipe.json",
            "item_count": len(package["recipe"]["items"]), "state": "done"})
        return self._reopen()

    def _reopen(self):
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "target": "new", "name": _("Export a recipe")}


class NudgeRecipeImport(models.TransientModel):
    _name = "nudge.recipe.import"
    _description = "Import a Nudge Recipe"

    file = fields.Binary(string="Recipe File", attachment=False)
    file_name = fields.Char()
    state = fields.Selection([("choose", "Choose"), ("review", "Review"), ("done", "Done")], default="choose")
    summary = fields.Char(readonly=True)
    line_ids = fields.One2many("nudge.recipe.import.line", "wizard_id", string="Items")
    created_json = fields.Text(readonly=True, default="[]")
    rolled_back = fields.Boolean(readonly=True)
    # Set when the file comes from the marketplace: the import is then recorded as installed
    entry_id = fields.Many2one("nudge.recipe.entry", readonly=True, ondelete="set null")

    def _reopen(self):
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "target": "new", "name": _("Import a recipe")}

    def action_read(self):
        """Read the file, check it, and list what importing it would do."""
        self.ensure_one()
        Recipe = self.env["nudge.recipe"]
        Recipe._check_manager()
        if not self.file:
            raise UserError(_("Choose the recipe file first."))
        try:
            text = base64.b64decode(self.file).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise UserError(_("This file is not a Nudge recipe.")) from None
        body = Recipe._read(text)
        plan = Recipe._plan(body)
        self.line_ids.unlink()
        summary = _("From %(company)s, made with Nudge %(version)s on Odoo %(series)s.",
                             company=body.get("company") or "-", version=body.get("nudge_version") or "-",
                             series=body.get("odoo_series") or "-")
        if self.entry_id:
            summary = _("Marketplace: %(recipe)s, from %(site)s. ",
                                 recipe=self.entry_id.title, site=self.entry_id.source_id.name) + summary
        self.write({
            "state": "review",
            "summary": summary,
            "line_ids": [(0, 0, dict(line, selected=line["action"] in ("create", "update"))) for line in plan],
        })
        return self._reopen()

    def action_import(self):
        """Make or update what is ticked, each item on its own: one that fails does not stop the others."""
        self.ensure_one()
        Recipe = self.env["nudge.recipe"]
        Recipe._check_manager()
        created = []
        for line in self.line_ids.filtered(lambda l: l.selected and l.action in ("create", "update")):
            try:
                with self.env.cr.savepoint():
                    record, was_created = Recipe._apply(line.kind, line.key, json.loads(line.payload))
                    if was_created:
                        created.append([record._name, record.id])
                line.write({"result": "done", "message": ""})
            except Exception as error:  # noqa: BLE001 - reported on the line, the rest goes on
                line.write({"result": "failed", "message": getattr(error, "args", [str(error)])[0] or str(error)})
        for line in self.line_ids.filtered(lambda l: not l.result):
            line.result = "skipped"
        # Recorded when nothing failed and at least one item is now here, made or already the same
        if (self.entry_id and not self.line_ids.filtered(lambda l: l.result == "failed")
                and self.line_ids.filtered(lambda l: l.result == "done" or l.action == "same")):
            self.env["nudge.recipe.installed"]._record(self.entry_id)
        self.write({"state": "done", "created_json": json.dumps(created)})
        return self._reopen()

    def action_rollback(self):
        """Archive what this import made (nothing is deleted); what it only updated stays as it was written."""
        self.ensure_one()
        self.env["nudge.recipe"]._check_manager()
        for model, rid in json.loads(self.created_json or "[]"):
            record = self.env[model].with_context(active_test=False).browse(rid).exists()
            if record and "active" in record._fields:
                record.write({"active": False})
        self.rolled_back = True
        return self._reopen()


class NudgeRecipeImportLine(models.TransientModel):
    _name = "nudge.recipe.import.line"
    _description = "Item of a Nudge Recipe Import"

    wizard_id = fields.Many2one("nudge.recipe.import", required=True, ondelete="cascade")
    selected = fields.Boolean(string="Import")
    # Not a selection: a file may carry a kind this Nudge does not know, listed as skipped
    kind = fields.Char(required=True)
    kind_label = fields.Char(compute="_compute_kind_label")
    key = fields.Char(required=True)
    name = fields.Char()
    action = fields.Selection(
        [("create", "Will be created"), ("update", "Will be updated"), ("same", "Already the same"),
         ("skip", "Cannot be imported here")], required=True)
    note = fields.Text()
    payload = fields.Text()
    result = fields.Selection([("done", "Done"), ("failed", "Failed"), ("skipped", "Not imported")])
    message = fields.Char()

    @api.depends("kind")
    def _compute_kind_label(self):
        labels = dict(KINDS)
        for line in self:
            line.kind_label = labels.get(line.kind) or line.kind
