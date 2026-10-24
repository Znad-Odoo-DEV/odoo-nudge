"""The two HTTP APIs, with plain ``requests``.

The keys are read here and nowhere else, handed to the request as a header or
the address, and never written in a log, an error or an answer: what is
logged is the kind of failure only. Tests replace this model, so no test
reaches a network."""
import logging
import time
from urllib.parse import urlsplit

import requests

from odoo import api, models
from odoo.exceptions import UserError

from odoo.addons.nudge.tools.deadline import cap

_logger = logging.getLogger(__name__)

PARAM = "nudge_messaging."
WHATSAPP_API = "https://graph.facebook.com/v21.0"
TELEGRAM_API = "https://api.telegram.org"
TIMEOUT = 15
# The whole of a download, not each read
DOWNLOAD_SECONDS = 25
# What is downloaded from a phone is a voice note or a picture, not a film
MAX_DOWNLOAD = 5 * 1024 * 1024
MAX_TEXT = 3500
CHANNELS = ("whatsapp", "telegram")
SECRET_KEYS = (
    "whatsapp.app_secret", "whatsapp.access_token", "telegram.bot_token", "telegram.webhook_secret")
# Where Graph serves the files of WhatsApp: the access token goes there and nowhere else
WHATSAPP_MEDIA_HOSTS = ("lookaside.fbsbx.com",)
WHATSAPP_MEDIA_DOMAINS = (".fbsbx.com", ".fbcdn.net")
LABELS = {"whatsapp": "WhatsApp", "telegram": "Telegram"}


class MessagingError(UserError):
    """A channel could not be reached; the message holds no key"""


class NudgeMessagingApi(models.AbstractModel):
    _name = "nudge.messaging.api"
    _description = "Nudge Messaging Channels"

    # ------------------------------------------------------------------
    # What the administrator entered
    # ------------------------------------------------------------------
    @api.model
    def _param(self, key):
        # sudo: the keys are readable by the administrator only; they are used here, for the channel
        return (self.env["ir.config_parameter"].sudo().get_param(PARAM + key) or "").strip()

    @api.model
    def _is_enabled(self):
        return self._param("enabled") in ("True", "true", "1")

    @api.model
    def _is_ready(self, channel):
        """:return: whether the channel may receive: on, and the secret that proves a message is real is set"""
        if not self._is_enabled():
            return False
        if channel == "whatsapp":
            return bool(self._param("whatsapp.app_secret"))
        return bool(self._param("telegram.webhook_secret"))

    @api.model
    def _check_egress(self, channel, url):
        """NP-15: the allowed destinations of the company hold for the channels too.

        :raise UserError: before anything is sent, when the host is not on the list"""
        self.env["nudge.egress"].check(url, LABELS.get(channel, channel))

    @api.model
    def _is_media_host(self, url):
        """:return: whether ``url`` is a file of WhatsApp on Meta's servers, the only place its token is sent"""
        parts = urlsplit(str(url or ""))
        host = (parts.hostname or "").lower()
        return parts.scheme == "https" and (
            host in WHATSAPP_MEDIA_HOSTS or host.endswith(WHATSAPP_MEDIA_DOMAINS))

    @api.model
    def _redact(self, text):
        """:return: ``text`` without any of the keys"""
        text = str(text or "")
        for key in SECRET_KEYS:
            secret = self._param(key)
            if secret:
                text = text.replace(secret, "***")
        return text

    # ------------------------------------------------------------------
    # Out
    # ------------------------------------------------------------------
    @api.model
    def _send_text(self, channel, to, text):
        """Send one message. :return: whether it left; never raises, never logs a key"""
        text = str(text or "")[:MAX_TEXT]
        try:
            if channel == "whatsapp":
                phone_id, token = self._param("whatsapp.phone_id"), self._param("whatsapp.access_token")
                if not (phone_id and token):
                    return False
                self._check_egress(channel, WHATSAPP_API)
                reply = requests.post(
                    f"{WHATSAPP_API}/{phone_id}/messages", timeout=TIMEOUT,
                    headers={"Authorization": f"Bearer {token}"},
                    json={"messaging_product": "whatsapp", "to": str(to), "type": "text",
                          "text": {"body": text, "preview_url": False}})
            else:
                token = self._param("telegram.bot_token")
                if not token:
                    return False
                self._check_egress(channel, TELEGRAM_API)
                reply = requests.post(
                    f"{TELEGRAM_API}/bot{token}/sendMessage", timeout=TIMEOUT,
                    json={"chat_id": to, "text": text, "disable_web_page_preview": True})
            if reply.status_code >= 400:
                _logger.info("Nudge messaging: %s refused a message (%s)", channel, reply.status_code)
                return False
            return True
        except UserError:
            _logger.info("Nudge messaging: a message to %s was not sent: not an allowed destination", channel)
            return False
        except requests.RequestException as error:
            # The address of a Telegram call holds the token: only the kind of failure is kept
            _logger.info("Nudge messaging: a message to %s did not leave (%s)", channel, type(error).__name__)
            return False

    # ------------------------------------------------------------------
    # In
    # ------------------------------------------------------------------
    @api.model
    def _download(self, channel, media):
        """:param media: ``{"id": ..., "mime": ..., "name": ...}`` as the webhook gave it
        :return: ``(bytes, mimetype)``
        :raise MessagingError: when the file cannot be had"""
        try:
            if channel == "whatsapp":
                token = self._param("whatsapp.access_token")
                headers = {"Authorization": f"Bearer {token}"}
                self._check_egress(channel, WHATSAPP_API)
                info = requests.get(f"{WHATSAPP_API}/{media['id']}", headers=headers, timeout=TIMEOUT)
                info.raise_for_status()
                described = info.json()
                if not self._is_media_host(described["url"]):
                    # The token is not handed to an address an answer named: only to Meta's file servers
                    _logger.info("Nudge messaging: a file of WhatsApp was not at a host of Meta, not downloaded")
                    raise MessagingError(self.env._("The file could not be downloaded."))
                self._check_egress(channel, described["url"])
                data = requests.get(described["url"], headers=headers, timeout=TIMEOUT, stream=True)
                mime = described.get("mime_type") or media.get("mime") or ""
            else:
                token = self._param("telegram.bot_token")
                self._check_egress(channel, TELEGRAM_API)
                info = requests.get(f"{TELEGRAM_API}/bot{token}/getFile", params={"file_id": media["id"]},
                                    timeout=TIMEOUT)
                info.raise_for_status()
                path = info.json()["result"]["file_path"]
                data = requests.get(f"{TELEGRAM_API}/file/bot{token}/{path}", timeout=TIMEOUT, stream=True)
                mime = media.get("mime") or ""
            data.raise_for_status()
            content = self._read_within(data)
            if len(content) > MAX_DOWNLOAD:
                raise MessagingError(self.env._("The file is too large."))
            return content, mime
        except MessagingError:
            raise
        except UserError as refusal:
            # Not an allowed destination: said as the list says it
            raise MessagingError(str(refusal)) from None
        except (requests.RequestException, KeyError, ValueError, TypeError) as error:
            _logger.info("Nudge messaging: a file of %s could not be had (%s)", channel, type(error).__name__)
            raise MessagingError(self.env._("The file could not be downloaded.")) from None

    @api.model
    def _read_within(self, response):
        """The body of a download, read in blocks within a time for the whole: a server that sends a
        byte now and then is not waited for.

        :raise MessagingError: when it takes longer than :data:`DOWNLOAD_SECONDS` or is above the cap"""
        if not (hasattr(response, "iter_content") and getattr(response, "raw", None)):
            return response.content
        limit = time.monotonic() + cap(DOWNLOAD_SECONDS)
        blocks, size = [], 0
        for block in response.iter_content(65536):
            blocks.append(block)
            size += len(block)
            if size > MAX_DOWNLOAD:
                break
            if time.monotonic() > limit:
                raise MessagingError(self.env._("The file took too long to download."))
        return b"".join(blocks)

    @api.model
    def _telegram_set_webhook(self, url):
        """Tell Telegram where to send updates, with the secret it must repeat. :return: whether it worked"""
        token, secret = self._param("telegram.bot_token"), self._param("telegram.webhook_secret")
        if not (token and secret):
            return False
        try:
            self._check_egress("telegram", TELEGRAM_API)
            reply = requests.post(
                f"{TELEGRAM_API}/bot{token}/setWebhook", timeout=TIMEOUT,
                json={"url": url, "secret_token": secret, "allowed_updates": ["message"]})
            return reply.status_code < 400 and bool(reply.json().get("ok"))
        except UserError:
            _logger.info("Nudge messaging: the webhook was not set: Telegram is not an allowed destination")
            return False
        except (requests.RequestException, ValueError) as error:
            _logger.info("Nudge messaging: the webhook could not be set (%s)", type(error).__name__)
            return False
