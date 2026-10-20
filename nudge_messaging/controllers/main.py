"""The two doors the channels knock on.

Public by nature (Meta and Telegram carry no cookie), so nothing is trusted until the
proof is checked, before the body is parsed or anything is read:
WhatsApp signs the body with the app secret (``X-Hub-Signature-256``); Telegram repeats the
secret token chosen with the webhook (``X-Telegram-Bot-Api-Secret-Token``). Wrong or missing: 403,
and nothing else is said. The body is capped at 1 MB. A genuine request is always answered 200,
so a channel does not retry what Nudge already took.

The routes avoid the words mcp and sse on purpose: some local proxies rewrite those paths."""
import hashlib
import hmac
import json
import logging

from odoo import SUPERUSER_ID, http
from odoo.http import Response, request

_logger = logging.getLogger(__name__)

# A message of a phone is small; a picture or a voice note is downloaded by Nudge, not posted here
MAX_BODY = 1024 * 1024
MAX_EVENTS = 20
MAX_TEXT = 4000


def _plain(status, text=""):
    return Response(text, status=status, content_type="text/plain; charset=utf-8",
                    headers=[("Cache-Control", "no-store")])


def _body():
    """:return: the raw body, or None when it is larger than the cap (the route refuses most of
    them before this; a body that announces no length is checked here too)"""
    httprequest = request.httprequest
    if (httprequest.content_length or 0) > MAX_BODY:
        return None
    data = httprequest.get_data(cache=False)
    return None if len(data) > MAX_BODY else data


def _same(left, right):
    return hmac.compare_digest(str(left or "").encode(), str(right or "").encode())


def _text(value):
    return " ".join(str(value or "").split())[:MAX_TEXT]


class NudgeMessagingController(http.Controller):

    # csrf=False on purpose: the callers are servers of Meta and Telegram, not browsers. What
    # protects the door is the signature or the secret token, checked first.
    @http.route("/nudge_messaging/whatsapp", type="http", auth="none", methods=["GET", "POST"],
                csrf=False, save_session=False, readonly=False, max_content_length=MAX_BODY)
    def whatsapp(self, **kwargs):
        # sudo: no user is logged in; the keys are the system's and are read to check the proof
        env = request.env(user=SUPERUSER_ID)
        Api = env["nudge.messaging.api"]
        httprequest = request.httprequest
        # Read first, whatever is decided: a refusal sent before the body is read resets the connection
        data = _body() if httprequest.method == "POST" else b""
        if not Api._is_ready("whatsapp"):
            return _plain(403)
        if httprequest.method == "GET":
            # Meta checks the address once, with the verify token we gave it
            expected = Api._param("whatsapp.verify_token")
            if (expected and kwargs.get("hub.mode") == "subscribe"
                    and _same(kwargs.get("hub.verify_token"), expected)):
                return _plain(200, str(kwargs.get("hub.challenge") or "")[:200])
            return _plain(403)
        if data is None:
            return _plain(413)
        secret = Api._param("whatsapp.app_secret").encode()
        header = httprequest.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(secret, data, hashlib.sha256).hexdigest()
        if not _same(header, expected):
            return _plain(403)
        try:
            payload = json.loads(data or b"{}")
        except ValueError:
            return _plain(200)
        for event in self._whatsapp_events(payload)[:MAX_EVENTS]:
            env["nudge.messaging"]._receive("whatsapp", event)
        return _plain(200)

    @http.route("/nudge_messaging/telegram", type="http", auth="none", methods=["POST"],
                csrf=False, save_session=False, readonly=False, max_content_length=MAX_BODY)
    def telegram(self, **kwargs):
        env = request.env(user=SUPERUSER_ID)
        Api = env["nudge.messaging.api"]
        httprequest = request.httprequest
        data = _body()
        if not Api._is_ready("telegram"):
            return _plain(403)
        if not _same(httprequest.headers.get("X-Telegram-Bot-Api-Secret-Token", ""),
                     Api._param("telegram.webhook_secret")):
            return _plain(403)
        if data is None:
            return _plain(413)
        try:
            payload = json.loads(data or b"{}")
        except ValueError:
            return _plain(200)
        event = self._telegram_event(payload)
        if event:
            env["nudge.messaging"]._receive("telegram", event)
        return _plain(200)

    # ------------------------------------------------------------------
    # What the channels say, as the same small event
    # ------------------------------------------------------------------
    @staticmethod
    def _whatsapp_events(payload):
        """:return: ``[{"id", "sender", "kind", "text", "media"}]`` of a Cloud API notification"""
        events = []
        if not isinstance(payload, dict):
            return events
        for entry in payload.get("entry") or []:
            for change in (entry.get("changes") or []) if isinstance(entry, dict) else []:
                value = change.get("value") if isinstance(change, dict) else None
                for message in (value.get("messages") or []) if isinstance(value, dict) else []:
                    if not isinstance(message, dict) or not message.get("id") or not message.get("from"):
                        continue
                    kind = message.get("type")
                    event = {"id": str(message["id"]), "sender": str(message["from"]), "kind": "other",
                             "text": "", "media": {}, "timestamp": message.get("timestamp")}
                    if kind == "text":
                        event.update(kind="text", text=_text((message.get("text") or {}).get("body")))
                    elif kind in ("audio", "image", "document"):
                        part = message.get(kind) or {}
                        event.update(
                            kind={"audio": "audio", "image": "image", "document": "file"}[kind],
                            text=_text(part.get("caption")),
                            media={"id": str(part.get("id") or ""), "mime": str(part.get("mime_type") or ""),
                                   "name": _text(part.get("filename"))})
                    events.append(event)
        return events

    @staticmethod
    def _telegram_event(payload):
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, dict) or not message.get("message_id"):
            return None
        chat = message.get("chat") or {}
        sender = chat.get("id")
        if sender is None or chat.get("type") not in (None, "private"):
            # A bot added to a group would otherwise answer the whole group
            return None
        event = {"id": "%s:%s" % (payload.get("update_id", ""), message["message_id"]), "sender": str(sender),
                 "kind": "other", "text": "", "media": {}, "timestamp": message.get("date")}
        if message.get("text"):
            event.update(kind="text", text=_text(message["text"]))
        elif message.get("voice") or message.get("audio"):
            part = message.get("voice") or message.get("audio")
            event.update(kind="audio", media={"id": str(part.get("file_id") or ""),
                                              "mime": str(part.get("mime_type") or "audio/ogg")})
        elif message.get("photo"):
            event.update(kind="image", text=_text(message.get("caption")),
                         media={"id": str(message["photo"][-1].get("file_id") or ""), "mime": "image/jpeg"})
        elif message.get("document"):
            part = message["document"]
            event.update(kind="file", text=_text(message.get("caption")),
                         media={"id": str(part.get("file_id") or ""), "mime": str(part.get("mime_type") or ""),
                                "name": _text(part.get("file_name"))})
        return event
