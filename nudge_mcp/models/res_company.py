from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    # Not required: the column is added to companies that exist
    nudge_mcp_allow_writes = fields.Boolean(
        string="Allow Changes from External Assistants", copy=False,
        help="Let the assistants that connect through MCP prepare changes in this company. "
             "Each change waits for the person to approve it in Odoo (Nudge, Assistant Approvals), "
             "unless the allow-list of changes they may confirm alone names it.")
