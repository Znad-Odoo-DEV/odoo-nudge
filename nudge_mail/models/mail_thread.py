import re

from odoo import api, models

# What the receiving server wrote about the sender: only a pass counts
PASS = re.compile(r"\b(?:dkim|spf|dmarc)\s*=\s*pass\b", re.IGNORECASE)


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    @api.model
    def message_parse(self, message, save_original=False):
        """Notes, for ``nudge.mail.proposal.message_new``, whether the receiving server vouched for the sender
        (DKIM, SPF or DMARC passed). The From header alone is written by whoever sends the mail."""
        msg_dict = super().message_parse(message, save_original=save_original)
        try:
            lines = list(message.get_all("Authentication-Results") or []) + list(message.get_all("Received-SPF") or [])
        except Exception:  # noqa: BLE001 - an odd header must not lose the mail
            lines = []
        verified = any(PASS.search(str(line)) or str(line).strip().lower().startswith("pass") for line in lines)
        # Kept beside the transaction, not in msg_dict: message_post refuses keys it does not know
        self.env.cr.cache.setdefault("nudge_mail_auth", {})[msg_dict.get("message_id")] = verified
        return msg_dict
