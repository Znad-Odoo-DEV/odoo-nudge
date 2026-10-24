import json
import logging
import time

from odoo import _, http
from odoo.http import Response, request
from odoo.tools import frozendict

from ..models.nudge_mcp import (
    AUTH_CONTEXT, INVALID_REQUEST, PARSE_ERROR, InsufficientScope, clean_client, error_response, legacy_auth,
)
from ..models.nudge_mcp_oauth import ACCESS_PREFIX

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
    """:return: whether wrong keys came too often for this key id, or from this address.
    An access token of OAuth has no key id: 256 random bits are not guessed, its address counts"""
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

        The credential travels in ``Authorization: Bearer``: an access token
        of OAuth 2.1, or a key of before (legacy). There is no cookie and
        nothing to forge across sites. A missing or wrong credential is
        answered 401 with where to sign in (RFC 9728); a token without the
        scope of a tool, 403 with the scopes to ask for. The server does not
        open a stream to the client (GET answers 405), which the protocol allows."""
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
        OAuth = env["nudge.mcp.oauth"]
        is_token = scheme.lower() == "bearer" and key.startswith(ACCESS_PREFIX)
        user, auth = env["res.users"], None
        if is_token:
            user, auth = OAuth._authenticate_access(key)
        elif scheme.lower() == "bearer":
            user, auth = Mcp._authenticate(key), legacy_auth()
        key_id = "" if is_token else key
        if not user:
            if _throttled(address, key_id):
                return _reply(error_response(None, -32001, _("Too many wrong keys. Wait a minute.")),
                              status=429, headers=[("Retry-After", str(FAILURE_WINDOW))])
            _failed(address, key_id)
            _logger.info("Nudge MCP: refused a credential from %s", address)
            challenge = OAuth._challenge(error="invalid_token" if key else None)
            return _reply(error_response(None, -32001, _("The key is missing or not valid.")),
                          status=401, headers=[("WWW-Authenticate", challenge)])
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
        # What the credential opens travels in the context, frozen: nothing downstream changes it
        user_env = env(user=user.id, context={"lang": person.lang, "tz": person.tz, AUTH_CONTEXT: frozendict(auth)})
        dispatcher = user_env["nudge.mcp"]
        # Signed in with OAuth, the assistant is the client the person allowed, not what it says it is
        client = auth["client_name"] or dispatcher._session_client(
            user.id, httprequest.headers.get("Mcp-Session-Id")) \
            or clean_client(httprequest.headers.get("User-Agent", "").split("/")[0])
        answers, headers = [], []
        for message in messages:
            try:
                answer, extra = dispatcher._dispatch(message, client)
            except InsufficientScope as error:
                return _reply(error_response(
                    message.get("id") if isinstance(message, dict) else None, -32001,
                    _("This needs more than the assistant was allowed: %s. Sign it in again.",
                          " ".join(error.scopes))),
                    status=403, headers=[("WWW-Authenticate", OAuth._challenge(
                        error="insufficient_scope", scopes=error.scopes))])
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
