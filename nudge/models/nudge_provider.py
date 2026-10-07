import logging
import os

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..llm import base

_logger = logging.getLogger(__name__)

DEFAULT_PROVIDER_PARAM = "nudge.default_provider_id"


class NudgeProvider(models.Model):
    _name = "nudge.provider"
    _description = "Nudge AI Provider"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    kind = fields.Selection(
        [
            ("openai_compatible", "OpenAI-compatible API"),
            ("anthropic", "Anthropic Claude"),
        ],
        string="Protocol", required=True, default="openai_compatible",
        help="OpenAI-compatible covers Google Gemini, Groq, OpenRouter, Ollama and OpenAI.",
    )
    base_url = fields.Char(
        string="Base URL",
        help="Address of the API, without the final /chat/completions. "
             "Leave empty for Anthropic to use the official endpoint.",
    )
    api_key = fields.Char(string="API Key", groups="base.group_system", copy=False)
    has_api_key = fields.Boolean(
        string="API Key Set", compute="_compute_has_api_key", compute_sudo=True)
    requires_api_key = fields.Boolean(
        string="Requires an API Key", default=True,
        help="Untick for a local server such as Ollama that accepts requests without a key.",
    )
    model_name = fields.Char(string="Model", help="Model identifier sent to the provider.")
    model_ids = fields.One2many("nudge.provider.model", "provider_id", string="Available Models")
    capabilities = fields.Json(copy=False)
    max_output_tokens = fields.Integer(
        string="Maximum Output Tokens",
        help="Upper limit of tokens generated per answer. 0 lets the provider decide.",
    )
    effort = fields.Selection(
        [
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
            ("xhigh", "Extra high"),
            ("max", "Maximum"),
        ],
        help="How much reasoning the model spends on an answer. Lower is faster and cheaper. "
             "Only sent to models that support it.",
    )
    refusal_fallback = fields.Boolean(
        string="Fallback on Refusal", default=True,
        help="When the model declines a request for safety reasons, let the provider retry it "
             "on its recommended fallback model.",
    )
    timeout = fields.Integer(string="Timeout (seconds)", default=60)
    stt_mode = fields.Selection(
        [
            (base.STT_NONE, "Not available"),
            (base.STT_CHAT, "A model that listens"),
            (base.STT_ENDPOINT, "Transcription service"),
        ],
        string="Speech to Text", required=True, default=base.STT_NONE,
        help="How this provider turns what people say into text, for the browsers that cannot "
             "do it themselves.\n"
             "A model that listens: the recording is given to a chat model, as Google Gemini "
             "accepts.\n"
             "Transcription service: the recording is sent to /audio/transcriptions, as OpenAI "
             "and Groq offer.",
    )
    stt_model = fields.Char(
        string="Speech Model",
        help="Model that turns speech into text. For a model that listens, empty uses the "
             "model of the conversations.",
    )
    can_transcribe = fields.Boolean(
        string="Turns Speech into Text", compute="_compute_can_transcribe", compute_sudo=True)
    price_input = fields.Float(string="Input Price", digits=(12, 4), help="Per million tokens.")
    price_output = fields.Float(string="Output Price", digits=(12, 4), help="Per million tokens.")
    price_cache_read = fields.Float(
        string="Cache Read Price", digits=(12, 4), help="Per million tokens.")
    price_cache_write = fields.Float(
        string="Cache Write Price", digits=(12, 4), help="Per million tokens.")
    notes = fields.Text(translate=True)
    is_configured = fields.Boolean(
        string="Ready", compute="_compute_is_configured", compute_sudo=True)

    _check_timeout = models.Constraint(
        "CHECK(timeout > 0)", "The timeout must be strictly positive.")
    _check_max_output_tokens = models.Constraint(
        "CHECK(max_output_tokens >= 0)", "The maximum of output tokens cannot be negative.")

    @api.depends("api_key")
    def _compute_has_api_key(self):
        for provider in self:
            provider.has_api_key = bool(provider.api_key)

    @api.depends("api_key", "requires_api_key", "model_name", "kind", "base_url")
    def _compute_is_configured(self):
        for provider in self:
            provider.is_configured = bool(
                provider.model_name
                and (provider.kind == "anthropic" or provider.base_url)
                and provider._has_credentials()
            )

    @api.depends("is_configured", "kind", "stt_mode", "stt_model")
    def _compute_can_transcribe(self):
        for provider in self:
            provider.can_transcribe = bool(
                provider.is_configured
                and provider.kind == "openai_compatible"
                and (
                    provider.stt_mode == base.STT_CHAT
                    or (provider.stt_mode == base.STT_ENDPOINT and provider.stt_model)
                )
            )

    def _has_credentials(self):
        self.ensure_one()
        if self.api_key or not self.requires_api_key:
            return True
        # The Anthropic SDK also reads its key from the environment
        return self.kind == "anthropic" and bool(os.environ.get("ANTHROPIC_API_KEY"))

    @api.onchange("kind")
    def _onchange_kind(self):
        if self.kind == "anthropic":
            self.requires_api_key = True

    def write(self, vals):
        if "capabilities" not in vals and {"model_name", "kind", "base_url"} & vals.keys():
            # Capabilities belong to one model of one provider
            vals = dict(vals, capabilities=False)
        return super().write(vals)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------
    @api.model
    def _get_default(self):
        """:return: the provider answering new conversations, possibly empty.
        Must be called in sudo: it is infrastructure, users have no access to providers."""
        param = self.env["ir.config_parameter"].sudo().get_param(DEFAULT_PROVIDER_PARAM)
        if param and param.isdigit():
            provider = self.browse(int(param)).exists()
            if provider.active and provider.is_configured:
                return provider
        return self.search([]).filtered("is_configured")[:1]

    @api.model
    def _get_listener(self):
        """:return: the provider turning speech into text, possibly empty: the
        one of the conversations when it can, otherwise the first that can.
        Must be called in sudo, as :meth:`_get_default`."""
        default = self._get_default()
        if default.can_transcribe:
            return default
        return self.search([]).filtered("can_transcribe")[:1]

    def _get_config(self):
        self.ensure_one()
        return base.ProviderConfig(
            kind=self.kind,
            model=self.model_name or "",
            api_key=self.api_key or "",
            base_url=(self.base_url or "").strip(),
            max_output_tokens=self.max_output_tokens,
            effort=self.effort or "",
            refusal_fallback=self.refusal_fallback,
            timeout=self.timeout,
            capabilities=self.capabilities or {},
            stt_mode=self.stt_mode or base.STT_NONE,
            stt_model=(self.stt_model or "").strip(),
        )

    def _get_adapter(self):
        self.ensure_one()
        return base.get_adapter(self._get_config())

    def _compute_cost(self, usage):
        """:param dict usage: normalized token counts
        :return: price of one answer, in the currency of the provider's price list"""
        self.ensure_one()
        return (
            usage["input_tokens"] * self.price_input
            + usage["output_tokens"] * self.price_output
            + usage["cache_read_tokens"] * self.price_cache_read
            + usage["cache_write_tokens"] * self.price_cache_write
        ) / 1_000_000

    def _ensure_capabilities(self):
        """Ask the provider once what the configured model supports."""
        self.ensure_one()
        if self.kind != "anthropic" or self.capabilities or not self.model_name:
            return
        try:
            self.capabilities = self._get_adapter().fetch_capabilities()
        except base.AdapterError as exc:
            _logger.info("Nudge: capabilities of %s not available: %s", self.model_name, exc)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _notify(self, message, notification_type="success"):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": notification_type,
                "message": message,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def _error_message(self, error):
        messages = {
            base.ERROR_AUTH: self.env._("The provider rejected the API key."),
            base.ERROR_MODEL_NOT_FOUND: self.env._("The provider does not know this model."),
            base.ERROR_RATE_LIMIT: self.env._("The provider's rate limit is reached. Try again in a minute."),
            base.ERROR_NETWORK: self.env._("The provider could not be reached."),
            base.ERROR_UNAVAILABLE: self.env._("The provider is temporarily unavailable."),
            base.ERROR_MISSING_LIBRARY: self.env._("A Python library is missing on the server."),
            base.ERROR_UNSUPPORTED: self.env._("The provider cannot do this."),
        }
        message = messages.get(error.code, self.env._("The provider returned an error."))
        return f"{message}\n{error.detail}" if error.detail else message

    def _check_ready(self):
        self.ensure_one()
        if not self.model_name:
            raise UserError(self.env._("Set a model first."))
        if not self._has_credentials():
            raise UserError(self.env._("Set an API key first."))

    def action_test_connection(self):
        self._check_ready()
        try:
            self._get_adapter().check()
        except base.AdapterError as exc:
            raise UserError(self._error_message(exc)) from exc
        self._ensure_capabilities()
        return self._notify(self.env._("Connection successful."))

    def action_fetch_models(self):
        self.ensure_one()
        if not self._has_credentials():
            raise UserError(self.env._("Set an API key first."))
        try:
            names = self._get_adapter().list_models()
        except base.AdapterError as exc:
            raise UserError(self._error_message(exc)) from exc
        known = self.model_ids.mapped("name")
        self.model_ids.filtered(lambda line: line.name not in names).unlink()
        self.env["nudge.provider.model"].create([
            {"provider_id": self.id, "name": name} for name in names if name not in known
        ])
        return self._notify(self.env._("%s models found.", len(names)))


class NudgeProviderModel(models.Model):
    _name = "nudge.provider.model"
    _description = "Nudge AI Provider Model"
    _order = "name"

    provider_id = fields.Many2one("nudge.provider", required=True, ondelete="cascade", index=True)
    name = fields.Char(required=True)
    is_selected = fields.Boolean(string="In Use", compute="_compute_is_selected")

    @api.depends("name", "provider_id.model_name")
    def _compute_is_selected(self):
        for line in self:
            line.is_selected = line.name == line.provider_id.model_name

    def action_select(self):
        self.ensure_one()
        self.provider_id.model_name = self.name
