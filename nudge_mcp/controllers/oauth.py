"""The authorization server of the MCP endpoint (OAuth 2.1).

Metadata (RFC 9728 and RFC 8414) and the token, registration and revocation
endpoints answer JSON, never set a cookie, and are open to any origin: they
hold nothing a page could read without a secret. The page of consent is the
only one that needs the person signed in to Odoo; it is posted with Odoo's
CSRF token, cannot be framed, and never sends the person anywhere before
they have answered.
"""
import json
import logging
import time

from odoo import http
from odoo.http import Response, request

from ..models.nudge_mcp_oauth import (
    AUTHORIZE_PATH, REGISTER_PATH, RESOURCE_METADATA_PATH, REVOKE_PATH, SERVER_METADATA_PATH, TOKEN_PATH,
    OAuthError,
)
from .mcp import FAILURE_WINDOW, _failed, _throttled

_logger = logging.getLogger(__name__)

MAX_FORM_BYTES = 16 * 1024
MAX_REGISTRATION_BYTES = 64 * 1024
# Registrations from one address in a window: the table is not filled by one machine
MAX_REGISTRATIONS = 10
JSON_HEADERS = [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store"),
                ("Pragma", "no-cache")]
PAGE_HEADERS = [("X-Frame-Options", "DENY"), ("Content-Security-Policy", "frame-ancestors 'none'"),
                ("Cache-Control", "no-store"), ("Referrer-Policy", "no-referrer")]


def _json(body, status=200, headers=()):
    return Response(json.dumps(body, ensure_ascii=False), status=status, headers=[*JSON_HEADERS, *headers])


def _error(error):
    headers = []
    if error.status == 401:
        headers.append(("WWW-Authenticate", 'Basic realm="nudge_mcp"'))
    return _json(error.as_dict(), status=error.status, headers=headers)


def _form():
    """:return: the parameters of a form posted to an endpoint, the body only:
    a secret never travels in the address"""
    httprequest = request.httprequest
    if (httprequest.content_length or 0) > MAX_FORM_BYTES:
        raise OAuthError("invalid_request", "The request is too large.", 413)
    return {key: value for key, value in httprequest.form.items() if isinstance(value, str)}


class NudgeMcpOAuthController(http.Controller):

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------
    @http.route([RESOURCE_METADATA_PATH, RESOURCE_METADATA_PATH + "/nudge/mcp"], type="http", auth="public",
                methods=["GET", "OPTIONS"], csrf=False, save_session=False, cors="*")
    def resource_metadata(self, **kwargs):
        """RFC 9728: at the root and with the path of the endpoint inserted"""
        if not request.env["nudge.mcp"]._is_enabled():
            return _json({"error": "not_found"}, status=404)
        return _json(request.env["nudge.mcp.oauth"]._resource_metadata())

    @http.route(SERVER_METADATA_PATH, type="http", auth="public", methods=["GET", "OPTIONS"], csrf=False,
                save_session=False, cors="*")
    def server_metadata(self, **kwargs):
        """RFC 8414, the issuer being the address of Odoo"""
        if not request.env["nudge.mcp"]._is_enabled():
            return _json({"error": "not_found"}, status=404)
        return _json(request.env["nudge.mcp.oauth"]._server_metadata())

    # ------------------------------------------------------------------
    # The page of consent
    # ------------------------------------------------------------------
    @http.route(AUTHORIZE_PATH, type="http", auth="user", methods=["GET", "POST"])
    def authorize(self, **kwargs):
        """GET shows the person what the assistant asks; POST is their
        answer. Every check is made again on the answer."""
        OAuth = request.env["nudge.mcp.oauth"]
        is_answer = request.httprequest.method == "POST"
        params = dict(request.httprequest.form if is_answer else request.httprequest.args)
        params = {key: value for key, value in params.items() if isinstance(value, str)}
        try:
            request_data = OAuth._check_authorization_request(params)
            if not is_answer:
                values = OAuth._consent_values(request_data)
                return request.render("nudge_mcp.oauth_consent", dict(values, action=AUTHORIZE_PATH),
                                      headers=PAGE_HEADERS)
            # Only the person signed in to Odoo answers, for themselves: no field names anybody else
            location = OAuth._decide(
                request_data, params.get("consent"), params.get("decision") == "allow",
                request.httprequest.form.getlist("granted"))
        except OAuthError as error:
            return request.render("nudge_mcp.oauth_message", {
                "title": request.env._("This assistant cannot be connected"),
                "message": error.description or error.error,
            }, headers=PAGE_HEADERS, status=400)
        return request.redirect(location, code=303, local=False)

    # ------------------------------------------------------------------
    # Endpoints of the assistants
    # ------------------------------------------------------------------
    @http.route(TOKEN_PATH, type="http", auth="public", methods=["POST", "OPTIONS"], csrf=False,
                save_session=False, cors="*")
    def token(self, **kwargs):
        """The code for tokens, and a refresh token for new ones (form-encoded)"""
        address = request.httprequest.remote_addr or "?"
        if _throttled(address):
            return _json({"error": "invalid_request", "error_description": "Too many failures. Wait a minute."},
                         status=429, headers=[("Retry-After", str(FAILURE_WINDOW))])
        OAuth = request.env["nudge.mcp.oauth"]
        try:
            answer = OAuth._token_request(_form(), request.httprequest.headers.get("Authorization", ""))
        except OAuthError as error:
            if error.error in ("invalid_grant", "invalid_client"):
                _failed(address)
                _logger.info("Nudge MCP: refused a token request from %s (%s)", address, error.error)
            # Not raised: what was taken back on the way (a code used twice) stays taken back
            return _error(error)
        return _json(answer)

    @http.route(REVOKE_PATH, type="http", auth="public", methods=["POST", "OPTIONS"], csrf=False,
                save_session=False, cors="*")
    def revoke(self, **kwargs):
        """RFC 7009: the token, and every token of its sign-in, end"""
        try:
            request.env["nudge.mcp.oauth"]._revoke_request(_form(), request.httprequest.headers.get("Authorization", ""))
        except OAuthError as error:
            return _error(error)
        return _json({})

    @http.route(REGISTER_PATH, type="http", auth="public", methods=["POST", "OPTIONS"], csrf=False,
                save_session=False, cors="*")
    def register(self, **kwargs):
        """RFC 7591: an assistant registers itself; that alone opens nothing"""
        httprequest = request.httprequest
        address = httprequest.remote_addr or "?"
        if len(_recent_registrations(address)) >= MAX_REGISTRATIONS:
            return _json({"error": "temporarily_unavailable", "error_description": "Too many registrations."},
                         status=429, headers=[("Retry-After", str(FAILURE_WINDOW))])
        if (httprequest.content_length or 0) > MAX_REGISTRATION_BYTES:
            return _json({"error": "invalid_client_metadata", "error_description": "Too large."}, status=413)
        try:
            payload = json.loads(httprequest.get_data(cache=False) or b"")
        except ValueError:
            return _json({"error": "invalid_client_metadata", "error_description": "Not JSON."}, status=400)
        try:
            answer = request.env["nudge.mcp.oauth"]._register_client(payload)
        except OAuthError as error:
            return _error(error)
        _registered(address)
        _logger.info("Nudge MCP: an assistant registered itself from %s", address)
        return _json(answer, status=201)


# Registrations by address in the last hour, in this worker
_registrations = {}
REGISTRATION_WINDOW = 3600


def _recent_registrations(address):
    now = time.monotonic()
    recent = [moment for moment in _registrations.get(address, []) if now - moment < REGISTRATION_WINDOW]
    if recent:
        _registrations[address] = recent
    else:
        _registrations.pop(address, None)
    return recent


def _registered(address):
    if len(_registrations) > 1000:
        _registrations.clear()
    _registrations.setdefault(address, []).append(time.monotonic())
