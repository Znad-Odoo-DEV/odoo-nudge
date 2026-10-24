import re

from odoo import api, fields, models
from odoo.exceptions import UserError

METHOD = re.compile(r"^[a-z][a-z0-9_]*$")
# What a button with an email never runs: what changes records wholesale, or
# reaches past the rights of the person who presses it
FORBIDDEN_METHODS = {
    "unlink", "write", "create", "copy", "copy_data", "sudo", "with_user", "with_context", "with_env",
    "with_company", "browse", "search", "read", "export_data", "load", "toggle_active", "action_archive",
    "action_unarchive", "message_post", "message_subscribe", "invalidate_recordset", "flush_recordset",
}


def check_method(model, name):
    """:raise UserError: when ``name`` is not a public method of ``model``
    that a button may run"""
    if not name or not METHOD.match(name) or name in FORBIDDEN_METHODS:
        raise UserError(model.env._("%s is not a button this email may go with.", name))
    method = getattr(type(model), name, None)
    if not callable(method):
        raise UserError(model.env._("%(model)s has no method %(name)s.", model=model._name, name=name))


class NudgeEmailFlow(models.Model):
    """An email read and sent from a button, as Nudge built it.

    The button opens a window with the email ready, from its template, for
    the record on screen: the person reads it, changes it, and Send sends it
    and does what the button it goes with does (approve, confirm,
    validate). Closing the window does neither.
    """
    _name = "nudge.email.flow"
    _description = "Email with Review"
    _order = "name"

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    model_id = fields.Many2one("ir.model", string="Model", required=True, ondelete="cascade")
    model = fields.Char(related="model_id.model", string="Technical Model")
    template_id = fields.Many2one(
        "mail.template", string="Email Template", required=True, ondelete="restrict",
        domain="[('model_id', '=', model_id)]")
    method = fields.Char(
        string="Goes With",
        help="The method of the button the email goes with: Send runs it before the email leaves. "
             "Empty: the button sends the email only.")
    method_label = fields.Char(string="Button It Goes With")
    button_groups = fields.Char(
        string="Groups of the Button",
        help="The groups of the button it goes with, as the form has them: whoever is not in "
             "one of them cannot open the window.")
    required_field_ids = fields.Many2many(
        "ir.model.fields", "nudge_email_flow_required_field_rel", "flow_id", "field_id",
        string="Filled Before Send", domain="[('model_id', '=', model_id)]",
        help="Send is refused while one of these fields of the record is empty.")
    without_email = fields.Boolean(
        string="May Be Done Without the Email",
        help="The window offers to do what the button does without sending the email.")
    action_id = fields.Many2one("ir.actions.server", string="Server Action", ondelete="set null", readonly=True)
    view_id = fields.Many2one("ir.ui.view", string="Button on the Form", ondelete="set null", readonly=True)

    @api.constrains("method", "model_id")
    def _check_method(self):
        for flow in self.filtered("method"):
            check_method(self.env[flow.model], flow.method)

    def unlink(self):
        """The button and its action go with it: a button left behind would
        open nothing."""
        views = self.view_id
        actions = self.action_id
        result = super().unlink()
        views.exists().unlink()
        actions.exists().unlink()
        return result

    def write(self, values):
        result = super().write(values)
        if "active" in values:
            # The button follows: switched off, it leaves the form
            self.view_id.write({"active": values["active"]})
        return result

    def check_open_to(self, record):
        """:raise UserError: when the person may not send this email for ``record``"""
        self.ensure_one()
        if not self.active:
            raise UserError(self.env._("This email is switched off."))
        if len(record) != 1 or record._name != self.model:
            raise UserError(self.env._("Open one %s to send this email.", self.model_id.sudo().name))
        if self.button_groups and not self.env.user.has_groups(self.button_groups):
            raise UserError(self.env._("This button is not yours to press."))
        record.check_access("read")

    def open_review(self, records):
        """What the button runs: the window with the email, for the record."""
        self.ensure_one()
        self.check_open_to(records)
        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": "nudge.email.review",
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
            "context": {"default_flow_id": self.id, "default_res_id": records.id},
        }
