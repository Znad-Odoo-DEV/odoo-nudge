import base64
import hashlib
import html
import inspect
import json
import re
import secrets
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.nudge_mcp.models import nudge_mcp
from odoo.addons.nudge_mcp.models import nudge_mcp_oauth as oauth

ENDPOINT = "/nudge/mcp"
REDIRECT = "https://assistant.example.com/callback"
HIDDEN = re.compile(r'<input type="hidden" name="([^"]+)" value="([^"]*)"')


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


@tagged("post_install", "-at_install", "nudge_mcp")
class TestMcpOAuth(HttpCase):
    """OAuth 2.1 over HTTP, as an assistant does it: metadata, registration,
    the page of consent, the code with PKCE, tokens, refresh, revocation, and
    what each token opens at /nudge/mcp."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = "nudge_mcp.group_nudge_mcp,nudge.group_nudge_agent,base.group_partner_manager"
        cls.raghad = new_test_user(cls.env, login="oauth_raghad", groups=groups)
        cls.ebrahim = new_test_user(cls.env, login="oauth_ebrahim", groups=groups)
        cls.reader = new_test_user(cls.env, login="oauth_reader", groups="nudge_mcp.group_nudge_mcp")
        cls.outsider = new_test_user(cls.env, login="oauth_outsider", groups="nudge.group_nudge_agent")
        cls.admin_user = new_test_user(
            cls.env, login="oauth_admin", groups=groups + ",base.group_system,nudge.group_nudge_manager")

    def setUp(self):
        super().setUp()
        from odoo.addons.nudge_mcp.controllers import mcp, oauth as oauth_controller
        mcp._failures.clear()
        oauth_controller._registrations.clear()
        self.addCleanup(mcp._failures.clear)
        self.addCleanup(oauth_controller._registrations.clear)
        self.params()

    def params(self, enabled="True", exposure="write", writes=True):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param(nudge_mcp.ENABLED_PARAM, enabled)
        ICP.set_param(nudge_mcp.EXPOSURE_PARAM, exposure)
        self.env.company.nudge_mcp_allow_writes = writes

    # ------------------------------------------------------------------
    # As an assistant
    # ------------------------------------------------------------------
    def register(self, **values):
        body = dict({"client_name": "Raghad Assistant", "redirect_uris": [REDIRECT],
                     "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"]},
                    **values)
        return self.url_open(oauth.REGISTER_PATH, data=json.dumps(body), headers={"Content-Type": "application/json"})

    def client(self, **values):
        response = self.register(**values)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def authorize_url(self, client_id, challenge, scope=None, redirect=REDIRECT, **extra):
        query = dict({"response_type": "code", "client_id": client_id, "redirect_uri": redirect,
                      "code_challenge": challenge, "code_challenge_method": "S256", "state": "raghad-state",
                      "resource": self.base_url() + ENDPOINT}, **extra)
        if scope is not None:
            query["scope"] = scope
        return f"{oauth.AUTHORIZE_PATH}?{urlencode(query)}"

    def page(self, login, url):
        self.authenticate(login, login)
        response = self.url_open(url, allow_redirects=False)
        return response, {name: html.unescape(value) for name, value in HIDDEN.findall(response.text)}

    def answer(self, form, decision="allow", granted=(), extra=None):
        data = dict(form, decision=decision, **(extra or {}))
        data["granted"] = list(granted) or None
        data = {key: value for key, value in data.items() if value is not None}
        return self.url_open(oauth.AUTHORIZE_PATH, data=data, allow_redirects=False)

    def allow(self, client_id, login="oauth_raghad", granted=(nudge_mcp.SCOPE_READ, nudge_mcp.SCOPE_WRITE),
              redirect=REDIRECT, scope=None):
        """:return: the code and the verifier of a sign-in the person allowed"""
        verifier, challenge = pkce()
        response, form = self.page(login, self.authorize_url(client_id, challenge, scope=scope, redirect=redirect))
        self.assertEqual(response.status_code, 200, response.text[:500])
        answer = self.answer(form, granted=granted)
        self.assertEqual(answer.status_code, 303, answer.text[:500])
        query = parse_qs(urlsplit(answer.headers["Location"]).query)
        return query["code"][0], verifier, answer

    def token(self, data, headers=None):
        return self.url_open(oauth.TOKEN_PATH, data=data, headers=headers)

    def exchange(self, client_id, code, verifier, redirect=REDIRECT):
        return self.token({"grant_type": "authorization_code", "client_id": client_id, "code": code,
                           "code_verifier": verifier, "redirect_uri": redirect,
                           "resource": self.base_url() + ENDPOINT})

    def sign_in(self, login="oauth_raghad", granted=(nudge_mcp.SCOPE_READ, nudge_mcp.SCOPE_WRITE), **values):
        """:return: the client and the tokens of a whole sign-in"""
        client = self.client(**values)
        code, verifier, _answer = self.allow(client["client_id"], login=login, granted=granted)
        response = self.exchange(client["client_id"], code, verifier)
        self.assertEqual(response.status_code, 200, response.text)
        return client, response.json()

    def refresh(self, client_id, refresh_token, **extra):
        return self.token(dict({"grant_type": "refresh_token", "client_id": client_id,
                                "refresh_token": refresh_token}, **extra))

    def rpc(self, access_token, method, params=None):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
        return self.url_open(ENDPOINT, data=body, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {access_token}"})

    def tool_names(self, access_token):
        answer = self.rpc(access_token, "tools/list")
        self.assertEqual(answer.status_code, 200, answer.text)
        return {tool["name"] for tool in answer.json()["result"]["tools"]}

    def legacy_key(self, user):
        """:return: a key of before OAuth (legacy) for ``user``; Odoo 17 keys have no expiry"""
        Keys = self.env["res.users.apikeys"].with_user(user).sudo()
        if "expiration_date" in inspect.signature(Keys._generate).parameters:
            return Keys._generate(nudge_mcp.SCOPE, "legacy", fields.Datetime.now() + timedelta(days=1))
        return Keys._generate(nudge_mcp.SCOPE, "legacy")

    def grant_of(self, client, user):
        return self.env["nudge.mcp.grant"].sudo().with_context(active_test=False).search([
            ("client_id.client_id", "=", client["client_id"]), ("user_id", "=", user.id)])

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def test_metadata_and_the_challenge(self):
        base = self.base_url()
        for path in (oauth.RESOURCE_METADATA_PATH, oauth.RESOURCE_METADATA_PATH + ENDPOINT):
            metadata = self.url_open(path).json()
            self.assertEqual(metadata["resource"], base + ENDPOINT)
            self.assertEqual(metadata["authorization_servers"], [base])
            self.assertEqual(metadata["scopes_supported"], list(nudge_mcp.SCOPES))
        server = self.url_open(oauth.SERVER_METADATA_PATH).json()
        self.assertEqual(server["issuer"], base)
        self.assertEqual(server["code_challenge_methods_supported"], ["S256"])
        self.assertEqual(server["authorization_endpoint"], base + oauth.AUTHORIZE_PATH)
        self.assertEqual(server["registration_endpoint"], base + oauth.REGISTER_PATH)
        self.assertTrue(server["authorization_response_iss_parameter_supported"])
        self.assertNotIn("offline_access", server["scopes_supported"])
        # Without a credential, the endpoint says where to sign in and what to ask for
        refused = self.url_open(ENDPOINT, data="{}", headers={"Content-Type": "application/json"})
        self.assertEqual(refused.status_code, 401)
        challenge = refused.headers["WWW-Authenticate"]
        self.assertIn(f'resource_metadata="{base}{oauth.RESOURCE_METADATA_PATH}{ENDPOINT}"', challenge)
        self.assertIn('scope="nudge.read nudge.write"', challenge)
        # Registration closed: not announced; MCP closed: nothing is
        self.env["ir.config_parameter"].sudo().set_param(oauth.REGISTRATION_PARAM, "closed")
        self.assertNotIn("registration_endpoint", self.url_open(oauth.SERVER_METADATA_PATH).json())
        self.params("False")
        self.assertEqual(self.url_open(oauth.SERVER_METADATA_PATH).status_code, 404)
        self.assertEqual(self.url_open(oauth.RESOURCE_METADATA_PATH).status_code, 404)

    def test_registration(self):
        client = self.client()
        self.assertTrue(client["client_id"].startswith(oauth.CLIENT_PREFIX))
        self.assertEqual(client["token_endpoint_auth_method"], "none")
        self.assertNotIn("client_secret", client)
        record = self.env["nudge.mcp.client"].sudo().search([("client_id", "=", client["client_id"])])
        self.assertEqual((record.registration, record.name), ("dynamic", "Raghad Assistant"))
        # A confidential client gets a secret once; only its hash is kept
        confidential = self.client(token_endpoint_auth_method="client_secret_post")
        record = self.env["nudge.mcp.client"].sudo().search([("client_id", "=", confidential["client_id"])])
        self.assertTrue(confidential["client_secret"].startswith(oauth.SECRET_PREFIX))
        self.assertEqual(record.secret_hash, oauth.digest(confidential["client_secret"]))
        self.assertNotIn(confidential["client_secret"], record.secret_hash)
        # Addresses an assistant may not be sent back to
        for bad in ("http://assistant.example.com/callback", "https://assistant.example.com/cb#x",
                    "javascript:alert(1)", "https://user:pw@assistant.example.com/cb"):
            response = self.register(redirect_uris=[bad])
            self.assertEqual(response.status_code, 400, bad)
            self.assertEqual(response.json()["error"], "invalid_redirect_uri")
        self.assertEqual(self.register(response_types=["token"]).status_code, 400)
        self.assertEqual(self.register(grant_types=["client_credentials"]).status_code, 400)
        # A name is what the assistant says of itself: kept short and plain
        named = self.client(client_name="<script>Ebrahim</script>" + "x" * 200)
        self.assertNotIn("<", named["client_name"])
        self.assertLessEqual(len(named["client_name"]), 60)
        # The administrator may close it
        self.env["ir.config_parameter"].sudo().set_param(oauth.REGISTRATION_PARAM, "closed")
        self.assertEqual(self.register().status_code, 403)

    def test_registrations_are_capped_by_address(self):
        from odoo.addons.nudge_mcp.controllers import oauth as oauth_controller
        codes = [self.register().status_code for _index in range(oauth_controller.MAX_REGISTRATIONS + 1)]
        self.assertEqual(codes[-1], 429)
        self.assertEqual(set(codes[:-1]), {201})

    # ------------------------------------------------------------------
    # The whole flow
    # ------------------------------------------------------------------
    def test_full_pkce_flow(self):
        client = self.client()
        verifier, challenge = pkce()
        # Not signed in to Odoo: sent to the login page first, then back here
        self.url_open("/web/session/logout", allow_redirects=False)
        anonymous = self.url_open(self.authorize_url(client["client_id"], challenge), allow_redirects=False)
        self.assertIn(anonymous.status_code, (302, 303))
        self.assertIn("/web/login", anonymous.headers["Location"])
        # The page shows who asks, where it sends back, and cannot be framed
        response, form = self.page("oauth_raghad", self.authorize_url(client["client_id"], challenge))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Raghad Assistant", response.text)
        self.assertIn("assistant.example.com", response.text)
        self.assertIn("oauth_raghad", response.text)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertTrue(form["csrf_token"])
        self.assertTrue(form["consent"])
        # Allowed: back to the assistant with the code, the state and the issuer (RFC 9207)
        answer = self.answer(form, granted=[nudge_mcp.SCOPE_READ, nudge_mcp.SCOPE_WRITE])
        self.assertEqual(answer.status_code, 303)
        location = urlsplit(answer.headers["Location"])
        self.assertEqual(f"{location.scheme}://{location.netloc}{location.path}", REDIRECT)
        query = parse_qs(location.query)
        self.assertEqual(query["state"], ["raghad-state"])
        self.assertEqual(query["iss"], [self.base_url()])
        code = query["code"][0]
        stored = self.env["nudge.mcp.code"].sudo().search([("code_hash", "=", oauth.digest(code))])
        self.assertTrue(stored)
        self.assertNotIn(code, stored.code_hash)
        # The code and the verifier for the tokens
        response = self.exchange(client["client_id"], code, verifier)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("Set-Cookie", response.headers)
        tokens = response.json()
        self.assertEqual(tokens["token_type"], "Bearer")
        self.assertEqual(tokens["expires_in"], oauth.ACCESS_MINUTES * 60)
        self.assertEqual(tokens["scope"], "nudge.read nudge.write")
        self.assertTrue(tokens["refresh_token"].startswith(oauth.REFRESH_PREFIX))
        # Only hashes are kept
        Token = self.env["nudge.mcp.token"].sudo()
        self.assertEqual(Token.search_count([("token_hash", "in", [tokens["access_token"], tokens["refresh_token"]])]), 0)
        self.assertEqual(Token.search_count([("token_hash", "=", oauth.digest(tokens["access_token"]))]), 1)
        # The token opens /nudge/mcp as Raghad, logged with the client she allowed
        self.url_open("/web/session/logout", allow_redirects=False)
        names = self.tool_names(tokens["access_token"])
        self.assertTrue({"search_records", "prepare_create", "confirm"} <= names)
        answer = self.rpc(tokens["access_token"], "tools/call", {
            "name": "search_odoo_documentation", "arguments": {"query": "invoice"}})
        self.assertEqual(answer.status_code, 200, answer.text)
        run = self.env["nudge.run"].sudo().search([("user_id", "=", self.raghad.id)], order="id desc", limit=1)
        self.assertEqual(run.mcp_client_id.client_id, client["client_id"])
        self.assertEqual(run.origin, "MCP: Raghad Assistant")
        # Listed on her connected assistants, with when it was last used
        grant = self.grant_of(client, self.raghad)
        self.assertTrue(grant.last_used)
        self.assertTrue(grant.signed_in_until)
        self.assertEqual(grant.redirect_host, "assistant.example.com")
        self.assertIn(grant, self.raghad.with_user(self.raghad).nudge_mcp_grant_ids)

    def test_refused_at_consent(self):
        client = self.client()
        _verifier, challenge = pkce()
        _response, form = self.page("oauth_raghad", self.authorize_url(client["client_id"], challenge))
        answer = self.answer(form, decision="deny")
        self.assertEqual(answer.status_code, 303)
        query = parse_qs(urlsplit(answer.headers["Location"]).query)
        self.assertEqual(query["error"], ["access_denied"])
        self.assertNotIn("code", query)
        self.assertFalse(self.grant_of(client, self.raghad))

    def test_bad_requests_never_redirect(self):
        client = self.client()
        _verifier, challenge = pkce()
        self.authenticate("oauth_raghad", "oauth_raghad")
        bad = [
            self.authorize_url("nmcp_c_unknown", challenge),
            self.authorize_url(client["client_id"], challenge, redirect="https://elsewhere.example.com/cb"),
            self.authorize_url(client["client_id"], challenge, code_challenge_method="plain"),
            self.authorize_url(client["client_id"], "short"),
            self.authorize_url(client["client_id"], challenge, response_type="token"),
            self.authorize_url(client["client_id"], challenge, resource="https://other.example.com/mcp"),
        ]
        for url in bad:
            response = self.url_open(url, allow_redirects=False)
            self.assertEqual(response.status_code, 400, url)
            self.assertNotIn("Location", response.headers)
        # Without a PKCE challenge at all
        url = self.authorize_url(client["client_id"], challenge).replace("code_challenge=", "x=")
        self.assertEqual(self.url_open(url, allow_redirects=False).status_code, 400)

    def test_loopback_address_takes_any_port(self):
        client = self.client(redirect_uris=["http://localhost/callback", "http://127.0.0.1/callback"])
        code, verifier, _answer = self.allow(client["client_id"], redirect="http://localhost:53682/callback")
        response = self.exchange(client["client_id"], code, verifier, redirect="http://localhost:53682/callback")
        self.assertEqual(response.status_code, 200, response.text)
        # The page warns that the assistant runs on this computer
        _verifier, challenge = pkce()
        page, _form = self.page("oauth_raghad", self.authorize_url(
            client["client_id"], challenge, redirect="http://127.0.0.1:9999/callback"))
        self.assertIn("runs on this computer", page.text)
        # Another path is another address
        page, _form = self.page("oauth_raghad", self.authorize_url(
            client["client_id"], challenge, redirect="http://localhost:53682/other"))
        self.assertEqual(page.status_code, 400)

    # ------------------------------------------------------------------
    # The code
    # ------------------------------------------------------------------
    def test_wrong_verifier(self):
        client = self.client()
        code, verifier, _answer = self.allow(client["client_id"])
        wrong = self.exchange(client["client_id"], code, pkce()[0])
        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(wrong.json()["error"], "invalid_grant")
        # One try per code: the right verifier comes too late
        late = self.exchange(client["client_id"], code, verifier)
        self.assertEqual(late.json()["error"], "invalid_grant")
        self.assertFalse(self.env["nudge.mcp.token"].sudo().search([("grant_id", "=", self.grant_of(client, self.raghad).id)]))

    def test_code_reuse_takes_back_its_tokens(self):
        client = self.client()
        code, verifier, _answer = self.allow(client["client_id"])
        tokens = self.exchange(client["client_id"], code, verifier).json()
        self.assertEqual(self.rpc(tokens["access_token"], "ping").status_code, 200)
        again = self.exchange(client["client_id"], code, verifier)
        self.assertEqual(again.json()["error"], "invalid_grant")
        # Whoever used it first is no longer trusted: its tokens end
        self.assertEqual(self.rpc(tokens["access_token"], "ping").status_code, 401)
        self.assertEqual(self.refresh(client["client_id"], tokens["refresh_token"]).json()["error"], "invalid_grant")

    def test_code_is_for_its_client_address_and_time(self):
        client, other = self.client(), self.client(client_name="Ebrahim Assistant")
        code, verifier, _answer = self.allow(client["client_id"])
        self.assertEqual(self.exchange(other["client_id"], code, verifier).json()["error"], "invalid_grant")
        code, verifier, _answer = self.allow(client["client_id"])
        self.assertEqual(self.exchange(client["client_id"], code, verifier,
                                       redirect="https://assistant.example.com/other").json()["error"], "invalid_grant")
        code, verifier, _answer = self.allow(client["client_id"])
        self.env["nudge.mcp.code"].sudo().search([("code_hash", "=", oauth.digest(code))]).write(
            {"expires_at": fields.Datetime.now() - timedelta(seconds=1)})
        self.assertEqual(self.exchange(client["client_id"], code, verifier).json()["error"], "invalid_grant")
        # A client with a secret must give it
        confidential = self.client(token_endpoint_auth_method="client_secret_basic")
        code, verifier, _answer = self.allow(confidential["client_id"])
        data = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                "redirect_uri": REDIRECT, "client_id": confidential["client_id"]}
        self.assertEqual(self.token(data).status_code, 401)
        wrong = base64.b64encode(f"{confidential['client_id']}:nmcp_cs_wrong".encode()).decode()
        self.assertEqual(self.token(data, headers={"Authorization": f"Basic {wrong}"}).status_code, 401)
        # A client that failed to authenticate did not use the code up
        basic = base64.b64encode(f"{confidential['client_id']}:{confidential['client_secret']}".encode()).decode()
        response = self.token(data, headers={"Authorization": f"Basic {basic}"})
        self.assertEqual(response.status_code, 200, response.text)

    # ------------------------------------------------------------------
    # The tokens
    # ------------------------------------------------------------------
    def test_expired_access_token(self):
        client, tokens = self.sign_in()
        self.env["nudge.mcp.token"].sudo().search([("token_hash", "=", oauth.digest(tokens["access_token"]))]).write(
            {"expires_at": fields.Datetime.now() - timedelta(seconds=1)})
        response = self.rpc(tokens["access_token"], "ping")
        self.assertEqual(response.status_code, 401)
        self.assertIn('error="invalid_token"', response.headers["WWW-Authenticate"])
        # The refresh token gives a new one
        fresh = self.refresh(client["client_id"], tokens["refresh_token"]).json()
        self.assertEqual(self.rpc(fresh["access_token"], "ping").status_code, 200)

    def test_refresh_rotation_and_reuse_detection(self):
        client, tokens = self.sign_in()
        first = self.refresh(client["client_id"], tokens["refresh_token"])
        self.assertEqual(first.status_code, 200, first.text)
        rotated = first.json()
        self.assertNotEqual(rotated["refresh_token"], tokens["refresh_token"])
        self.assertNotEqual(rotated["access_token"], tokens["access_token"])
        self.assertEqual(self.rpc(rotated["access_token"], "ping").status_code, 200)
        # A refresh may narrow the scopes, never widen them
        self.assertEqual(self.refresh(client["client_id"], rotated["refresh_token"],
                                      scope="nudge.developer").json()["error"], "invalid_scope")
        # The old refresh token again: somebody holds a copy, the whole sign-in ends
        reused = self.refresh(client["client_id"], tokens["refresh_token"])
        self.assertEqual(reused.status_code, 400)
        self.assertEqual(reused.json()["error"], "invalid_grant")
        self.assertEqual(self.rpc(rotated["access_token"], "ping").status_code, 401)
        self.assertEqual(self.refresh(client["client_id"], rotated["refresh_token"]).json()["error"], "invalid_grant")
        # A refresh token is for its own client
        client2, tokens2 = self.sign_in(client_name="Ebrahim Assistant")
        self.assertEqual(self.refresh(client["client_id"], tokens2["refresh_token"]).json()["error"], "invalid_grant")
        self.assertEqual(self.refresh(client2["client_id"], tokens2["refresh_token"]).status_code, 200)

    def test_revoke(self):
        client, tokens = self.sign_in()
        grant = self.grant_of(client, self.raghad)
        # Nobody else revokes it
        with self.assertRaises(AccessError):
            grant.with_user(self.ebrahim).action_revoke()
        grant.with_user(self.raghad).action_revoke()
        self.assertFalse(grant.active)
        self.assertEqual(self.rpc(tokens["access_token"], "ping").status_code, 401)
        self.assertEqual(self.refresh(client["client_id"], tokens["refresh_token"]).json()["error"], "invalid_grant")
        self.assertNotIn(grant, self.raghad.with_user(self.raghad).nudge_mcp_grant_ids)
        # Allowed again: the same grant comes back
        code, verifier, _answer = self.allow(client["client_id"])
        tokens = self.exchange(client["client_id"], code, verifier).json()
        self.assertTrue(grant.active)
        # The revocation endpoint (RFC 7009) ends the sign-in too
        response = self.url_open(oauth.REVOKE_PATH, data={"token": tokens["refresh_token"],
                                                          "client_id": client["client_id"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.rpc(tokens["access_token"], "ping").status_code, 401)
        # An administrator sees and revokes any; a person sees only theirs
        Grant = self.env["nudge.mcp.grant"]
        _client2, _tokens2 = self.sign_in(login="oauth_ebrahim")
        self.assertFalse(Grant.with_user(self.raghad).search([("user_id", "=", self.ebrahim.id)]))
        self.assertTrue(Grant.with_user(self.admin_user).search([("user_id", "=", self.ebrahim.id)]))

    # ------------------------------------------------------------------
    # Whose sign-in
    # ------------------------------------------------------------------
    def test_a_person_cannot_authorise_for_another(self):
        client = self.client()
        verifier, challenge = pkce()
        _page, raghads_form = self.page("oauth_raghad", self.authorize_url(client["client_id"], challenge))
        # Ebrahim posts the page shown to Raghad, with his own session and CSRF token: refused
        _page, ebrahims_form = self.page("oauth_ebrahim", self.authorize_url(client["client_id"], challenge))
        forged = dict(raghads_form, csrf_token=ebrahims_form["csrf_token"])
        answer = self.answer(forged)
        self.assertEqual(answer.status_code, 400)
        self.assertNotIn("Location", answer.headers)
        self.assertFalse(self.grant_of(client, self.raghad))
        self.assertFalse(self.grant_of(client, self.ebrahim))
        # A field naming Raghad changes nothing: the code is Ebrahim's
        answer = self.answer(ebrahims_form, extra={"user_id": str(self.raghad.id), "login": "oauth_raghad"})
        self.assertEqual(answer.status_code, 303)
        code = parse_qs(urlsplit(answer.headers["Location"]).query)["code"][0]
        tokens = self.exchange(client["client_id"], code, verifier).json()
        self.rpc(tokens["access_token"], "tools/call", {"name": "search_odoo_documentation",
                                                        "arguments": {"query": "invoice"}})
        run = self.env["nudge.run"].sudo().search([("mcp_client_id.client_id", "=", client["client_id"])],
                                                   order="id desc", limit=1)
        self.assertEqual(run.user_id, self.ebrahim)
        self.assertFalse(self.grant_of(client, self.raghad))
        # A person out of the group cannot allow anything
        _verifier, challenge = pkce()
        page, form = self.page("oauth_outsider", self.authorize_url(client["client_id"], challenge))
        self.assertIn("group External Assistant", page.text)
        self.assertEqual(self.answer(form, granted=[nudge_mcp.SCOPE_READ]).status_code, 400)
        self.assertFalse(self.grant_of(client, self.outsider))

    def test_an_archived_person_has_no_more_access(self):
        client, tokens = self.sign_in(login="oauth_ebrahim")
        self.assertEqual(self.rpc(tokens["access_token"], "ping").status_code, 200)
        self.ebrahim.active = False
        self.assertEqual(self.rpc(tokens["access_token"], "ping").status_code, 401)
        self.assertEqual(self.refresh(client["client_id"], tokens["refresh_token"]).json()["error"], "invalid_grant")
        # Out of the group: the same
        client, tokens = self.sign_in(login="oauth_raghad")
        groups = "group_ids" if "group_ids" in self.env["res.users"]._fields else "groups_id"
        self.raghad.write({groups: [(3, self.env.ref("nudge_mcp.group_nudge_mcp").id)]})
        self.assertEqual(self.rpc(tokens["access_token"], "ping").status_code, 401)
        # An archived client: nobody signs it in, its tokens stop
        self.raghad.write({groups: [(4, self.env.ref("nudge_mcp.group_nudge_mcp").id)]})
        self.assertEqual(self.rpc(tokens["access_token"], "ping").status_code, 200)
        self.env["nudge.mcp.client"].sudo().search([("client_id", "=", client["client_id"])]).active = False
        self.assertEqual(self.rpc(tokens["access_token"], "ping").status_code, 401)

    # ------------------------------------------------------------------
    # Scopes
    # ------------------------------------------------------------------
    def test_scope_enforcement_per_tool(self):
        # Read only: Raghad unticked the changes
        client, tokens = self.sign_in(granted=[nudge_mcp.SCOPE_READ])
        self.assertEqual(tokens["scope"], "nudge.read")
        names = self.tool_names(tokens["access_token"])
        self.assertIn("search_records", names)
        self.assertFalse(names & {"prepare_create", "prepare_write", "call_method", "confirm", "decline"})
        # A write tool with that token: 403 and the scopes to ask for, nothing prepared
        response = self.rpc(tokens["access_token"], "tools/call", {
            "name": "prepare_create", "arguments": {"model": "res.partner",
                                                    "values": [{"field": "name", "value": "Ebrahim Scoped"}]}})
        self.assertEqual(response.status_code, 403)
        challenge = response.headers["WWW-Authenticate"]
        self.assertIn('error="insufficient_scope"', challenge)
        self.assertIn('scope="nudge.read nudge.write"', challenge)
        self.assertIn("resource_metadata=", challenge)
        self.assertFalse(self.env["nudge.step"].sudo().search([("tool", "=", "prepare_create"),
                                                                ("user_id", "=", self.raghad.id)]))
        # A tool the server does not offer is no scope matter: it does not exist
        self.params("True", "read", False)
        answer = self.rpc(tokens["access_token"], "tools/call", {"name": "prepare_create", "arguments": {}})
        self.assertEqual(answer.json()["error"]["code"], nudge_mcp.INVALID_PARAMS)
        # Granted, the same person prepares it
        self.params()
        _client, tokens = self.sign_in(granted=[nudge_mcp.SCOPE_READ, nudge_mcp.SCOPE_WRITE])
        answer = self.rpc(tokens["access_token"], "tools/call", {
            "name": "prepare_create", "arguments": {"model": "res.partner",
                                                    "values": [{"field": "name", "value": "Ebrahim Scoped"}]}})
        self.assertFalse(answer.json()["result"]["isError"], answer.text)

    def test_developer_scope(self):
        self.params("True", "develop", True)
        self.env["ir.config_parameter"].sudo().set_param("nudge.developer", "yes")
        # Raghad is no administrator: the page offers her no developer scope, and none is granted
        client = self.client()
        _verifier, challenge = pkce()
        page, _form = self.page("oauth_raghad", self.authorize_url(client["client_id"], challenge))
        self.assertIn("Not open to you here", page.text)
        _client, tokens = self.sign_in(granted=list(nudge_mcp.SCOPES))
        self.assertEqual(tokens["scope"], "nudge.read nudge.write")
        self.assertNotIn("read_view", self.tool_names(tokens["access_token"]))
        # An administrator with Developer mode: only with the scope, and only the tools that read
        if not self.env["nudge.conversation"].with_user(self.admin_user)._can_develop():
            self.skipTest("Developer mode is closed on this database")
        _client, without = self.sign_in(login="oauth_admin", granted=[nudge_mcp.SCOPE_READ, nudge_mcp.SCOPE_WRITE])
        self.assertNotIn("read_view", self.tool_names(without["access_token"]))
        response = self.rpc(without["access_token"], "tools/call", {"name": "read_view", "arguments": {"model": "res.partner"}})
        self.assertEqual(response.status_code, 403)
        self.assertIn("nudge.developer", response.headers["WWW-Authenticate"])
        _client, developer = self.sign_in(login="oauth_admin", granted=list(nudge_mcp.SCOPES))
        names = self.tool_names(developer["access_token"])
        self.assertTrue(set(nudge_mcp.DEV_READ_TOOLS) <= names)
        for forbidden in ("prepare_automation", "prepare_server_action", "run_python", "run_sql", "diagnose_failure"):
            self.assertNotIn(forbidden, names)
        answer = self.rpc(developer["access_token"], "tools/call", {"name": "read_view", "arguments": {"model": "res.partner"}})
        self.assertFalse(answer.json()["result"]["isError"], answer.text)

    def test_legacy_keys_still_work(self):
        key = self.legacy_key(self.raghad)
        names = self.tool_names(key)
        self.assertTrue({"search_records", "prepare_create", "confirm"} <= names)
        self.assertFalse(names & set(nudge_mcp.DEV_READ_TOOLS))
        # A wrong key is told where to sign in instead
        response = self.rpc("0" * 40, "ping")
        self.assertEqual(response.status_code, 401)
        self.assertIn("resource_metadata=", response.headers["WWW-Authenticate"])

    # ------------------------------------------------------------------
    # Changes an assistant may confirm alone (SEC-03)
    # ------------------------------------------------------------------
    def prepare(self, access_token, name):
        answer = self.rpc(access_token, "tools/call", {"name": "prepare_create", "arguments": {
            "model": "res.partner", "values": [{"field": "name", "value": name}]}}).json()
        text = answer["result"]["content"][0]["text"]
        return text.split("token ")[1].split(" ")[0]

    def confirm(self, access_token, token):
        return self.rpc(access_token, "tools/call", {"name": "confirm", "arguments": {"token": token}}).json()

    def allow_alone(self, **values):
        return self.env["nudge.mcp.self.confirm"].create(dict({
            "tool": "prepare_create", "model_id": self.env.ref("base.model_res_partner").id}, **values))

    def test_self_confirm_is_off_by_default_and_per_change(self):
        client, tokens = self.sign_in()
        Partner = self.env["res.partner"]
        # Nothing allowed: the person's yes in Odoo is needed
        token = self.prepare(tokens["access_token"], "Ebrahim Waits")
        self.assertTrue(self.confirm(tokens["access_token"], token)["result"]["isError"])
        self.assertEqual(Partner.search_count([("name", "=", "Ebrahim Waits")]), 0)
        # Allowed for another assistant only: still waits
        other = self.env["nudge.mcp.client"].sudo().create({"name": "Other", "redirect_uris": REDIRECT})
        line = self.allow_alone(client_id=other.id)
        token = self.prepare(tokens["access_token"], "Ebrahim Other Client")
        self.assertTrue(self.confirm(tokens["access_token"], token)["result"]["isError"])
        # Allowed for this assistant: it confirms alone, and the log says so
        line.client_id = self.env["nudge.mcp.client"].sudo().search([("client_id", "=", client["client_id"])])
        token = self.prepare(tokens["access_token"], "Ebrahim Alone")
        answer = self.confirm(tokens["access_token"], token)
        self.assertFalse(answer["result"]["isError"], answer)
        self.assertEqual(Partner.search_count([("name", "=", "Ebrahim Alone")]), 1)
        step = self.env["nudge.step"].sudo().browse(int(token.split(".")[0]))
        self.assertTrue(step.mcp_self_confirmed)
        self.assertEqual(step.state, "done")
        # Another tool on the same model is not allowed by that line
        partner = Partner.search([("name", "=", "Ebrahim Alone")])
        answer = self.rpc(tokens["access_token"], "tools/call", {"name": "prepare_write", "arguments": {
            "model": "res.partner", "ids": [partner.id], "values": [{"field": "name", "value": "Ebrahim Renamed"}]}}).json()
        token = answer["result"]["content"][0]["text"].split("token ")[1].split(" ")[0]
        self.assertTrue(self.confirm(tokens["access_token"], token)["result"]["isError"])
        # A sensitive step never, whatever the list says
        token = self.prepare(tokens["access_token"], "Ebrahim Sensitive")
        self.env["nudge.step"].sudo().browse(int(token.split(".")[0])).is_sensitive = True
        self.assertTrue(self.confirm(tokens["access_token"], token)["result"]["isError"])
        self.assertEqual(Partner.search_count([("name", "=", "Ebrahim Sensitive")]), 0)
        # A key of before never confirms alone, even with a line for any assistant
        line.client_id = False
        key = self.legacy_key(self.raghad)
        token = self.prepare(key, "Ebrahim Legacy")
        self.assertTrue(self.confirm(key, token)["result"]["isError"])
        self.assertEqual(Partner.search_count([("name", "=", "Ebrahim Legacy")]), 0)
        # Nor may another assistant confirm what this one prepared
        _client2, tokens2 = self.sign_in(client_name="Ebrahim Assistant")
        token = self.prepare(tokens["access_token"], "Ebrahim Crossed")
        self.assertTrue(self.confirm(tokens2["access_token"], token)["result"]["isError"])
        self.assertEqual(Partner.search_count([("name", "=", "Ebrahim Crossed")]), 0)

    def test_self_confirm_refuses_sensitive_lines(self):
        SelfConfirm = self.env["nudge.mcp.self.confirm"]
        for model in ("account.move", "res.users", "res.groups", "ir.cron"):
            ir_model = self.env["ir.model"]._get(model)
            if not ir_model:
                continue
            with self.assertRaises(ValidationError):
                SelfConfirm.create({"tool": "prepare_write", "model_id": ir_model.id})
        partner_model = self.env.ref("base.model_res_partner").id
        with self.assertRaises(ValidationError):
            SelfConfirm.create({"tool": "call_method", "model_id": partner_model, "method": "action_archive"})
        with self.assertRaises(ValidationError):
            SelfConfirm.create({"tool": "call_method", "model_id": partner_model})
        with self.assertRaises(ValidationError):
            SelfConfirm.create({"tool": "prepare_write", "model_id": partner_model, "method": "write"})
        # Only administrators manage it
        with self.assertRaises(AccessError):
            SelfConfirm.with_user(self.raghad).create({"tool": "prepare_create", "model_id": partner_model})

    def test_self_confirm_follows_the_company(self):
        other_company = self.env["res.company"].create({"name": "Nudge OAuth Other Company"})
        client, tokens = self.sign_in()
        line = self.allow_alone(company_id=other_company.id)
        token = self.prepare(tokens["access_token"], "Ebrahim Company")
        # The line is of another company: the change, made in Raghad's company, waits for her
        self.assertTrue(self.confirm(tokens["access_token"], token)["result"]["isError"])
        self.assertEqual(self.env["res.partner"].search_count([("name", "=", "Ebrahim Company")]), 0)
        # An administrator of one company does not see the lines of another
        admin = new_test_user(self.env, login="oauth_admin_one", groups="base.group_system",
                              company_id=self.env.company.id, company_ids=[(6, 0, [self.env.company.id])])
        self.assertNotIn(line, self.env["nudge.mcp.self.confirm"].with_user(admin).search([]))
        line.company_id = self.env.company
        self.assertIn(line, self.env["nudge.mcp.self.confirm"].with_user(admin).search([]))
        token = self.prepare(tokens["access_token"], "Ebrahim Company")
        self.assertFalse(self.confirm(tokens["access_token"], token)["result"]["isError"])
        # The token works in the person's companies only: a contact of another company stays hidden
        hidden = self.env["res.partner"].create({"name": "Raghad Hidden Partner", "company_id": other_company.id})
        answer = self.rpc(tokens["access_token"], "tools/call", {"name": "search_records", "arguments": {
            "model": "res.partner", "conditions": [{"field": "name", "operator": "contains",
                                                    "value": "Raghad Hidden"}]}}).json()
        self.assertNotIn(hidden.name, answer["result"]["content"][0]["text"])

    # ------------------------------------------------------------------
    # Prompts (EXT-14)
    # ------------------------------------------------------------------
    def test_prompts(self):
        client, tokens = self.sign_in()
        initialize = self.rpc(tokens["access_token"], "initialize", {"protocolVersion": "2025-06-18"}).json()
        self.assertNotIn("prompts", initialize["result"]["capabilities"])
        prompts = [{"name": "chase", "title": "Chase a customer", "description": "Ask for a payment",
                    "arguments": [{"name": "customer", "required": True}, {"name": "amount"}],
                    "text": "Write to {customer} about {amount}."}]
        Mcp = type(self.env["nudge.mcp"])
        with patch.object(Mcp, "_mcp_prompts", lambda self: prompts):
            initialize = self.rpc(tokens["access_token"], "initialize", {"protocolVersion": "2025-06-18"}).json()
            self.assertIn("prompts", initialize["result"]["capabilities"])
            listing = self.rpc(tokens["access_token"], "prompts/list").json()["result"]["prompts"]
            self.assertEqual(listing[0]["name"], "chase")
            self.assertTrue(listing[0]["arguments"][0]["required"])
            got = self.rpc(tokens["access_token"], "prompts/get", {
                "name": "chase", "arguments": {"customer": "Raghad {amount}", "amount": "100"}}).json()
            # A value is put in as it is: what it holds is never read as a placeholder
            self.assertEqual(got["result"]["messages"][0]["content"]["text"], "Write to Raghad {amount} about 100.")
            missing = self.rpc(tokens["access_token"], "prompts/get", {"name": "chase", "arguments": {}}).json()
            self.assertEqual(missing["error"]["code"], nudge_mcp.INVALID_PARAMS)
            unknown = self.rpc(tokens["access_token"], "prompts/get", {"name": "nothing"}).json()
            self.assertEqual(unknown["error"]["code"], nudge_mcp.INVALID_PARAMS)

    def test_connected_assistants_in_preferences(self):
        self.sign_in()
        view = self.env["res.users"].with_user(self.raghad).get_views(
            [(self.env.ref("base.view_users_form_simple_modif").id, "form")])
        self.assertIn("nudge_mcp_grant_ids", view["views"]["form"]["arch"])
        values = self.raghad.with_user(self.raghad).read(["nudge_mcp_grant_ids"])[0]
        self.assertEqual(len(values["nudge_mcp_grant_ids"]), 1)
