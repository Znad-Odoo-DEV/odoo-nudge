import re

from odoo import models

from .nudge_mention import text_of

# "@Nudge" typed in the message: a channel keeps only the mentions of its members
NAMED = re.compile(r"(?<![\w@])@nudge\b", re.IGNORECASE)


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _message_post_after_hook(self, message, msg_values):
        result = super()._message_post_after_hook(message, msg_values)
        nudge = self.env.ref("nudge_discuss.partner_nudge", raise_if_not_found=False)
        if (nudge and len(self) == 1 and message.message_type == "comment" and message.author_id != nudge
                and (nudge in message.partner_ids or NAMED.search(text_of(message.body)))):
            # The author is whatever the poster says: it is believed only from code that runs in
            # sudo (the portal, the mail gateway); anyone else is answered as themselves
            user = self.env.user
            if self.env.su and message.author_id != user.partner_id:
                user = message.author_id.user_ids[:1]
            # Answered in the background: posting is not kept waiting
            self.env["nudge.mention"].sudo()._queue(message, self, user)
        return result
