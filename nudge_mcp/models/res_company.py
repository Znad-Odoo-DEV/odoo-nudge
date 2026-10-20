from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    # Not required: the column is added to companies that exist
    nudge_mcp_allow_writes = fields.Boolean(
        string="Allow Changes from External Assistants", copy=False,
        help="Let the assistants that connect through MCP prepare changes in this company. "
             "Each change waits for the person to approve it in Odoo (Nudge, Assistant Approvals): "
             "the assistant cannot approve it for them.")
    nudge_mcp_self_confirm = fields.Boolean(
        string="External assistants may confirm their own changes (unsafe)", copy=False,
        help="Unsafe. The assistant confirms what it prepared without the person's yes in Odoo, so "
             "anything it was talked into by a text it read becomes a change. Changes that cannot "
             "be taken back (post, validate, pay, confirm orders, users and rights) always wait "
             "for the person, whatever this says.")
