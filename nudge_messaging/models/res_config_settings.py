from odoo import api, fields, models

from .nudge_messaging_api import PARAM


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nudge_msg_enabled = fields.Boolean(
        string="Messaging Assistants", config_parameter=PARAM + "enabled",
        help="People who linked their phone can ask Nudge on WhatsApp or Telegram. Changes are never "
             "made from the chat: Nudge answers with a link and the person says yes in Odoo.")
    nudge_msg_allow_private = fields.Boolean(
        string="Allow Messaging in Private Mode", config_parameter=PARAM + "allow_private",
        help="Private mode keeps company data from outside AI, but a chat answer travels through the servers of "
             "WhatsApp or Telegram. Off: in private mode the channel answers only that it is closed.")
    nudge_msg_retention_days = fields.Integer(
        string="Keep the Message Log (Days)", config_parameter="nudge_messaging.retention_days", default=30,
        help="Older lines of the log of received messages are deleted. The limit per sender and the check "
             "against a repeated message need only a few hours of it.")
    nudge_msg_whatsapp_phone_id = fields.Char(
        string="WhatsApp Phone Number ID", config_parameter=PARAM + "whatsapp.phone_id",
        help="The identifier of the business phone number in the WhatsApp Business Cloud API (Meta for Developers, WhatsApp, API Setup).")
    nudge_msg_whatsapp_verify_token = fields.Char(
        string="WhatsApp Verify Token", config_parameter=PARAM + "whatsapp.verify_token",
        help="Any word you choose. Give the same one to Meta when you register the webhook.")
    # The four below are written, never read back: the form shows only whether they are kept
    nudge_msg_whatsapp_app_secret = fields.Char(
        string="WhatsApp App Secret", copy=False,
        help="The secret of the Meta app. Every message is checked against it before anything is read. Stored, never shown again.")
    nudge_msg_whatsapp_access_token = fields.Char(
        string="WhatsApp Access Token", copy=False,
        help="The permanent token Nudge uses to answer on WhatsApp. Stored, never shown again.")
    nudge_msg_telegram_bot_token = fields.Char(
        string="Telegram Bot Token", copy=False,
        help="The token BotFather gave to your bot. Stored, never shown again.")
    nudge_msg_telegram_webhook_secret = fields.Char(
        string="Telegram Webhook Secret", copy=False,
        help="Letters, digits, underscore and hyphen only. Telegram repeats it with every message.")
    nudge_msg_whatsapp_keys_set = fields.Boolean(compute="_compute_nudge_msg_keys")
    nudge_msg_telegram_keys_set = fields.Boolean(compute="_compute_nudge_msg_keys")
    nudge_msg_forget_keys = fields.Boolean(
        string="Forget the Stored Keys", copy=False,
        help="Remove the keys of both channels when you save.")
    nudge_msg_whatsapp_url = fields.Char(
        string="WhatsApp Callback Address", compute="_compute_nudge_msg_urls",
        help="Give this address to Meta as the callback URL of the webhook.")
    nudge_msg_telegram_url = fields.Char(
        string="Telegram Webhook Address", compute="_compute_nudge_msg_urls",
        help="The address Telegram sends messages to; the button next to it registers it.")

    @api.depends_context("uid")
    def _compute_nudge_msg_keys(self):
        Api = self.env["nudge.messaging.api"]
        whatsapp = bool(Api._param("whatsapp.app_secret") and Api._param("whatsapp.access_token"))
        telegram = bool(Api._param("telegram.bot_token") and Api._param("telegram.webhook_secret"))
        for settings in self:
            settings.nudge_msg_whatsapp_keys_set = whatsapp
            settings.nudge_msg_telegram_keys_set = telegram

    def _compute_nudge_msg_urls(self):
        base = (self.env["ir.config_parameter"].sudo().get_param("web.base.url") or "").rstrip("/")
        for settings in self:
            settings.nudge_msg_whatsapp_url = f"{base}/nudge_messaging/whatsapp"
            settings.nudge_msg_telegram_url = f"{base}/nudge_messaging/telegram"

    def set_values(self):
        super().set_values()
        if not self.env.user.has_group("base.group_system"):
            return
        Parameter = self.env["ir.config_parameter"].sudo()
        if self.nudge_msg_forget_keys:
            for key in ("whatsapp.app_secret", "whatsapp.access_token", "telegram.bot_token",
                        "telegram.webhook_secret"):
                Parameter.set_param(PARAM + key, False)
            return
        # Blank means "keep what is stored": the form never shows a key back
        for field, key in (
                ("nudge_msg_whatsapp_app_secret", "whatsapp.app_secret"),
                ("nudge_msg_whatsapp_access_token", "whatsapp.access_token"),
                ("nudge_msg_telegram_bot_token", "telegram.bot_token"),
                ("nudge_msg_telegram_webhook_secret", "telegram.webhook_secret")):
            value = (self[field] or "").strip()
            if value:
                Parameter.set_param(PARAM + key, value)

    def action_nudge_msg_telegram_register(self):
        """Tell Telegram where to send the messages of the bot"""
        self.ensure_one()
        done = self.env["nudge.messaging.api"].sudo()._telegram_set_webhook(self.nudge_msg_telegram_url)
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "type": "success" if done else "warning",
            "title": self.env._("Telegram"),
            "message": self.env._("Telegram will send the messages here.") if done else self.env._(
                "Telegram did not accept it. Save the bot token and the webhook secret first, "
                "and the address must be reachable from the internet over https."),
            "sticky": False}}
