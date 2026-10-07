import logging
import time

from odoo import _, api, models
from odoo.exceptions import UserError

from ..llm import base, transcription

_logger = logging.getLogger(__name__)

VOICE_PARAM = "nudge.voice"
VOICE_ENGINE_PARAM = "nudge.voice_engine"

# Who turns speech into text: the browser when it can and the AI provider
# otherwise, the browser only, or the AI provider only
ENGINE_AUTO = "auto"
ENGINE_BROWSER = "browser"
ENGINE_PROVIDER = "provider"
ENGINES = (ENGINE_AUTO, ENGINE_BROWSER, ENGINE_PROVIDER)

TRANSCRIPTION_SECONDS = 40


class NudgeVoice(models.AbstractModel):
    _name = "nudge.voice"
    _description = "Nudge Voice"

    @api.model
    def _is_allowed(self):
        # Stored as a word, as screen sharing is
        return self.env["ir.config_parameter"].sudo().get_param(VOICE_PARAM) != "no"

    @api.model
    def _get_engine(self):
        engine = self.env["ir.config_parameter"].sudo().get_param(VOICE_ENGINE_PARAM)
        return engine if engine in ENGINES else ENGINE_AUTO

    @api.model
    def _get_session_info(self):
        """:return: what the web client needs to know to listen"""
        allowed = self._is_allowed()
        engine = self._get_engine()
        listener = self.env["nudge.provider"].sudo()
        if allowed and engine != ENGINE_BROWSER:
            listener = listener._get_listener()
        return {
            "can_use_voice": allowed,
            "voice_engine": engine,
            "can_transcribe": bool(listener),
        }

    def _error_message(self, code):
        messages = {
            "empty": _("Nothing was recorded."),
            "too_long": _(
                "This recording is too long. The limit is %s seconds.", transcription.MAX_SECONDS),
            "not_wav": _("This recording cannot be read."),
            base.ERROR_UNSUPPORTED: _(
                "No AI provider is set up to turn speech into text."),
        }
        if code in messages:
            return messages[code]
        return self.env["nudge.conversation"]._chat_error_message(code)

    @api.model
    def _transcribe(self, audio, language="", hints=None):
        """Turn what the current user said into text.

        :param bytes audio: WAV file sent by the browser, untrusted
        :param language: code of the language they most likely spoke
        :param hints: words written on their screen, untrusted
        :return: what was said; empty when nothing was
        :raise UserError: when it cannot be done
        """
        if not self._is_allowed() or self._get_engine() == ENGINE_BROWSER:
            raise UserError(_("Your administrator turned this off."))
        try:
            seconds = transcription.wav_seconds(audio)
        except transcription.RecordingError as error:
            raise UserError(self._error_message(error.code)) from error
        # Infrastructure, as the provider of a conversation is: users have no
        # access to providers, and the key is for administrators
        provider = self.env["nudge.provider"].sudo()._get_listener()
        if not provider:
            raise UserError(self._error_message(base.ERROR_UNSUPPORTED))
        self.env["nudge.usage"]._check_budget()

        started = time.monotonic()
        limit = min(TRANSCRIPTION_SECONDS, self.env["nudge.conversation"]._get_stream_seconds())
        try:
            result = provider._get_adapter().transcribe(
                audio,
                language=transcription.clean_language(language),
                hints=transcription.clean_hints(hints),
                deadline=started + limit,
            )
        except base.AdapterError as error:
            _logger.info(
                "Nudge: speech of user %s not turned into text by %s: %s %s",
                self.env.uid, provider.name, error.code, error.detail)
            raise UserError(self._error_message(error.code)) from error
        usage = base.normalize_usage(result.get("usage"))
        model_name = result.get("model") or provider.stt_model
        _logger.info(
            "Nudge: %.1f s of speech of user %s turned into text by %s in %d ms "
            "(%s tokens in, %s out)",
            seconds, self.env.uid, model_name,
            (time.monotonic() - started) * 1000, usage["input_tokens"], usage["output_tokens"])
        self.env["nudge.usage"]._add(
            "speech", usage, provider._compute_cost(usage), self.env.user, self.env.company,
            provider_id=provider.id, model_name=model_name)
        return transcription.clean_text(result.get("text"))
