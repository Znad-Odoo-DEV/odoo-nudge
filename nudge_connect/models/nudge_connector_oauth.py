"""OAuth sign-in for connectors: authorization code with PKCE (S256), one token per person.

An outside system that serves the data of a person (a calendar, a mailbox, a workspace) asks the person to
sign in with it once. The administrator sets the client of the connector (or lets the server register one,
RFC 7591, found by RFC 9728 and RFC 8414); each person signs in from a link, the provider sends them back to
``/nudge_connect/oauth/callback`` on the base address of Odoo, and the tokens are kept for that person and that
connector only. A person without a token is told to sign in, never given another person's. The tokens are
read by the administrators' group only, are sent to the server they belong to and nowhere else, and are taken
out of everything that is kept or read back, as the other secrets of a connector are.

The address the provider sends the person back to is built from ``web.base.url`` and nothing a request says.
The state is random, single use (deleted by the one statement that reads it), bound to the person and the
connector, and lives ten minutes. The PKCE verifier never leaves the database but for the token request.
"""
import base64
import hashlib
import json
import logging
import secrets
from datetime import timedelta
from urllib.parse import quote, urlencode, urlparse, urlsplit, urlunsplit

import requests

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.nudge.tools.records import Refused

from .. import mcp_client

_logger = logging.getLogger(__name__)

STATE_MINUTES = 10
CALLBACK_PATH = "/nudge_connect/oauth/callback"
START_PATH = "/nudge_connect/oauth/start/%d"
SIGN_OUT_PATH = "/nudge_connect/oauth/signout/%d"
MAX_DOCUMENT = 256 * 1024
# A token that ends within this many seconds is renewed before it is used
RENEW_BEFORE = 30
OAUTH_FIELDS = {"url", "auth_type", "oauth_client_id", "oauth_client_secret", "oauth_token_url",
                "oauth_authorize_url", "oauth_scope"}


def pkce_challenge(verifier):
    """:return: the S256 code challenge of ``verifier`` (RFC 7636)"""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()


class NudgeConnector(models.Model):
    _inherit = "nudge.connector"

    auth_type = fields.Selection(
        selection_add=[("oauth_user", "OAuth Sign-In (Each Person Signs In)")],
        ondelete={"oauth_user": "set default"})
    oauth_authorize_url = fields.Char(
        string="Sign-In Address", help="The page of the provider where a person signs in and agrees (HTTPS).")
    oauth_registration_url = fields.Char(
        string="Registration Address", help="Where Nudge may register itself as a client (RFC 7591), when the server offers it.")
    oauth_redirect_uri = fields.Char(
        string="Redirect Address", compute="_compute_oauth_redirect_uri",
        help="Give this address to the provider when you create the client: it is where the person comes back to.")
    token_ids = fields.One2many("nudge.connector.token", "connector_id", string="Signed-In People")
    signed_in = fields.Boolean(string="You Are Signed In", compute="_compute_signed_in")

    def _compute_oauth_redirect_uri(self):
        for connector in self:
            connector.oauth_redirect_uri = connector._oauth_redirect_uri()

    @api.depends_context("uid")
    def _compute_signed_in(self):
        mine = {row.connector_id.id for row in self.env["nudge.connector.token"].sudo().search([
            ("connector_id", "in", self.ids), ("user_id", "=", self.env.uid)])}
        for connector in self:
            connector.signed_in = connector.id in mine

    @api.constrains("oauth_authorize_url", "oauth_registration_url", "oauth_token_url", "auth_type", "runs_locally")
    def _check_oauth_urls(self):
        for connector in self:
            if connector.auth_type != "oauth_user":
                continue
            for url in filter(None, (connector.oauth_authorize_url, connector.oauth_registration_url,
                                     connector.oauth_token_url)):
                try:
                    mcp_client.check_url(url, allow_private=True)
                except mcp_client.McpError:
                    raise UserError(self.env._(
                        "%s: the address must start with https:// (http:// only for this machine, localhost).",
                        url)) from None

    # ------------------------------------------------------------------ what the base class asks
    def write(self, vals):
        result = super().write(vals)
        if OAUTH_FIELDS & set(vals):
            # The people signed in to the old setup are signed in to nothing now
            self.env["nudge.connector.token"].sudo().search([("connector_id", "in", self.ids)]).unlink()
        return result

    def _secrets(self):
        found = super()._secrets()
        connector = self.sudo()
        if connector.auth_type == "oauth_user":
            for row in self.env["nudge.connector.token"].sudo().search([("connector_id", "=", connector.id)]):
                found += [row.access_token, row.refresh_token]
        return [secret for secret in found if secret]

    def _auth_headers(self):
        if self.sudo().auth_type == "oauth_user":
            # sudo (inside): the token is the person's own and goes to the server it belongs to
            return {"Authorization": "Bearer " + self.env["nudge.connector.token"]._bearer(self)}
        return super()._auth_headers()

    def _failure(self, error):
        if error.code == "signin":
            return self.env._("Sign in to %(name)s first, then ask again: open %(link)s",
                              name=self.name, link=self._oauth_link(START_PATH))
        return super()._failure(error)

    def _oauth_base(self):
        return (self.env["ir.config_parameter"].sudo().get_param("web.base.url") or "").rstrip("/")

    def _oauth_redirect_uri(self):
        """:return: where the provider sends the person back to: the base address of Odoo, never what a
        request says"""
        return self._oauth_base() + CALLBACK_PATH

    def _oauth_link(self, path):
        self.ensure_one()
        return self._oauth_base() + path % self.id

    # ------------------------------------------------------------------ buttons
    def action_sign_in(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": self._oauth_link(START_PATH), "target": "self"}

    def action_sign_out(self):
        """Sign the person out of this connector: their tokens are forgotten here"""
        self.ensure_one()
        self.env["nudge.connector.token"].sudo().search([
            ("connector_id", "=", self.id), ("user_id", "=", self.env.uid)]).unlink()
        return self._notify(self.env._("You are signed out of %s.", self.sudo().name), "success")

    def action_revoke_all(self):
        """Every person's tokens of this connector are forgotten (administrators)"""
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(self.env._("Only an administrator may sign everybody out."))
        self.env["nudge.connector.token"].sudo().search([("connector_id", "=", self.id)]).unlink()
        return self._notify(self.env._("Everybody is signed out of %s.", self.sudo().name), "success")

    def action_discover_oauth(self):
        """Read how the server wants people to sign in (RFC 9728, RFC 8414) and, when there is no client yet
        and the server offers registration (RFC 7591), register one for the redirect address of this Odoo"""
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(self.env._("Only an administrator may set up the sign-in."))
        try:
            found = self._oauth_discover()
            values = {name: found[name] for name in
                      ("oauth_authorize_url", "oauth_token_url", "oauth_registration_url") if found.get(name)}
            if values:
                self.write(values)
            registered = False
            if not self.sudo().oauth_client_id and self.oauth_registration_url:
                self.write(self._oauth_register())
                registered = True
        except mcp_client.McpError as error:
            told = self._failure(error)
            self.last_error = told
            return self._notify(told, "danger")
        if not (self.oauth_authorize_url and self.oauth_token_url):
            return self._notify(self.env._(
                "The server does not say how to sign in: enter the sign-in and token addresses."), "warning")
        return self._notify(self.env._(
            "Found how to sign in%s.", self.env._(" and registered a client") if registered else ""), "success")

    # ------------------------------------------------------------------ talking to the provider
    def _oauth_http(self, url, method="GET", data=None, body=None):
        """:return: ``(status, json)`` of a request to an address of the sign-in: checked as every address
        of the connector is, never followed elsewhere, answer cut to size"""
        self.ensure_one()
        try:
            mcp_client.check_url(url, self.runs_locally)
            self.env["nudge.egress"].check(url, "connector")
        except UserError as refused:
            raise mcp_client.McpError("egress", refused.args[0]) from None
        try:
            response = requests.request(
                method, url, data=data, json=body, headers={"Accept": "application/json"},
                timeout=self._seconds(), allow_redirects=False, stream=True)
            raw = b""
            for chunk in response.iter_content(8192):
                raw += chunk
                if len(raw) > MAX_DOCUMENT:
                    raise mcp_client.McpError("toobig")
        except requests.Timeout:
            raise mcp_client.McpError("timeout") from None
        except requests.RequestException:
            raise mcp_client.McpError("network") from None
        try:
            answer = json.loads(raw or b"{}")
        except ValueError:
            answer = {}
        return response.status_code, answer if isinstance(answer, dict) else {}

    def _oauth_discover(self):
        """:return: the addresses the server publishes: ``oauth_authorize_url``, ``oauth_token_url``,
        ``oauth_registration_url`` (those it does not say are left out)"""
        self.ensure_one()
        parts = urlsplit(self.url)
        origin = f"{parts.scheme}://{parts.netloc}"
        path = parts.path.rstrip("/")
        issuer = origin
        for candidate in dict.fromkeys((f"{origin}/.well-known/oauth-protected-resource{path}",
                                        f"{origin}/.well-known/oauth-protected-resource")):
            status, document = self._oauth_http(candidate)
            servers = document.get("authorization_servers")
            if status == 200 and isinstance(servers, list) and servers and isinstance(servers[0], str):
                issuer = servers[0]
                break
        iparts = urlsplit(issuer)
        iorigin, ipath = f"{iparts.scheme}://{iparts.netloc}", iparts.path.rstrip("/")
        metadata = {}
        for candidate in dict.fromkeys((f"{iorigin}/.well-known/oauth-authorization-server{ipath}",
                                        f"{issuer.rstrip('/')}/.well-known/openid-configuration")):
            status, document = self._oauth_http(candidate)
            if status == 200 and document.get("token_endpoint"):
                metadata = document
                break
        found = {}
        for name, key in (("oauth_authorize_url", "authorization_endpoint"), ("oauth_token_url", "token_endpoint"),
                          ("oauth_registration_url", "registration_endpoint")):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                try:
                    mcp_client.check_url(value, self.runs_locally)
                except mcp_client.McpError:
                    raise mcp_client.McpError("protocol", f"the server names an address that may not be used: {key}") from None
                found[name] = value
        return found

    def _oauth_register(self):
        """:return: the client the server registered for this Odoo (RFC 7591)"""
        self.ensure_one()
        status, answer = self._oauth_http(self.oauth_registration_url, "POST", body={
            "client_name": "Nudge for Odoo", "redirect_uris": [self._oauth_redirect_uri()],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        })
        client_id = answer.get("client_id")
        if status not in (200, 201) or not isinstance(client_id, str) or not client_id:
            raise mcp_client.McpError("auth", f"the server did not register a client (HTTP {status})")
        values = {"oauth_client_id": client_id}
        if isinstance(answer.get("client_secret"), str):
            values["oauth_client_secret"] = answer["client_secret"]
        return values

    # ------------------------------------------------------------------ the sign-in
    def _oauth_begin(self):
        """Start the sign-in of the person of this environment.

        :return: the address of the provider's page
        :raise UserError: when the person may not sign in now, or the connector is not set up for it"""
        self.ensure_one()
        env = self.env
        connector = self.sudo()
        if connector.auth_type != "oauth_user" or not connector.active:
            raise UserError(env._("This connector does not use a sign-in."))
        if not env.user.has_group("base.group_system"):
            try:
                connector.with_env(env)._check_usable()
            except Refused as refused:
                raise UserError(str(refused)) from None
        if not (connector.oauth_authorize_url and connector.oauth_token_url and connector.oauth_client_id):
            raise UserError(env._("The administrator has not finished setting up the sign-in of %s.", connector.name))
        State = env["nudge.connector.oauth.state"].sudo()
        State._purge()
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        State.create({
            "state_hash": State._hash(state), "verifier": verifier, "connector_id": connector.id,
            "user_id": env.uid, "company_id": env.company.id,
            "expires": fields.Datetime.now() + timedelta(minutes=STATE_MINUTES),
        })
        query = {
            "response_type": "code", "client_id": connector.oauth_client_id,
            "redirect_uri": connector._oauth_redirect_uri(), "state": state,
            "code_challenge": pkce_challenge(verifier), "code_challenge_method": "S256", "resource": connector.url,
        }
        if connector.oauth_scope:
            query["scope"] = connector.oauth_scope
        parts = urlsplit(connector.oauth_authorize_url)
        joined = (parts.query + "&" if parts.query else "") + urlencode(query, quote_via=quote)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, joined, ""))

    def _oauth_token_request(self, data):
        """:return: the answer of the token address to ``data``, the client added
        :raise McpError: ``signin`` when the provider refuses the grant, else what went wrong"""
        self.ensure_one()
        connector = self.sudo()
        data = dict(data, client_id=connector.oauth_client_id)
        if connector.oauth_client_secret:
            data["client_secret"] = connector.oauth_client_secret
        status, answer = connector._oauth_http(connector.oauth_token_url, "POST", data=data)
        token = answer.get("access_token")
        if status != 200 or not isinstance(token, str) or not token:
            if status in (400, 401):
                raise mcp_client.McpError("signin")
            raise mcp_client.McpError("auth", f"no token (HTTP {status})")
        return answer


class NudgeConnectorOauthState(models.Model):
    """A sign-in begun and not yet finished: single use, ten minutes. Nudge's own: sudo only."""
    _name = "nudge.connector.oauth.state"
    _description = "Nudge Connector Sign-In Started"

    state_hash = fields.Char(required=True, index=True)
    verifier = fields.Char(required=True, groups="base.group_system")
    connector_id = fields.Many2one("nudge.connector", required=True, ondelete="cascade")
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade")
    company_id = fields.Many2one("res.company", ondelete="cascade")
    expires = fields.Datetime(required=True)

    @api.model
    def _hash(self, state):
        return hashlib.sha256(state.encode()).hexdigest()

    @api.model
    def _purge(self):
        self.sudo().search([("expires", "<=", fields.Datetime.now())]).unlink()

    @api.model
    def _consume(self, state):
        """:return: ``(connector, user_id, verifier)`` of the sign-in begun with ``state``, which can then
        never be used again: the row is deleted by the statement that reads it, so that two requests with
        the same state cannot both succeed
        :raise UserError: when there is no such sign-in or it ran out"""
        self.flush_model()
        self.env.cr.execute(
            "DELETE FROM nudge_connector_oauth_state WHERE state_hash = %s "
            "RETURNING connector_id, user_id, verifier, expires", (self._hash(state or ""),))
        row = self.env.cr.fetchone()
        self.invalidate_model()
        if not row or fields.Datetime.to_datetime(row[3]) <= fields.Datetime.now():
            raise UserError(self.env._("This sign-in link is not valid or ran out. Start again from Nudge."))
        return self.env["nudge.connector"].sudo().browse(row[0]), row[1], row[2]

    @api.model
    def _complete(self, state, code):
        """Finish the sign-in of the person of this environment: the state must be theirs; the code is
        exchanged for tokens, which are kept for this person and the connector of the state.

        :return: the connector"""
        connector, user_id, verifier = self._consume(state)
        if user_id != self.env.uid:
            raise UserError(self.env._("This sign-in was begun by someone else."))
        if not connector.exists() or not connector.active or connector.auth_type != "oauth_user":
            raise UserError(self.env._("This connector no longer uses a sign-in."))
        if not code:
            raise UserError(self.env._("The provider did not send a code."))
        try:
            answer = connector._oauth_token_request({
                "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                "redirect_uri": connector._oauth_redirect_uri(), "resource": connector.url})
        except mcp_client.McpError as error:
            raise UserError(connector._failure(error) if error.code != "signin" else self.env._(
                "The provider refused the sign-in.")) from None
        self.env["nudge.connector.token"]._keep(connector, self.env.user, answer)
        return connector


class NudgeConnectorToken(models.Model):
    """What a person signed in to a connector with. One line per person and connector; the tokens are
    for the administrators' group to own and for nobody to read: they are used in sudo, by the call."""
    _name = "nudge.connector.token"
    _description = "Nudge Connector Sign-In"
    _order = "connector_id, user_id"

    connector_id = fields.Many2one("nudge.connector", required=True, index=True, ondelete="cascade")
    user_id = fields.Many2one("res.users", required=True, index=True, ondelete="cascade")
    access_token = fields.Char(groups="base.group_system", copy=False)
    refresh_token = fields.Char(groups="base.group_system", copy=False)
    expires = fields.Datetime(help="When the access token ends; empty when the provider did not say.")
    scope = fields.Char()

    @api.model
    def _keep(self, connector, user, answer):
        """Keep the tokens of ``answer`` for ``user`` and ``connector`` (a refresh that does not send a new
        refresh token keeps the old one)"""
        # sudo: Nudge's own store
        row = self.sudo().search([("connector_id", "=", connector.id), ("user_id", "=", user.id)], limit=1)
        try:
            lasts = int(answer.get("expires_in") or 0)
        except (TypeError, ValueError):
            lasts = 0
        values = {
            "access_token": answer["access_token"],
            "expires": fields.Datetime.now() + timedelta(seconds=lasts) if lasts > 0 else False,
            "scope": str(answer.get("scope") or "")[:500] or False,
        }
        if isinstance(answer.get("refresh_token"), str) and answer["refresh_token"]:
            values["refresh_token"] = answer["refresh_token"]
        if row:
            row.write(values)
            return row
        return self.sudo().create(dict(values, connector_id=connector.id, user_id=user.id))

    @api.model
    def _bearer(self, connector):
        """:return: the access token of the person of this environment for ``connector``, renewed when it
        ends. Never another person's.
        :raise McpError: ``signin`` when the person has no token (or it can no longer be renewed)"""
        # sudo: Nudge's own store; the line is looked up by this person
        row = self.sudo().search([("connector_id", "=", connector.id), ("user_id", "=", self.env.uid)], limit=1)
        if not row or not row.access_token:
            raise mcp_client.McpError("signin")
        if row.expires and row.expires <= fields.Datetime.now() + timedelta(seconds=RENEW_BEFORE):
            if not row.refresh_token:
                row.unlink()
                raise mcp_client.McpError("signin")
            try:
                answer = connector._oauth_token_request({
                    "grant_type": "refresh_token", "refresh_token": row.refresh_token, "resource": connector.url})
            except mcp_client.McpError as error:
                if error.code == "signin":
                    row.unlink()
                raise
            row = self._keep(connector, self.env.user, answer)
        return row.access_token
