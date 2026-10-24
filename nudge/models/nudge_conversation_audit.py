"""NP-16: what an auditor asks is kept for the company. The conversation of an auditor is marked when it is
made; the retention cron leaves it, and nobody deletes it, the auditor included."""
from odoo import api, fields, models
from odoo.exceptions import AccessError

from .res_users_auditor import AUDITOR_GROUP


class NudgeConversation(models.Model):
    _inherit = "nudge.conversation"

    kept_for_audit = fields.Boolean(
        string="Kept for Audit", readonly=True, copy=False, index=True,
        help="Asked by an auditor: kept for the company, never deleted by the retention of old conversations.")

    @api.model_create_multi
    def create(self, vals_list):
        conversations = super().create(vals_list)
        # Bookkeeping of Nudge: the person cannot choose it (the create above only takes the title)
        audited = conversations.sudo().filtered(lambda conversation: conversation.user_id.has_group(AUDITOR_GROUP))
        audited.write({"kept_for_audit": True})
        return conversations

    def write(self, vals):
        if "kept_for_audit" in vals and not self.env.su:
            raise AccessError(self.env._("What an auditor asked is kept: this cannot be changed."))
        if not self.env.su and ("active" in vals or "name" in vals):
            for conversation in self.filtered("kept_for_audit"):
                # the title is made once, from the first question: after that it is part of what is kept
                if ("active" in vals and not vals["active"]) or ("name" in vals and conversation.name and
                                                                  vals["name"] != conversation.name):
                    raise AccessError(self.env._("What an auditor asked is kept: it cannot be archived or renamed."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and self.filtered("kept_for_audit"):
            raise AccessError(self.env._("What an auditor asked is kept for the company and cannot be deleted."))
        return super().unlink()
