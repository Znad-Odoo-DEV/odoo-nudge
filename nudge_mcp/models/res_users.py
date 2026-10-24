from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    nudge_mcp_grant_ids = fields.One2many(
        "nudge.mcp.grant", "user_id", string="Connected Assistants",
        help="The assistants you allowed to use Nudge as you, signed in with OAuth.")

    @property
    def SELF_READABLE_FIELDS(self):
        # The person sees their own connected assistants in their preferences
        return super().SELF_READABLE_FIELDS + ["nudge_mcp_grant_ids"]
