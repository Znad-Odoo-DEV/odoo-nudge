"""A Nudge provider may take the key typed in the AI app: typed once."""
import dataclasses

from odoo import api, fields, models

# Where the AI app keeps its keys
AI_APP_KEYS = {
    "ai_google": "ai.google_key",
    "ai_openai": "ai.openai_key",
}


class NudgeProvider(models.Model):
    _inherit = "nudge.provider"

    key_source = fields.Selection(
        [
            ("own", "Typed here"),
            ("ai_google", "The Google key of the AI app"),
            ("ai_openai", "The OpenAI key of the AI app"),
        ],
        string="API Key From", required=True, default="own",
        help="The AI app keeps a key per provider in AI ‣ Configuration ‣ Settings. "
             "A Nudge provider can use it rather than a key of its own.",
    )

    def _ai_app_key(self):
        self.ensure_one()
        parameter = AI_APP_KEYS.get(self.key_source)
        if not parameter:
            return ""
        return self.env["ir.config_parameter"].sudo().get_param(parameter) or ""

    def _has_credentials(self):
        self.ensure_one()
        if self.key_source != "own":
            return bool(self._ai_app_key())
        return super()._has_credentials()

    def _get_config(self):
        config = super()._get_config()
        if self.key_source == "own":
            return config
        key = self._ai_app_key()
        if dataclasses.is_dataclass(config):
            return dataclasses.replace(config, api_key=key)
        return config._replace(api_key=key)

    @api.depends("key_source")
    def _compute_is_configured(self):
        super()._compute_is_configured()
