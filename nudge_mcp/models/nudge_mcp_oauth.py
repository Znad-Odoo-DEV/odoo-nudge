"""OAuth 2.1 for the assistants that connect through MCP.

Nudge is its own authorization server, as the MCP authorization specification
(revision 2026-07-28, https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
lets a server be. The assistant finds the server through the protected
resource metadata (RFC 9728) and the authorization server metadata
(RFC 8414), registers itself (RFC 7591) or is registered by an administrator,
and sends the person to Odoo: they sign in, see which assistant asks, where
it sends them back and what it may do, and allow it. The assistant then
holds a short-lived access token and a refresh token that changes at every
use (PKCE with S256 only; a refresh token used twice ends the sign-in).

Only hashes of the codes, tokens and secrets are stored. Every record of this
file is Nudge's own infrastructure: read and written in sudo here, after the
checks made here, and never handed to the tools.
"""
import base64
import calendar
import hashlib
import hmac
import logging
import re
import secrets
import uuid
from datetime import timedelta
from urllib.parse import unquote_plus, urlencode, urlsplit, urlunsplit

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools.misc import hmac as odoo_hmac

from .nudge_mcp import (
    GROUP, SCOPE_DEVELOPER, SCOPE_READ, SCOPE_WRITE, SCOPES, clean_client, expand, ordered,
)

_logger = logging.getLogger(__name__)

# Where the authorization server answers. Not under /mcp: the protocol keeps that address
AUTHORIZE_PATH = "/nudge/oauth/authorize"
TOKEN_PATH = "/nudge/oauth/token"
REGISTER_PATH = "/nudge/oauth/register"
REVOKE_PATH = "/nudge/oauth/revoke"
RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource"
SERVER_METADATA_PATH = "/.well-known/oauth-authorization-server"

# How long each lasts; an administrator may change the two first
ACCESS_MINUTES = 60
REFRESH_DAYS = 30
CODE_SECONDS = 120
ACCESS_PARAM = "nudge_mcp.oauth_access_minutes"
REFRESH_PARAM = "nudge_mcp.oauth_refresh_days"
REGISTRATION_PARAM = "nudge_mcp.oauth_registration"
# The last use of a sign-in is written at most this often: not one write per call
TOUCH_SECONDS = 60

# The start of each secret says what it is, so that a key of before is told apart
ACCESS_PREFIX = "nmcp_at_"
REFRESH_PREFIX = "nmcp_rt_"
CODE_PREFIX = "nmcp_ac_"
CLIENT_PREFIX = "nmcp_c_"
SECRET_PREFIX = "nmcp_cs_"
MAX_SECRET = 200

# Self-registration: a handful of addresses, short names, and no flood of the table
MAX_REDIRECT_URIS = 10
MAX_URI = 500
MAX_UNUSED_CLIENTS = 200
AUTH_METHODS = ("none", "client_secret_post", "client_secret_basic")
LOOPBACK = ("localhost", "127.0.0.1", "[::1]", "::1")
CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")


class OAuthError(Exception):
    """An error of the protocol, told to the client as RFC 6749 says.

    :param error: the code of the error (``invalid_grant``, ...)
    :param description: what went wrong, in words
    :param status: the HTTP status of the answer
    """

    def __init__(self, error, description="", status=400):
        super().__init__(description or error)
        self.error = error
        self.description = description
        self.status = status

    def as_dict(self):
        body = {"error": self.error}
        if self.description:
            body["error_description"] = self.description
        return body


def digest(value):
    """:return: what is stored of a secret: its SHA-256. The secrets are 256
    random bits, so a slow hash adds nothing"""
    return hashlib.sha256((value or "").encode()).hexdigest()


def new_secret(prefix):
    return prefix + secrets.token_urlsafe(32)


def pkce_matches(verifier, challenge):
    """:return: whether the verifier is the one the challenge was made of (S256)"""
    if not isinstance(verifier, str) or not VERIFIER.fullmatch(verifier) or not challenge:
        return False
    made = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return hmac.compare_digest(made, challenge)


def is_loopback(parts):
    return parts.scheme == "http" and (parts.hostname or "") in LOOPBACK


def check_redirect_uri(uri):
    """:return: the address, when an assistant may be sent back to it: https,
    or http on this computer (localhost); no fragment, no user in it
    :raise OAuthError: otherwise"""
    if not isinstance(uri, str) or not uri or len(uri) > MAX_URI or any(char.isspace() for char in uri):
        raise OAuthError("invalid_redirect_uri", "A redirect address is missing or too long.")
    try:
        parts = urlsplit(uri)
        parts.port  # noqa: B018  (raises on a port that is not a number)
    except ValueError:
        raise OAuthError("invalid_redirect_uri", "A redirect address cannot be read.") from None
    if parts.fragment or "#" in uri or parts.username or parts.password or not parts.hostname:
        raise OAuthError("invalid_redirect_uri", "A redirect address may not hold a fragment or a user.")
    if parts.scheme != "https" and not is_loopback(parts):
        raise OAuthError("invalid_redirect_uri", "A redirect address uses https, or http on localhost.")
    return uri


def redirect_matches(registered, uri):
    """:return: whether ``uri`` is one of the registered addresses: the very
    same text, except the port of an address on this computer, which an
    application chooses when it starts (RFC 8252, section 7.3)"""
    if uri in registered:
        return True
    try:
        asked = urlsplit(uri)
    except ValueError:
        return False
    if not is_loopback(asked):
        return False
    for known in registered:
        try:
            parts = urlsplit(known)
        except ValueError:
            continue
        if is_loopback(parts) and (parts.hostname, parts.path, parts.query) == (asked.hostname, asked.path, asked.query):
            return True
    return False


def with_query(uri, values):
    """:return: the address with these values added to its query"""
    parts = urlsplit(uri)
    query = urlencode({key: value for key, value in values.items() if value not in (None, False, "")})
    return urlunsplit(parts._replace(query=f"{parts.query}&{query}" if parts.query else query))


def same_resource(given, ours):
    """:return: whether the resource a client names is this server: scheme
    and host compared without case, a final slash forgiven (RFC 8707)"""
    def canonical(uri):
        parts = urlsplit((uri or "").strip())
        return (parts.scheme.lower(), (parts.netloc or "").lower(), parts.path.rstrip("/"), parts.query)
    try:
        return canonical(given) == canonical(ours)
    except ValueError:
        return False


class NudgeMcpClient(models.Model):
    """An assistant that may ask people to sign in: registered by itself
    (dynamic registration) or by an administrator. Its name is what it says
    of itself; the addresses it sends people back to are what tell it apart."""
    _name = "nudge.mcp.client"
    _description = "Assistant Registered for Sign-in"
    _order = "id desc"

    name = fields.Char(string="Name", required=True, help="The name the assistant gave itself.")
    client_id = fields.Char(
        string="Client ID", required=True, readonly=True, copy=False, index=True,
        default=lambda self: new_secret(CLIENT_PREFIX))
    secret_hash = fields.Char(readonly=True, copy=False, groups="base.group_system")
    auth_method = fields.Selection(
        [("none", "None (an application on a computer)"),
         ("client_secret_post", "Secret in the request"),
         ("client_secret_basic", "Secret in the header")],
        string="Authentication", required=True, default="none", readonly=True)
    redirect_uris = fields.Text(
        string="Redirect Addresses", required=True,
        help="One per line: https, or http on localhost (any port).")
    redirect_hosts = fields.Char(string="Sends You Back To", compute="_compute_redirect_hosts")
    client_uri = fields.Char(string="Website", readonly=True)
    registration = fields.Selection(
        [("dynamic", "Registered itself"), ("manual", "Registered by an administrator")],
        string="Registration", required=True, default="manual", readonly=True)
    grant_ids = fields.One2many("nudge.mcp.grant", "client_id", string="Sign-ins")
    grant_count = fields.Integer(string="People", compute="_compute_grant_count")
    active = fields.Boolean(default=True)

    @api.depends("redirect_uris")
    def _compute_redirect_hosts(self):
        for client in self:
            hosts = []
            for uri in client._redirect_list():
                host = urlsplit(uri).hostname or ""
                if host and host not in hosts:
                    hosts.append(host)
            client.redirect_hosts = ", ".join(hosts)

    def _compute_grant_count(self):
        for client in self:
            client.grant_count = len(client.sudo().grant_ids)

    def _redirect_list(self):
        self.ensure_one()
        return [line.strip() for line in (self.redirect_uris or "").splitlines() if line.strip()]

    @api.constrains("redirect_uris")
    def _check_redirect_uris(self):
        for client in self:
            uris = client._redirect_list()
            if not uris or len(uris) > MAX_REDIRECT_URIS:
                raise ValidationError(_("Give one to %s redirect addresses.", MAX_REDIRECT_URIS))
            for uri in uris:
                try:
                    check_redirect_uri(uri)
                except OAuthError as error:
                    raise ValidationError(_("%(uri)s: %(error)s", uri=uri, error=error.description)) from None

    def init(self):
        # One row per client id: the lookup of the token endpoint relies on it
        self.env.cr.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS nudge_mcp_client_client_id_uniq ON nudge_mcp_client (client_id)")


class NudgeMcpGrant(models.Model):
    """What a person allowed an assistant: one row per person and assistant.
    Revoking it archives it and ends every token it gave."""
    _name = "nudge.mcp.grant"
    _description = "Assistant Connected to Odoo"
    _order = "last_used desc, id desc"
    _rec_name = "client_id"

    user_id = fields.Many2one("res.users", string="Person", required=True, readonly=True, index=True,
                              ondelete="cascade")
    client_id = fields.Many2one("nudge.mcp.client", string="Assistant", required=True, readonly=True,
                                index=True, ondelete="cascade")
    registration = fields.Selection(related="client_id.registration")
    scopes = fields.Char(string="Scopes", readonly=True)
    scope_names = fields.Char(string="May", compute="_compute_scope_names")
    redirect_host = fields.Char(string="Sends You Back To", readonly=True)
    last_used = fields.Datetime(string="Last Used", readonly=True)
    signed_in_until = fields.Datetime(
        string="Signed In Until", readonly=True,
        help="When the assistant has to ask you again, unless it is used before.")
    revoked_at = fields.Datetime(string="Revoked On", readonly=True)
    active = fields.Boolean(default=True, readonly=True)

    @api.depends("scopes")
    def _compute_scope_names(self):
        labels = self.env["nudge.mcp.oauth"]._scope_labels()
        for grant in self:
            grant.scope_names = ", ".join(
                labels[scope][0] for scope in ordered((grant.scopes or "").split()))

    def init(self):
        self.env.cr.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS nudge_mcp_grant_user_client_uniq "
            "ON nudge_mcp_grant (user_id, client_id)")

    def action_revoke(self):
        """Ends the sign-in: the assistant has to ask again. Only the person
        or an administrator; the write is in sudo, the log of Nudge's sign-ins
        is not the person's to edit."""
        is_admin = self.env.user.has_group("base.group_system")
        for grant in self:
            if grant.sudo().user_id != self.env.user and not is_admin:
                raise AccessError(_("This is not yours to revoke."))
        self.sudo()._revoke()
        return True

    def _revoke(self):
        now = fields.Datetime.now()
        self.write({"active": False, "revoked_at": now, "signed_in_until": False})
        self.env["nudge.mcp.token"].search([("grant_id", "in", self.ids), ("revoked", "=", False)]).write(
            {"revoked": True})


class NudgeMcpToken(models.Model):
    """An access or refresh token, stored as its hash. The tokens of one
    sign-in (the code exchanged, and every refresh after it) are a family:
    a refresh token used twice ends the whole family."""
    _name = "nudge.mcp.token"
    _description = "Assistant Sign-in Token"
    _order = "id desc"

    token_hash = fields.Char(required=True, readonly=True, index=True)
    kind = fields.Selection([("access", "Access"), ("refresh", "Refresh")], required=True, readonly=True)
    grant_id = fields.Many2one("nudge.mcp.grant", required=True, readonly=True, index=True, ondelete="cascade")
    family = fields.Char(required=True, readonly=True, index=True)
    scopes = fields.Char(readonly=True)
    resource = fields.Char(readonly=True)
    expires_at = fields.Datetime(required=True, readonly=True)
    used = fields.Boolean(readonly=True, help="A refresh token already exchanged for new tokens.")
    revoked = fields.Boolean(readonly=True)


class NudgeMcpCode(models.Model):
    """An authorization code: good once, for two minutes, for the client and
    the address it was made for, with the PKCE challenge it must answer."""
    _name = "nudge.mcp.code"
    _description = "Assistant Sign-in Code"
    _order = "id desc"

    code_hash = fields.Char(required=True, readonly=True, index=True)
    grant_id = fields.Many2one("nudge.mcp.grant", required=True, readonly=True, index=True, ondelete="cascade")
    redirect_uri = fields.Char(required=True, readonly=True)
    code_challenge = fields.Char(required=True, readonly=True)
    scopes = fields.Char(readonly=True)
    resource = fields.Char(readonly=True)
    expires_at = fields.Datetime(required=True, readonly=True)
    used = fields.Boolean(readonly=True)
    family = fields.Char(readonly=True, help="The tokens it was exchanged for.")


class NudgeMcpOAuth(models.AbstractModel):
    """The authorization server: what the controller of OAuth asks. Every
    method works in sudo on the records above, never on the person's data."""
    _name = "nudge.mcp.oauth"
    _description = "Nudge MCP Sign-in"

    # ------------------------------------------------------------------
    # Addresses and metadata
    # ------------------------------------------------------------------
    @api.model
    def _issuer(self):
        """The address of Odoo, as the administrator set it: the issuer of
        the tokens, and the start of every address below"""
        return (self.env["ir.config_parameter"].sudo().get_param("web.base.url") or "").rstrip("/")

    @api.model
    def _resource(self):
        return self.env["nudge.mcp"]._server_url()

    @api.model
    def _resource_metadata_url(self):
        """RFC 9728: the metadata of /nudge/mcp, path inserted after the well-known part"""
        return self._issuer() + RESOURCE_METADATA_PATH + urlsplit(self._resource()).path

    @api.model
    def _scope_labels(self):
        """:return: ``{scope: (short name, what it lets the assistant do)}``"""
        return {
            SCOPE_READ: (_("Read"), _(
                "Look up records, screens, the documentation of Odoo and the company files, as you: "
                "only what you may see.")),
            SCOPE_WRITE: (_("Prepare changes"), _(
                "Prepare creations, changes and button presses. Each one waits for your yes in Odoo "
                "(Nudge, Assistant Approvals), unless an administrator allowed that very change.")),
            SCOPE_DEVELOPER: (_("Read what developers build"), _(
                "Read automation rules, server actions and the definition of screens. For "
                "administrators; it never builds anything.")),
        }

    @api.model
    def _offered_scopes(self):
        """:return: the scopes the server offers now, whoever asks"""
        exposure = self.env["nudge.mcp"]._exposure()
        found = [SCOPE_READ]
        if exposure in ("write", "develop"):
            found.append(SCOPE_WRITE)
        if exposure == "develop":
            found.append(SCOPE_DEVELOPER)
        return found

    @api.model
    def _grantable_scopes(self):
        """:return: the scopes the current person may give an assistant: those
        the server offers and that they could use themselves"""
        Mcp = self.env["nudge.mcp"]
        found = [SCOPE_READ]
        if Mcp._writes_open():
            found.append(SCOPE_WRITE)
        if Mcp._develop_open():
            found.append(SCOPE_DEVELOPER)
        return found

    @api.model
    def _registration_open(self):
        return self.env["ir.config_parameter"].sudo().get_param(REGISTRATION_PARAM) != "closed"

    @api.model
    def _resource_metadata(self):
        """RFC 9728: where to sign in, and what may be asked"""
        return {
            "resource": self._resource(),
            "authorization_servers": [self._issuer()],
            "scopes_supported": list(SCOPES),
            "bearer_methods_supported": ["header"],
            "resource_name": "Nudge for Odoo",
        }

    @api.model
    def _server_metadata(self):
        """RFC 8414, with what the MCP specification asks: PKCE with S256,
        the issuer in the answer (RFC 9207), self-registration when open"""
        issuer = self._issuer()
        metadata = {
            "issuer": issuer,
            "authorization_endpoint": issuer + AUTHORIZE_PATH,
            "token_endpoint": issuer + TOKEN_PATH,
            "revocation_endpoint": issuer + REVOKE_PATH,
            "scopes_supported": list(SCOPES),
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": list(AUTH_METHODS),
            "revocation_endpoint_auth_methods_supported": list(AUTH_METHODS),
            "code_challenge_methods_supported": ["S256"],
            "authorization_response_iss_parameter_supported": True,
        }
        if self._registration_open():
            metadata["registration_endpoint"] = issuer + REGISTER_PATH
        return metadata

    @api.model
    def _challenge(self, error=None, scopes=None, description=None):
        """:return: the WWW-Authenticate header of a refusal: where the
        metadata is, and the scopes to ask for (RFC 6750, RFC 9728)"""
        parts = ['Bearer realm="nudge_mcp"', f'resource_metadata="{self._resource_metadata_url()}"']
        parts.append('scope="%s"' % " ".join(ordered(scopes or self._offered_scopes())))
        if error:
            parts.append(f'error="{error}"')
        if description:
            parts.append('error_description="%s"' % description.replace('"', "'").replace("\\", ""))
        return ", ".join(parts)

    # ------------------------------------------------------------------
    # Lifetimes
    # ------------------------------------------------------------------
    def _param_int(self, key, default, low, high):
        raw = self.env["ir.config_parameter"].sudo().get_param(key) or ""
        value = int(raw) if raw.isdigit() else default
        return min(max(value, low), high)

    def _access_minutes(self):
        return self._param_int(ACCESS_PARAM, ACCESS_MINUTES, 5, 24 * 60)

    def _refresh_days(self):
        return self._param_int(REFRESH_PARAM, REFRESH_DAYS, 1, 365)

    # ------------------------------------------------------------------
    # Who may ask
    # ------------------------------------------------------------------
    def _client(self, client_id):
        """:return: the active client of this id, in sudo, or nothing"""
        Client = self.env["nudge.mcp.client"].sudo()
        if not isinstance(client_id, str) or not client_id or len(client_id) > MAX_SECRET:
            return Client
        return Client.search([("client_id", "=", client_id)], limit=1)

    def _authenticate_client(self, params, authorization=""):
        """The client of a request to the token or revocation endpoint: its
        id, and its secret when it has one (in the body or in Basic)
        :raise OAuthError: invalid_client"""
        client_id, secret = params.get("client_id"), params.get("client_secret")
        scheme, _space, value = (authorization or "").partition(" ")
        if scheme.lower() == "basic" and value:
            try:
                decoded = base64.b64decode(value.strip(), validate=True).decode()
            except ValueError:
                raise OAuthError("invalid_client", "The client could not be read.", 401) from None
            basic_id, _colon, basic_secret = decoded.partition(":")
            client_id, secret = unquote_plus(basic_id), unquote_plus(basic_secret)
        client = self._client(client_id)
        if not client:
            raise OAuthError("invalid_client", "This client is unknown.", 401)
        if client.auth_method != "none":
            if not isinstance(secret, str) or not secret or not hmac.compare_digest(
                    digest(secret), client.secret_hash or ""):
                raise OAuthError("invalid_client", "The secret of the client is wrong.", 401)
        return client

    # ------------------------------------------------------------------
    # The authorization request and the consent
    # ------------------------------------------------------------------
    def _check_authorization_request(self, params):
        """Checks what an assistant asks, before the person sees it.

        :return: dict of the request, cleaned
        :raise OAuthError: with ``status`` 400; the page tells the person,
            Nudge never sends them anywhere on an error"""
        if not self.env["nudge.mcp"]._is_enabled():
            raise OAuthError("access_denied", _(
                "Nudge is closed to external assistants. An administrator turns it on in Settings, Nudge."))
        client = self._client(params.get("client_id"))
        if not client:
            raise OAuthError("invalid_client", _("This assistant is not registered here."))
        registered = client._redirect_list()
        redirect_uri = params.get("redirect_uri")
        if not redirect_uri and len(registered) == 1 and not is_loopback(urlsplit(registered[0])):
            redirect_uri = registered[0]
        if not isinstance(redirect_uri, str) or not redirect_matches(registered, redirect_uri):
            raise OAuthError("invalid_request", _("The address to send you back to is not the one registered."))
        if params.get("response_type") != "code":
            raise OAuthError("unsupported_response_type", _("Only the authorization code is answered here."))
        if params.get("code_challenge_method") != "S256":
            raise OAuthError("invalid_request", _("PKCE with S256 is required."))
        challenge = params.get("code_challenge")
        if not isinstance(challenge, str) or not CHALLENGE.fullmatch(challenge):
            raise OAuthError("invalid_request", _("The PKCE challenge is missing or malformed."))
        resource = params.get("resource") or self._resource()
        if not same_resource(resource, self._resource()):
            raise OAuthError("invalid_target", _("The resource asked for is not this server."))
        state = params.get("state") or ""
        if not isinstance(state, str) or len(state) > 1000:
            raise OAuthError("invalid_request", _("The state is too long."))
        asked = [scope for scope in str(params.get("scope") or "").split() if scope in SCOPES]
        return {
            "client": client,
            "client_id": client.client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "state": state,
            "resource": self._resource(),
            "scope": " ".join(ordered(expand(asked or self._offered_scopes()))),
        }

    def _consent_signature(self, request_data):
        """What binds the page of consent to the person it was shown to and
        to what it showed: a form posted by another person, or changed, is refused"""
        message = "|".join([str(self.env.uid)] + [str(request_data[key]) for key in (
            "client_id", "redirect_uri", "code_challenge", "state", "resource", "scope")])
        return odoo_hmac(self.env(su=True), "nudge_mcp.consent", message)

    def _consent_values(self, request_data):
        """:return: what the page of consent shows"""
        user = self.env.user
        client = request_data["client"]
        parts = urlsplit(request_data["redirect_uri"])
        labels = self._scope_labels()
        grantable = self._grantable_scopes()
        return {
            "client_name": clean_client(client.name),
            "self_registered": client.registration == "dynamic",
            "redirect_host": parts.hostname or "",
            "is_local": is_loopback(parts),
            "user_name": user.name,
            "user_login": user.login,
            "can_connect": user.has_group(GROUP),
            "scopes": [{
                "name": scope, "label": labels[scope][0], "help": labels[scope][1],
                "available": scope in grantable, "locked": scope == SCOPE_READ,
            } for scope in ordered(request_data["scope"].split())],
            "hidden": [
                ("client_id", request_data["client_id"]), ("redirect_uri", request_data["redirect_uri"]),
                ("response_type", "code"), ("code_challenge", request_data["code_challenge"]),
                ("code_challenge_method", "S256"), ("state", request_data["state"]),
                ("resource", request_data["resource"]), ("scope", request_data["scope"]),
                ("consent", self._consent_signature(request_data)),
            ],
        }

    def _decide(self, request_data, signature, allow, chosen):
        """The person's answer on the page of consent.

        :param chosen: the scopes they left ticked
        :return: the address to send them back to, with the code or the refusal
        :raise OAuthError: when the form is not the one shown to this person"""
        if not isinstance(signature, str) or not hmac.compare_digest(
                signature, self._consent_signature(request_data)):
            raise OAuthError("invalid_request", _(
                "This page was not shown to you, or it was changed. Start again from the assistant."))
        back = {"state": request_data["state"] or None, "iss": self._issuer()}
        if not allow:
            return with_query(request_data["redirect_uri"], dict(back, error="access_denied"))
        user = self.env.user
        if not user.has_group(GROUP):
            raise OAuthError("access_denied", _(
                "Ask an administrator to add you to the group External Assistant."))
        asked = set(request_data["scope"].split())
        granted = (set(chosen or ()) | {SCOPE_READ}) & asked & set(self._grantable_scopes())
        granted = " ".join(ordered(granted)) or SCOPE_READ
        grant = self._grant_for(user, request_data["client"], granted, request_data["redirect_uri"])
        code = new_secret(CODE_PREFIX)
        Code = self.env["nudge.mcp.code"].sudo()
        # The codes nobody exchanged are forgotten as new ones are made
        Code.search([("expires_at", "<", fields.Datetime.now() - timedelta(days=1))], limit=500).unlink()
        Code.create({
            "code_hash": digest(code),
            "grant_id": grant.id,
            "redirect_uri": request_data["redirect_uri"],
            "code_challenge": request_data["code_challenge"],
            "scopes": granted,
            "resource": request_data["resource"],
            "expires_at": fields.Datetime.now() + timedelta(seconds=CODE_SECONDS),
        })
        return with_query(request_data["redirect_uri"], dict(back, code=code))

    def _grant_for(self, user, client, scopes, redirect_uri):
        """:return: the grant of this person for this client, made or renewed"""
        Grant = self.env["nudge.mcp.grant"].sudo().with_context(active_test=False)
        grant = Grant.search([("user_id", "=", user.id), ("client_id", "=", client.id)], limit=1)
        values = {"scopes": scopes, "redirect_host": urlsplit(redirect_uri).hostname or "",
                  "active": True, "revoked_at": False}
        if grant:
            grant.write(values)
            return grant
        return Grant.create(dict(values, user_id=user.id, client_id=client.id))

    # ------------------------------------------------------------------
    # The token endpoint
    # ------------------------------------------------------------------
    def _token_request(self, params, authorization=""):
        """:return: the answer of the token endpoint
        :raise OAuthError: what to answer instead"""
        if not self.env["nudge.mcp"]._is_enabled():
            raise OAuthError("access_denied", "Nudge is closed to external assistants.", 403)
        client = self._authenticate_client(params, authorization)
        grant_type = params.get("grant_type")
        if grant_type == "authorization_code":
            return self._exchange_code(params, client)
        if grant_type == "refresh_token":
            return self._refresh(params, client)
        raise OAuthError("unsupported_grant_type", "Only authorization_code and refresh_token are answered.")

    def _lock(self, table, record_id):
        """Locks one row until the end of the transaction: two requests with the
        same code or refresh token are answered one after the other. ``table``
        is one of the two tables of this file, never a value of the request."""
        assert table in ("nudge_mcp_code", "nudge_mcp_token")
        self.env.cr.execute(f"SELECT used FROM {table} WHERE id = %s FOR UPDATE", [record_id])  # noqa: S608
        row = self.env.cr.fetchone()
        return bool(row and row[0])

    def _usable_grant(self, grant):
        """:return: whether a grant may still be given tokens: not revoked,
        the client and the person still active, the person still in the group"""
        user = grant.user_id
        return bool(grant.active and grant.client_id.active and user.active and user.has_group(GROUP))

    def _exchange_code(self, params, client):
        code = params.get("code")
        Code = self.env["nudge.mcp.code"].sudo()
        row = Code
        if isinstance(code, str) and code.startswith(CODE_PREFIX) and len(code) <= MAX_SECRET:
            row = Code.search([("code_hash", "=", digest(code))], limit=1)
        refused = OAuthError("invalid_grant", "The code is not valid, expired, or was used already.")
        if not row or row.grant_id.client_id != client:
            raise refused
        if self._lock("nudge_mcp_code", row.id):
            # A code used twice: what it gave is taken back (OAuth 2.1, section 4.1.3)
            if row.family:
                self._revoke_family(row.family)
            raise refused
        # Burnt now, whatever comes next: one try per code
        row.used = True
        if row.expires_at <= fields.Datetime.now():
            raise refused
        if params.get("redirect_uri") != row.redirect_uri:
            raise OAuthError("invalid_grant", "The redirect address is not the one of the request.")
        if not pkce_matches(params.get("code_verifier"), row.code_challenge):
            raise OAuthError("invalid_grant", "The PKCE verifier does not match the challenge.")
        if params.get("resource") and not same_resource(params["resource"], row.resource):
            raise OAuthError("invalid_target", "The resource is not the one of the request.")
        if not self._usable_grant(row.grant_id):
            raise refused
        row.family = uuid.uuid4().hex
        return self._issue(row.grant_id, row.scopes, row.resource, row.family)

    def _refresh(self, params, client):
        token = params.get("refresh_token")
        Token = self.env["nudge.mcp.token"].sudo()
        row = Token
        if isinstance(token, str) and token.startswith(REFRESH_PREFIX) and len(token) <= MAX_SECRET:
            row = Token.search([("token_hash", "=", digest(token)), ("kind", "=", "refresh")], limit=1)
        refused = OAuthError("invalid_grant", "The refresh token is not valid; sign in again.")
        if not row or row.grant_id.client_id != client:
            raise refused
        if self._lock("nudge_mcp_token", row.id):
            # Used already: somebody else holds a copy. The whole sign-in ends
            self._revoke_family(row.family)
            _logger.warning("Nudge MCP: a refresh token was used twice; sign-in %s of %s revoked",
                            row.grant_id.id, row.grant_id.user_id.login)
            raise refused
        if row.revoked or row.expires_at <= fields.Datetime.now() or not self._usable_grant(row.grant_id):
            raise refused
        scopes = row.scopes
        if params.get("scope"):
            asked = set(str(params["scope"]).split())
            if not asked <= set(row.scopes.split()):
                raise OAuthError("invalid_scope", "A refresh may narrow the scopes, never widen them.")
            scopes = " ".join(ordered(asked))
        if params.get("resource") and not same_resource(params["resource"], row.resource):
            raise OAuthError("invalid_target", "The resource is not the one of the sign-in.")
        row.used = True
        return self._issue(row.grant_id, scopes, row.resource, row.family)

    def _issue(self, grant, scopes, resource, family):
        """:return: the answer with a new access token and a new refresh token"""
        Token = self.env["nudge.mcp.token"].sudo()
        now = fields.Datetime.now()
        # What expired more than a day ago is of no use to anybody: kept short
        Token.search([("family", "=", family), ("expires_at", "<", now - timedelta(days=1))]).unlink()
        access, refresh = new_secret(ACCESS_PREFIX), new_secret(REFRESH_PREFIX)
        minutes, days = self._access_minutes(), self._refresh_days()
        common = {"grant_id": grant.id, "family": family, "scopes": scopes, "resource": resource}
        Token.create([
            dict(common, kind="access", token_hash=digest(access), expires_at=now + timedelta(minutes=minutes)),
            dict(common, kind="refresh", token_hash=digest(refresh), expires_at=now + timedelta(days=days)),
        ])
        grant.signed_in_until = now + timedelta(days=days)
        return {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": minutes * 60,
            "refresh_token": refresh,
            "scope": scopes,
        }

    def _revoke_family(self, family):
        self.env["nudge.mcp.token"].sudo().search([("family", "=", family)]).write({"revoked": True})

    def _revoke_request(self, params, authorization=""):
        """RFC 7009: the token and its family end; an unknown token is no error"""
        client = self._authenticate_client(params, authorization)
        token = params.get("token")
        if not isinstance(token, str) or len(token) > MAX_SECRET:
            return
        row = self.env["nudge.mcp.token"].sudo().search([("token_hash", "=", digest(token))], limit=1)
        if row and row.grant_id.client_id == client:
            self._revoke_family(row.family)

    # ------------------------------------------------------------------
    # Registration (RFC 7591)
    # ------------------------------------------------------------------
    def _register_client(self, payload):
        """:return: the client made for what an assistant sent about itself
        :raise OAuthError: what to answer instead"""
        if not self.env["nudge.mcp"]._is_enabled() or not self._registration_open():
            raise OAuthError("access_denied", "Assistants cannot register themselves here.", 403)
        if not isinstance(payload, dict):
            raise OAuthError("invalid_client_metadata", "The registration must be a JSON object.")
        uris = payload.get("redirect_uris")
        if not isinstance(uris, list) or not uris or len(uris) > MAX_REDIRECT_URIS:
            raise OAuthError("invalid_redirect_uri", f"Give one to {MAX_REDIRECT_URIS} redirect addresses.")
        uris = [check_redirect_uri(uri) for uri in uris]
        method = payload.get("token_endpoint_auth_method") or "client_secret_basic"
        if method not in AUTH_METHODS:
            raise OAuthError("invalid_client_metadata", "This authentication method is not answered here.")
        grant_types = payload.get("grant_types") or ["authorization_code"]
        if not isinstance(grant_types, list) or not set(grant_types) <= {"authorization_code", "refresh_token"}:
            raise OAuthError("invalid_client_metadata", "Only authorization_code and refresh_token are answered.")
        response_types = payload.get("response_types") or ["code"]
        if response_types != ["code"]:
            raise OAuthError("invalid_client_metadata", "Only the response type code is answered.")
        client_uri = payload.get("client_uri")
        if not (isinstance(client_uri, str) and client_uri.startswith("https://") and len(client_uri) <= MAX_URI):
            client_uri = False
        Client = self.env["nudge.mcp.client"].sudo().with_context(active_test=False)
        now = fields.Datetime.now()
        # Clients that registered and were never allowed by anybody are forgotten after a day
        Client.search([("registration", "=", "dynamic"), ("grant_ids", "=", False),
                       ("create_date", "<", now - timedelta(days=1))], limit=500).unlink()
        if Client.search_count([("registration", "=", "dynamic"), ("grant_ids", "=", False)]) >= MAX_UNUSED_CLIENTS:
            raise OAuthError("temporarily_unavailable", "Too many registrations. Try again tomorrow.", 429)
        secret = new_secret(SECRET_PREFIX) if method != "none" else None
        client = Client.create({
            "name": clean_client(payload.get("client_name")) if payload.get("client_name") else "Assistant",
            "redirect_uris": "\n".join(uris),
            "auth_method": method,
            "registration": "dynamic",
            "client_uri": client_uri,
        })
        if secret:
            client.secret_hash = digest(secret)
        answer = {
            "client_id": client.client_id,
            "client_id_issued_at": calendar.timegm(client.create_date.timetuple()),
            "client_name": client.name,
            "redirect_uris": uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": method,
        }
        if secret:
            answer.update(client_secret=secret, client_secret_expires_at=0)
        return answer

    # ------------------------------------------------------------------
    # The access token at /nudge/mcp
    # ------------------------------------------------------------------
    def _authenticate_access(self, token):
        """:param str token: what came after ``Bearer``, starting with the
            prefix of an access token
        :return: ``(user, auth)``: the person, not in sudo, and what the token
            opens; an empty user when the token is unknown, expired, revoked,
            for another resource, or its person may no longer use it"""
        users = self.env["res.users"]
        if not isinstance(token, str) or not token.startswith(ACCESS_PREFIX) or len(token) > MAX_SECRET:
            return users, None
        row = self.env["nudge.mcp.token"].sudo().search(
            [("token_hash", "=", digest(token)), ("kind", "=", "access")], limit=1)
        if not row or row.revoked or row.expires_at <= fields.Datetime.now():
            return users, None
        if not same_resource(row.resource, self._resource()) or not self._usable_grant(row.grant_id):
            return users, None
        grant = row.grant_id
        self._touch(grant)
        return users.browse(grant.user_id.id), {
            "legacy": False,
            "grant": grant.id,
            "client": grant.client_id.id,
            "client_name": clean_client(grant.client_id.name),
            "scopes": tuple(ordered(expand(row.scopes.split()))),
        }

    def _touch(self, grant):
        """Writes when the sign-in was last used, at most once a minute, on a
        cursor of its own: a row another call holds is skipped, never waited for"""
        now = fields.Datetime.now()
        if grant.last_used and grant.last_used > now - timedelta(seconds=TOUCH_SECONDS):
            return
        try:
            with self.env.registry.cursor() as cr:
                cr.execute(
                    "UPDATE nudge_mcp_grant SET last_used = %s WHERE id IN "
                    "(SELECT id FROM nudge_mcp_grant WHERE id = %s FOR UPDATE SKIP LOCKED)", [now, grant.id])
        except Exception:  # noqa: BLE001
            _logger.debug("Nudge MCP: the last use of sign-in %s was not written", grant.id, exc_info=True)
        grant.invalidate_recordset(["last_used"])

