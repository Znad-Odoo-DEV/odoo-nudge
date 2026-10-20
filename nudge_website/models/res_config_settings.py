from odoo import _, fields, models

from .nudge_web_message import RATE_MESSAGES_PARAM, RATE_MINUTES_PARAM


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nudge_assistant = fields.Boolean(related="website_id.nudge_assistant", readonly=False)
    nudge_greeting = fields.Char(related="website_id.nudge_greeting", readonly=False)
    nudge_lang_ids = fields.Many2many(related="website_id.nudge_lang_ids", readonly=False)
    nudge_public_budget = fields.Integer(related="company_id.nudge_public_budget", readonly=False)
    nudge_web_keep_days = fields.Integer(related="company_id.nudge_web_keep_days", readonly=False)
    nudge_web_max_ai = fields.Integer(
        string="Answers of the AI at Once", config_parameter="nudge_website.max_ai", default=0,
        help="How many visitors may be answered by the AI at the same moment. 0: the workers of the "
             "server less three, at least one. Past it, visitors get the products and files alone.")
    nudge_web_handover_max = fields.Integer(
        string="Requests for a Person per Hour", config_parameter="nudge_website.handover_max", default=3)
    nudge_rate_messages = fields.Integer(
        string="Messages per Visitor", config_parameter=RATE_MESSAGES_PARAM, default=20,
        help="How many messages one address may send in the time below.")
    nudge_rate_minutes = fields.Integer(
        string="Minutes", config_parameter=RATE_MINUTES_PARAM, default=10)

    def action_nudge_read_pages(self):
        """Read the published pages of the websites with the assistant now."""
        count = self.env["nudge.source"]._sync_website_pages()
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {
                "title": _("Website assistant"), "type": "success", "sticky": False,
                "message": _("%s pages are known to the assistant.", count)},
        }
