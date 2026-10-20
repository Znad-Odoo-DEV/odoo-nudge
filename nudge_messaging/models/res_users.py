from odoo import _, fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    nudge_msg_link_ids = fields.One2many(
        "nudge.messaging.link", "user_id", string="Linked Phones", readonly=True)

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ["nudge_msg_link_ids"]

    def action_nudge_msg_code(self):
        """The person asks for a code to send from their phone"""
        self.ensure_one()
        if self != self.env.user or not self.env.user.has_group("nudge.group_nudge_user"):
            return False
        if not self.env["nudge.messaging.api"]._is_enabled():
            message = _("Messaging is closed. An administrator turns it on in Settings, Nudge.")
            kind = "warning"
        else:
            code = self.env["nudge.messaging.code"]._make(self)
            message = _(
                "Your code: %(code)s. Send it as a message from your phone to the Nudge number on WhatsApp "
                "or to the Nudge bot on Telegram within 10 minutes.", code=code)
            kind = "success"
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "type": kind, "title": _("Nudge Messaging"), "message": message, "sticky": True}}
