import json
import logging
import time

from odoo import _, http
from odoo.http import Response, request

from ..models.nudge_mcp import INVALID_REQUEST, PARSE_ERROR, clean_client, error_response

_logger = logging.getLogger(__name__)

# A message of the protocol is small; files do not travel through it
MAX_BODY_BYTES = 1024 * 1024
# Wrong keys: this many in a minute for one key id (the start of the key), then it waits;
# a whole address gets more, so that a scanner is slowed and a shared address is not shut
MAX_FAILURES = 10
MAX_FAILURES_ADDRESS = 50
FAILURE_WINDOW = 60
# A batch is a handful of messages, not a way to make thousands of writes in one request
MAX_BATCH = 20
KEY_ID_SIZE = 8
_failures = {}

JSON_HEADERS = [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")]


def _reply(body, status=200, headers=()):
    return Response(json.dumps(body, ensure_ascii=False), status=status, headers=[*JSON_HEADERS, *headers])


def _recent(name):
    now = time.monotonic()
    recent = [moment for moment in _failures.get(name, []) if now - moment < FAILURE_WINDOW]
    if recent:
        _failures[name] = recent
    else:
        _failures.pop(name, None)
    return recent


def _throttled(address, key=""):
    """:return: whether wrong keys came too often for this key id, or from this address"""
    return (len(_recent(("key", key[:KEY_ID_SIZE]))) >= MAX_FAILURES if key else False)         or len(_recent(("address", address))) >= MAX_FAILURES_ADDRESS


def _failed(address, key=""):
    # A server that is scanned does not grow this table without end
    if len(_failures) > 1000:
        _failures.clear()
    _failures.setdefault(("address", address), []).append(time.monotonic())
    if key:
        _failures.setdefault(("key", key[:KEY_ID_SIZE]), []).append(time.monotonic())


class NudgeMcpController(http.Controller):

    @http.route("/nudge/mcp", type="http", auth="public", methods=["GET", "POST", "DELETE"],
                csrf=False, save_session=False)
    def mcp(self, **kwargs):
        """MCP, Streamable HTTP, answered one request at a time with JSON.

        The key travels in ``Authorization: Bearer``; there is no cookie and
        nothing to forge across sites. The server does not open a stream to
        the client (GET answers 405), which the protocol allows."""
        httprequest = request.httprequest
        if httprequest.method != "POST":
            return Response(status=405, headers=[("Allow", "POST")])
        env = request.env
        Mcp = env["nudge.mcp"]
        if not Mcp._is_enabled():
            return _reply(error_response(None, -32001, _(
                "Nudge is closed to external assistants. An administrator turns it on in Settings, Nudge.")),
                status=403)
        address = httprequest.remote_addr or "?"
        authorization = httprequest.headers.get("Authorization", "")
        scheme, _space, key = authorization.partition(" ")
        key = key.strip()
        # Looked at after the key, not before: wrong keys of a scanner at the same address
        # must not shut out the people who hold right ones
        user = Mcp._authenticate(key) if scheme.lower() == "bearer" else env["res.users"]
        if not user:
            if _throttled(address, key):
                return _reply(error_response(None, -32001, _("Too many wrong keys. Wait a minute.")),
                              status=429, headers=[("Retry-After", str(FAILURE_WINDOW))])
            _failed(address, key)
            _logger.info("Nudge MCP: refused a key from %s", address)
            return _reply(error_response(None, -32001, _("The key is missing or not valid.")),
                          status=401, headers=[("WWW-Authenticate", 'Bearer realm="nudge_mcp"')])
        if (httprequest.content_length or 0) > MAX_BODY_BYTES:
            return _reply(error_response(None, INVALID_REQUEST, _("The message is too large.")), status=413)
        try:
            body = json.loads(httprequest.get_data(cache=False) or b"")
        except ValueError:
            return _reply(error_response(None, PARSE_ERROR, _("The message is not JSON.")), status=400)
        messages = body if isinstance(body, list) else [body]
        if not messages:
            return _reply(error_response(None, INVALID_REQUEST, _("The batch is empty.")), status=400)
        if len(messages) > MAX_BATCH:
            return _reply(error_response(None, INVALID_REQUEST, _(
                "A batch holds at most %s messages.", MAX_BATCH)), status=400)

        # The person who holds the key, with their companies and rights: never sudo
        person = user.sudo()
        user_env = env(user=user.id, context={"lang": person.lang, "tz": person.tz})
        dispatcher = user_env["nudge.mcp"]
        client = dispatcher._session_client(user.id, httprequest.headers.get("Mcp-Session-Id")) \
            or clean_client(httprequest.headers.get("User-Agent", "").split("/")[0])
        answers, headers = [], []
        for message in messages:
            try:
                answer, extra = dispatcher._dispatch(message, client)
            except Exception:  # noqa: BLE001
                _logger.exception("Nudge MCP: a message could not be answered")
                answer = error_response(
                    message.get("id") if isinstance(message, dict) else None, -32603,
                    _("Something went wrong on the server."))
                extra = {}
            if answer is not None:
                answers.append(answer)
            headers += list(extra.items())
        if not answers:
            return Response(status=202, headers=headers)
        return _reply(answers if isinstance(body, list) else answers[0], headers=headers)
