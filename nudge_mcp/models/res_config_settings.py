from odoo import api, fields, models

from .nudge_mcp import ENABLED_PARAM, EXPOSURE_PARAM, LEGACY_KEYS_END, LOCAL_PARAM, SCOPE, legacy_keys_ended
from .nudge_mcp_oauth import REGISTRATION_PARAM


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nudge_mcp_enabled = fields.Boolean(
        string="Open Nudge to External Assistants", config_parameter=ENABLED_PARAM,
        help="Claude Desktop, ChatGPT and other assistants that speak MCP can use the tools of "
             "Nudge, as the person who signed them in.")
    nudge_mcp_exposure = fields.Selection(
        [("read", "Look up and read only"), ("write", "Look up, read, and prepare changes"),
         ("develop", "Also read what developers build (administrators)")],
        string="What They May Use", config_parameter=EXPOSURE_PARAM, default="read",
        help="Reading: records, screens, the documentation, the company files. Changes also need "
             "the group Agent User for the person and the switch of the company below. Developers: "
             "automation rules, server actions and screen definitions, read only, for administrators "
             "with Developer mode; the tools that build or run code are never offered.")
    nudge_mcp_allow_writes = fields.Boolean(
        related="company_id.nudge_mcp_allow_writes", readonly=False)
    nudge_mcp_assistant_is_local = fields.Boolean(
        string="The assistants run on my own server", config_parameter=LOCAL_PARAM,
        help="With Private Mode on, assistants are refused unless they run on your own server or "
             "computer: what they read from Odoo would leave it otherwise.")
    nudge_mcp_oauth_registration = fields.Selection(
        [("open", "Assistants register themselves"), ("closed", "Only assistants an administrator registered")],
        string="Sign-in Registration", config_parameter=REGISTRATION_PARAM, default="open",
        help="Claude and most assistants register themselves the first time someone connects them "
             "(dynamic client registration). A registration alone opens nothing: each person signs in "
             "and allows the assistant.")

    nudge_mcp_legacy_keys = fields.Boolean(compute="_compute_nudge_mcp_legacy_keys")
    nudge_mcp_legacy_keys_end = fields.Date(compute="_compute_nudge_mcp_legacy_keys")

    @api.depends_context("uid")
    def _compute_nudge_mcp_legacy_keys(self):
        """Warns the administrator, until the keys of before OAuth end, while one of them is still alive"""
        Keys = self.env["res.users.apikeys"].sudo()
        domain = [("scope", "=", SCOPE)]
        if "expiration_date" in Keys._fields:
            domain += ["|", ("expiration_date", "=", False), ("expiration_date", ">", fields.Datetime.now())]
        alive = not legacy_keys_ended() and bool(Keys.search_count(domain, limit=1))
        for settings in self:
            settings.nudge_mcp_legacy_keys = alive
            settings.nudge_mcp_legacy_keys_end = LEGACY_KEYS_END

    def action_nudge_mcp_help(self):
        return self.env["nudge.mcp.help"].action_open()

    def action_nudge_mcp_self_confirm(self):
        return self.env["ir.actions.actions"]._for_xml_id("nudge_mcp.nudge_mcp_self_confirm_action")

    def action_nudge_mcp_grants(self):
        return self.env["ir.actions.actions"]._for_xml_id("nudge_mcp.nudge_mcp_grant_action")

    def action_nudge_mcp_clients(self):
        return self.env["ir.actions.actions"]._for_xml_id("nudge_mcp.nudge_mcp_client_action")
