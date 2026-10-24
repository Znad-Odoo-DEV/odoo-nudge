import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.nudge.tools.actions import is_sensitive

METHOD_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,79}")


class NudgeMcpSelfConfirm(models.Model):
    """One change an assistant may confirm without the person's yes in Odoo.

    Off by default: with no line, every change waits for the person. A line
    names a tool on one model (and the method, for a button) in one company,
    for any assistant signed in with OAuth or for one of them. Keys of before
    never confirm alone, and a sensitive change (posting, paying, confirming,
    users and rights, accounting, stock, ``ir.*``) is refused here and again
    when the change is confirmed."""
    _name = "nudge.mcp.self.confirm"
    _description = "Change an Assistant May Confirm Alone"
    _order = "company_id, model, tool, id"

    company_id = fields.Many2one(
        "res.company", string="Company", required=True, index=True, default=lambda self: self.env.company)
    tool = fields.Selection(
        [("prepare_create", "Create"), ("prepare_write", "Change"), ("call_method", "Press a button")],
        string="Change", required=True)
    model_id = fields.Many2one(
        "ir.model", string="Model", required=True, ondelete="cascade",
        domain=[("transient", "=", False)])
    model = fields.Char(related="model_id.model", store=True, index=True, string="Technical Name")
    method = fields.Char(
        string="Button", help="For a button: the name of its method (action_..., button_...). Required then.")
    client_id = fields.Many2one(
        "nudge.mcp.client", string="Assistant", ondelete="cascade",
        help="Empty: any assistant signed in with OAuth. Keys of before (legacy) never confirm alone.")
    note = fields.Char(string="Why")
    active = fields.Boolean(default=True)

    @api.constrains("tool", "model_id", "method")
    def _check_not_sensitive(self):
        for line in self:
            if line.model and is_sensitive(line.model):
                raise ValidationError(self.env._(
                    "Changes on %s always wait for the person: they move money or goods, or open rights.",
                    line.model))
            if line.tool == "call_method":
                if not line.method or not METHOD_PATTERN.fullmatch(line.method):
                    raise ValidationError(self.env._("Give the method of the button, as action_done."))
                if is_sensitive(line.model, line.method):
                    raise ValidationError(self.env._(
                        "The button %s cannot be taken back with a click: it always waits for the person.",
                        line.method))
            elif line.method:
                raise ValidationError(self.env._("Only a button has a method."))

    @api.model
    def _allows(self, step, client_id):
        """:return: whether a line of the step's company lets this client
        confirm it alone. Read in sudo: it is the administrator's choice,
        consulted, never shown to the assistant"""
        if step.is_sensitive or is_sensitive(step.res_model or "", step.method or None):
            return False
        domain = [
            ("company_id", "=", (step.sudo().company_id or self.env.company).id),
            ("tool", "=", step.tool),
            ("model", "=", step.res_model),
            "|", ("client_id", "=", False), ("client_id", "=", client_id),
        ]
        if step.tool == "call_method":
            domain.append(("method", "=", step.method))
        return bool(self.sudo().search_count(domain, limit=1))
