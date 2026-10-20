from datetime import timedelta

from odoo import fields, models
from psycopg2 import errors as pg_errors

from odoo.exceptions import AccessError, UserError

from odoo.addons.nudge.tools import base as tool_base

from .nudge_messaging import APPROVAL_HOURS, CHANNEL_OF, MODE_AGENT


class NudgeStep(models.Model):
    _inherit = "nudge.step"

    def _messaging_open_for_person(self):
        """:raise: unless this step is a change prepared from a phone for the person at the screen,
        and it still waits for them"""
        self.ensure_one()
        step = self.sudo()
        if step.user_id != self.env.user or step.origin not in CHANNEL_OF or step.state != "pending":
            raise AccessError(self.env._("This is not waiting for you."))
        if step.create_date < fields.Datetime.now() - timedelta(hours=APPROVAL_HOURS):
            raise UserError(self.env._("This has expired. Ask again from your phone."))
        return step

    def action_messaging_apply(self):
        """Yes, in Odoo: the change prepared from the phone is made now, as the person"""
        step = self._messaging_open_for_person()
        tool = tool_base.TOOLS.get(step.tool)
        if not isinstance(tool, tool_base.Action) or not tool.is_available(self.env, MODE_AGENT):
            raise UserError(self.env._("This can no longer be done. Nothing was changed."))
        try:
            self.env.cr.execute("SELECT id FROM nudge_step WHERE id = %s FOR UPDATE NOWAIT", (step.id,))
        except pg_errors.LockNotAvailable:
            raise UserError(self.env._("This is being done already.")) from None
        step.invalidate_recordset(["state"])
        if step.state != "pending":
            raise UserError(self.env._("This is not waiting for you."))
        prepared = step.run_id
        # In the environment of the person, never in sudo; only the log of the step is the system's
        user_env = self.env(context=dict(
            self.env.context, nudge_conversation_id=prepared.conversation_id.id,
            nudge_mode=MODE_AGENT, nudge_messaging=True))
        user_env = step._company_env(user_env)
        result = tool_base.guarded(user_env, step.tool, lambda: tool.execute(user_env, step))
        values = {
            "state": "failed" if result.is_error else "done",
            "result": self.env["nudge.mcp"]._preview(result.content),
            "error": result.content if result.is_error else False,
            "decided_at": fields.Datetime.now(),
        }
        if result.records:
            values["res_ids"] = list(result.records[1])[:50]
        if not result.is_error and result.card_lines is not None:
            values["preview"] = dict(step.preview or {}, lines=result.card_lines)
        step.write(values)
        prepared._end("failed" if result.is_error else "done")
        self._messaging_tell(step, result)
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "type": "danger" if result.is_error else "success",
            "title": self.env._("Failed") if result.is_error else self.env._("Done"),
            "message": result.content[:300], "next": {"type": "ir.actions.act_window_close"}}}

    def action_messaging_refuse(self):
        step = self._messaging_open_for_person()
        step.write({"state": "declined", "decided_at": fields.Datetime.now(),
                    "result": tool_base.NOTHING_MADE})
        step.run_id._end("stopped")
        return {"type": "ir.actions.act_window_close"}

    def _messaging_tell(self, step, result):
        """The phone it came from hears how it ended, in a line (best effort)"""
        channel = CHANNEL_OF.get(step.origin)
        link = self.env["nudge.messaging.link"].sudo().search(
            [("channel", "=", channel), ("user_id", "=", step.user_id.id), ("active", "=", True)], limit=1)
        if not link:
            return
        text = self.env._("Done: %s", result.content[:300]) if not result.is_error else self.env._(
            "It could not be done: %s", result.content[:300])
        self.env["nudge.messaging.api"]._send_text(channel, link.sender_id, text)
