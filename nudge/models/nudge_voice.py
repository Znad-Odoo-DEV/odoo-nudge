import logging
import time

from odoo import api, models
from odoo.exceptions import UserError

from ..llm import base, transcription
from ..tools.deadline import until

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
# A meeting is long: the provider is given minutes, not seconds
MEETING_TRANSCRIPTION_SECONDS = 300
MEETING_FORMATS = ("wav", "mp3", "m4a", "ogg", "webm", "aac", "opus", "oga")
MEETING_TEXT_CHARS = 120_000


class NudgeVoice(models.AbstractModel):
    _name = "nudge.voice"
    _description = "Nudge Voice"

    @api.model
    def _is_allowed(self):
        # Stored as a word, as screen sharing is
        return self.env["ir.config_parameter"].sudo().get_param(VOICE_PARAM) != "no"

    @api.model
    def _browser_speech_in_private(self):
        """:return: whether the administrator allowed the speech of the browser in private mode"""
        return bool(self.env.company.sudo().nudge_private_browser_speech)

    @api.model
    def _get_engine(self):
        Provider = self.env["nudge.provider"]
        if Provider._is_private_mode():
            # The browser may send the sound to its maker: in private mode a provider of ours listens, and
            # the browser only where the administrator allowed it and no provider of ours does
            if self._browser_speech_in_private() and not Provider.sudo()._get_listener():
                return ENGINE_BROWSER
            return ENGINE_PROVIDER
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
        private = self.env["nudge.provider"]._is_private_mode()
        browser_ok = not private or self._browser_speech_in_private()
        reason = ""
        if allowed and private and not listener and not browser_ok:
            reason = self.env._(
                "Private mode is on and no AI provider on your own server listens. An administrator can allow "
                "the speech of the browser in Settings, or add a provider on your own server.")
        return {
            "can_use_voice": allowed,
            "voice_engine": engine,
            "can_transcribe": bool(listener),
            "voice_private": private,
            "voice_blocked_reason": reason,
            # Whether the voices of the browser may read an answer: always, except in private mode unless allowed
            "browser_speech_ok": browser_ok,
        }

    def _error_message(self, code):
        messages = {
            "empty": self.env._("Nothing was recorded."),
            "too_long": self.env._(
                "This recording is too long. The limit is %s seconds.", transcription.MAX_SECONDS),
            "not_wav": self.env._("This recording cannot be read."),
            base.ERROR_UNSUPPORTED: self.env._(
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
            raise UserError(self.env._("Your administrator turned this off."))
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
                deadline=until(limit),
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

    @api.model
    def _can_transcribe_meeting(self):
        """:return: the provider that turns a recording of a meeting into text, possibly empty.
        In private mode only a provider on our own server qualifies."""
        if not self._is_allowed():
            return self.env["nudge.provider"]
        return self.env["nudge.provider"].sudo()._get_listener()

    @api.model
    def _transcribe_meeting(self, audio, audio_format="wav", language=""):
        """Turn a recording of a meeting, of any length the file cap allows, into text.

        :param bytes audio: the file, already within the cap of an attachment
        :param audio_format: its extension: wav, mp3, m4a, ogg or webm
        :return: what was said; empty when nothing was
        :raise UserError: when it cannot be done, with the reason for the person"""
        provider = self._can_transcribe_meeting()
        if not provider:
            raise UserError(self._error_message(base.ERROR_UNSUPPORTED))
        if not audio:
            raise UserError(self._error_message("empty"))
        self.env["nudge.usage"]._check_budget()
        started = time.monotonic()
        try:
            result = provider._get_adapter().transcribe(
                audio,
                language=transcription.clean_language(language),
                deadline=until(MEETING_TRANSCRIPTION_SECONDS),
                audio_format=audio_format if audio_format in MEETING_FORMATS else "wav",
            )
        except base.AdapterError as error:
            _logger.info(
                "Nudge: meeting of user %s not turned into text by %s: %s %s",
                self.env.uid, provider.name, error.code, error.detail)
            raise UserError(self._error_message(error.code)) from error
        usage = base.normalize_usage(result.get("usage"))
        model_name = result.get("model") or provider.stt_model
        _logger.info(
            "Nudge: a meeting of %d bytes of user %s turned into text by %s in %d ms",
            len(audio), self.env.uid, model_name, (time.monotonic() - started) * 1000)
        self.env["nudge.usage"]._add(
            "speech", usage, provider._compute_cost(usage), self.env.user, self.env.company,
            provider_id=provider.id, model_name=model_name)
        return " ".join(str(result.get("text") or "").split())[:MEETING_TEXT_CHARS]
