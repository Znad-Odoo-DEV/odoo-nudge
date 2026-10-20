from odoo import fields, models

from .nudge_mcp import ENABLED_PARAM, EXPOSURE_PARAM, LOCAL_PARAM


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nudge_mcp_enabled = fields.Boolean(
        string="Open Nudge to External Assistants", config_parameter=ENABLED_PARAM,
        help="Claude Desktop, ChatGPT and other assistants that speak MCP can use the tools of "
             "Nudge, as the person whose key they hold.")
    nudge_mcp_exposure = fields.Selection(
        [("read", "Look up and read only"), ("write", "Look up, read, and prepare changes")],
        string="What They May Use", config_parameter=EXPOSURE_PARAM, default="read",
        help="Reading: records, screens, the documentation, the company files. Changes also need "
             "the group Agent User for the person and the switch of the company below. The "
             "tools for developers are never offered.")
    nudge_mcp_allow_writes = fields.Boolean(
        related="company_id.nudge_mcp_allow_writes", readonly=False)
    nudge_mcp_self_confirm = fields.Boolean(
        related="company_id.nudge_mcp_self_confirm", readonly=False)
    nudge_mcp_assistant_is_local = fields.Boolean(
        string="The assistants run on my own server", config_parameter=LOCAL_PARAM,
        help="With Private Mode on, assistants are refused unless they run on your own server or "
             "computer: what they read from Odoo would leave it otherwise.")

    def action_nudge_mcp_help(self):
        return self.env["nudge.mcp.help"].action_open()
