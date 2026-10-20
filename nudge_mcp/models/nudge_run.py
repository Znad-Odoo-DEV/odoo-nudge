from datetime import timedelta

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError


class NudgeRun(models.Model):
    _inherit = "nudge.run"

    origin = fields.Char(
        string="Origin", index="btree_not_null", readonly=True,
        help="Where the request came from when it was not the chat of Nudge: MCP and the name "
             "of the assistant.")


class NudgeStep(models.Model):
    _inherit = "nudge.step"

    state = fields.Selection(
        selection_add=[("approved", "Approved in Odoo")], ondelete={"approved": "set default"})
    origin = fields.Char(related="run_id.origin", store=True, string="Origin")

    def _mcp_open_for_person(self):
        """:raise: unless this step is a change an external assistant prepared
        for the person at the screen, and it still waits for them"""
        self.ensure_one()
        step = self.sudo()
        if step.user_id != self.env.user or not step.origin or step.state != "pending":
            raise AccessError(_("This is not waiting for you."))
        limit = fields.Datetime.now() - timedelta(minutes=30)
        if step.create_date < limit:
            raise UserError(_("This has expired. Ask the assistant to prepare it again."))
        return step

    def action_mcp_approve(self):
        """Yes, in Odoo: only now can the assistant confirm this change."""
        step = self._mcp_open_for_person()
        step.write({"state": "approved", "decided_at": fields.Datetime.now()})
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "type": "success", "title": _("Approved"),
            "message": _("The assistant can make the change now."), "next": {
                "type": "ir.actions.act_window_close"}}}

    def action_mcp_refuse(self):
        step = self._mcp_open_for_person()
        step.write({"state": "declined", "decided_at": fields.Datetime.now()})
        step.run_id._end("stopped")
        return {"type": "ir.actions.act_window_close"}
