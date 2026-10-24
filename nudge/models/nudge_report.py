import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .. import tools
from ..tools import saved_reports
from ..tools.records import Refused

# What a person may change on a report of theirs: the rest is its definition
EDITABLE = {"name", "active", "home"}


class NudgeReport(models.Model):
    """A report a person asked Nudge to keep: the definition, the window action that opens it
    (Odoo checks the rights of whoever opens it, each time) and, if wanted,
    a tile on their home page. One menu, "My Reports", lists them: a menu per report cannot be
    kept per person, Odoo caches the visible menus by groups, not by user. Kept per person and per company; archived, never deleted."""

    _name = "nudge.report"
    _description = "Saved Report"
    _order = "name, id"

    name = fields.Char(required=True)
    user_id = fields.Many2one(
        "res.users", string="Owner", required=True, default=lambda self: self.env.user, readonly=True,
        index=True, ondelete="cascade")
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, readonly=True, index=True)
    active = fields.Boolean(default=True)
    home = fields.Boolean(string="On My Home Page")
    model_name = fields.Char(readonly=True, index=True)
    shape = fields.Selection(
        [("bar", "Bar chart"), ("line", "Line chart"), ("pie", "Pie chart"), ("pivot", "Pivot table"),
         ("list", "List")], readonly=True)
    definition = fields.Text(readonly=True)
    description = fields.Char(compute="_compute_description")
    action_id = fields.Many2one("ir.actions.act_window", readonly=True, ondelete="set null", copy=False)

    @api.depends("definition")
    @api.depends_context("lang")
    def _compute_description(self):
        for report in self:
            try:
                report.description = saved_reports.sentence(report.env, json.loads(report.definition or "{}"))
            except Exception:  # noqa: BLE001 - a report whose model went is still listed
                report.description = False

    # ------------------------------------------------------------------ making one
    @api.model
    def create_from_definition(self, definition, home=False):
        """Keep a report for the current person, from a definition checked here, as them.

        :return: the report"""
        try:
            definition, model = saved_reports.normalise(self.env, definition)
        except Refused as refusal:
            raise UserError(str(refusal)) from refusal
        definition["name"] = self._free_name(saved_reports.title_of(self.env, model, definition))
        # sudo: a plain user may not write the definition, the shape or the links; they were checked as them above
        report = self.sudo().create({
            "user_id": self.env.user.id, "company_id": self.env.company.id,
            "name": definition["name"],
            "model_name": model._name,
            "shape": definition["view"],
            "definition": json.dumps(definition),
            "home": bool(home),
        })
        report._publish(definition)
        tools.home.clear_cache(self.env)
        return report

    @api.model
    def _free_name(self, name):
        """:return: ``name``, or ``name (2)``... when the person keeps a report of that name already: two
        entries of one name in the menu cannot be told apart"""
        taken = set(self.search([("user_id", "=", self.env.uid), ("company_id", "=", self.env.company.id)]).mapped("name"))
        candidate, number = name, 1
        while candidate in taken:
            number += 1
            candidate = f"{name} ({number})"
        return candidate

    def _publish(self, definition):
        """The window action of the report. Written with elevated rights, since a
        person may not write actions: only after the definition was read as them, and the
        action holds nothing but a model, a domain and a context that Odoo checks at every opening."""
        self.ensure_one()
        values = saved_reports.action_values(self.env, definition)
        # sudo: the person has no right on actions and menus; what is written was checked as them above
        action = self.env["ir.actions.act_window"].sudo().create(dict(values, name=self.name))
        self.sudo().write({"action_id": action.id})  # sudo: the link is not the person's to edit

    # ------------------------------------------------------------------ keeping one
    @api.model_create_multi
    def create(self, vals_list):
        """What a person sends is a name and the home tile. The definition, the shape and the action
        come from :meth:`create_from_definition` only (it works with elevated rights): an action named by
        the caller would be renamed for everybody."""
        if not self.env.su:
            for vals in vals_list:
                if set(vals) - EDITABLE:
                    raise UserError(_(
                        "A report is kept by asking Nudge to save it; only its name and home tile can be given."))
                vals["user_id"] = self.env.user.id
                vals["company_id"] = self.env.company.id
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and set(vals) - EDITABLE:
            raise UserError(_("Only the name, the home page tile and the archive of a report can be changed."))
        result = super().write(vals)
        if "name" in vals:
            # sudo: the action of the report follows its name
            for report in self.sudo():
                report.action_id.write({"name": report.name})
        tools.home.clear_cache(self.env)
        return result

    @api.ondelete(at_uninstall=False)
    def _unlink_never(self):
        raise UserError(_("A saved report is archived, never deleted."))

    # ------------------------------------------------------------------ opening one
    def action_open_report(self):
        """:return: the window action of the report, as Odoo opens any action"""
        self.ensure_one()
        if not self.action_id:
            raise UserError(_("This report has no screen any more."))
        return self.action_id.sudo()._get_action_dict()

    def action_explain(self):
        """:return: the window that explains the report in sentences, read as the person who asks"""
        self.ensure_one()
        return self.env["nudge.report.explain"]._explain(self)._reopen()

    def action_toggle_home(self):
        for report in self:
            report.home = not report.home
        return True

    def action_spreadsheet(self):
        """:return: the action that opens a new spreadsheet of the figures, where the companion is installed"""
        self.ensure_one()
        definition = json.loads(self.definition)
        return saved_reports.spreadsheet_from(self.env, definition, self.name, self.numbers())

    def numbers(self):
        """:return: the figures of the report as the person reads them (see ``saved_reports.numbers``)"""
        self.ensure_one()
        definition = json.loads(self.definition)
        data = saved_reports.numbers(self.env, definition, self.action_id.sudo().domain)
        data["text"] = self._total_text(definition, data["total"])
        return data

    def _total_text(self, definition, total):
        model = self.env[definition["model"]]
        spec = definition["measures"][0]
        field = model._fields.get(spec.partition(":")[0]) if spec != "count" else None
        if field is not None and field.type == "monetary":
            return self.env.company.currency_id.format(total)
        value = float(total or 0)
        return f"{value:,.0f}" if value == int(value) else f"{value:,.2f}"
