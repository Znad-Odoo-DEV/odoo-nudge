"""The sign-in of a connector: authorization code with PKCE, one token per person. Fake HTTP only."""
import json
import logging
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from odoo import fields
from odoo.exceptions import AccessError, LockError, UserError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools as nudge_tools

from ..models import nudge_connector_oauth as oauth
from ..tools import connectors
from . import fake_mcp
from .test_connect import READ, ConnectCase, asking, call, saying

BASE = "https://odoo.example.com"


class SignInCase(ConnectCase):

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("web.base.url", BASE)
        self.other = new_test_user(
            self.env, login="nudge_g2", groups="nudge.group_nudge_agent,base.group_partner_manager")
        origin = self.server.url[:-len("/mcp")]
        self.signin = self.env["nudge.connector"].create({
            "name": "Calendar of Raghad", "key": "cal", "url": self.server.url, "runs_locally": True,
            "oauth_client_id": fake_mcp.CLIENT_ID, "oauth_client_secret": fake_mcp.CLIENT_SECRET,
            "oauth_authorize_url": origin + "/authorize", "oauth_token_url": origin + "/token",
            "oauth_scope": "contacts.read",
        })
        self.signin.action_sync_tools()
        self.signin.tool_ids.filtered(lambda tool: tool.remote_name == "search_contacts").access = "read"
        self.signin.write({"auth_type": "oauth_user", "state": "active"})
        self.read_name = "cx_cal__search_contacts"
        self.server.answers["search_contacts"] = lambda arguments, headers: fake_mcp.text_result(
            f"Raghad (asked with {headers.get('authorization')})")

    def begin(self, user):
        url = self.signin.with_user(user)._oauth_begin()
        parts = urlsplit(url)
        return url, {key: values[0] for key, values in parse_qs(parts.query).items()}

    def sign_in(self, user, connector=None):
        connector = connector or self.signin
        query = {key: values[0] for key, values in parse_qs(
            urlsplit(connector.with_user(user)._oauth_begin()).query).items()}
        code = self.server.code_for(query["code_challenge"], query["redirect_uri"])
        return self.env["nudge.connector.oauth.state"].with_user(user)._complete(query["state"], code)

    def ask(self, user):
        env = self.env(user=user)
        connectors.sync(env)
        return nudge_tools.base.call(env, self.read_name, {"query": "Ra"}, None, "agent")

    def tokens(self, user=None):
        domain = [("connector_id", "=", self.signin.id)] + ([("user_id", "=", user.id)] if user else [])
        return self.env["nudge.connector.token"].sudo().search(domain)



@tagged("post_install", "-at_install", "nudge_connect")
class TestConnectorSignIn(SignInCase, TransactionCase):

    def test_the_sign_in_address(self):
        url, query = self.begin(self.agent)
        self.assertTrue(url.startswith(self.server.url[:-len("/mcp")] + "/authorize?"))
        self.assertEqual(query["response_type"], "code")
        self.assertEqual(query["code_challenge_method"], "S256")
        self.assertEqual(query["redirect_uri"], BASE + "/nudge_connect/oauth/callback")
        self.assertEqual((query["client_id"], query["scope"], query["resource"]),
                         (fake_mcp.CLIENT_ID, "contacts.read", self.server.url))
        State = self.env["nudge.connector.oauth.state"].sudo()
        row = State.search([("state_hash", "=", State._hash(query["state"]))])
        self.assertEqual((row.user_id, row.connector_id), (self.agent, self.signin))
        self.assertEqual(oauth.pkce_challenge(row.verifier), query["code_challenge"])
        self.assertNotIn(row.verifier, url, "The verifier does not leave but for the token request")
        self.assertNotEqual(row.state_hash, query["state"], "Only the hash of the state is kept")
        self.assertLessEqual(row.expires, fields.Datetime.now() + timedelta(minutes=oauth.STATE_MINUTES))
        self.assertNotIn(fake_mcp.CLIENT_SECRET, url)

    def test_a_token_per_person(self):
        self.sign_in(self.agent)
        self.sign_in(self.other)
        self.assertEqual(len(self.tokens()), 2)
        first, second = self.ask(self.agent), self.ask(self.other)
        asked = [request["headers"]["authorization"] for request in self.server.calls()]
        self.assertEqual(len(asked), 2)
        self.assertNotEqual(asked[0], asked[1])
        self.assertEqual(asked[0], "Bearer " + self.tokens(self.agent).access_token)
        self.assertEqual(asked[1], "Bearer " + self.tokens(self.other).access_token)
        self.assertFalse(first.is_error or second.is_error)
        with self.assertRaises(AccessError):
            self.env["nudge.connector.token"].with_user(self.agent).search([])

    def test_a_state_is_used_once(self):
        _url, query = self.begin(self.agent)
        State = self.env["nudge.connector.oauth.state"].with_user(self.agent)
        code = self.server.code_for(query["code_challenge"], query["redirect_uri"])
        State._complete(query["state"], code)
        with self.assertRaises(UserError):
            State._complete(query["state"], self.server.code_for(query["code_challenge"], query["redirect_uri"]))
        with self.assertRaises(UserError):
            State._complete("never-given", code)

    def test_a_state_runs_out(self):
        _url, query = self.begin(self.agent)
        State = self.env["nudge.connector.oauth.state"].sudo()
        State.search([("state_hash", "=", State._hash(query["state"]))]).expires = (
            fields.Datetime.now() - timedelta(seconds=1))
        with self.assertRaises(UserError):
            State.with_user(self.agent)._complete(
                query["state"], self.server.code_for(query["code_challenge"], query["redirect_uri"]))
        self.assertFalse(self.tokens())

    def test_the_callback_of_another_person_is_refused(self):
        _url, query = self.begin(self.agent)
        code = self.server.code_for(query["code_challenge"], query["redirect_uri"])
        with self.assertRaises(UserError):
            self.env["nudge.connector.oauth.state"].with_user(self.other)._complete(query["state"], code)
        self.assertFalse(self.tokens(), "Nobody was signed in")
        # The refusal rolls the request back: the person it belongs to can still finish
        self.env["nudge.connector.oauth.state"].with_user(self.agent)._complete(query["state"], code)
        self.assertEqual(self.tokens().user_id, self.agent)

    def test_the_tokens_go_to_the_connector_of_the_state_only(self):
        origin = self.server.url[:-len("/mcp")]
        twin = self.env["nudge.connector"].create({
            "name": "Twin", "key": "twin", "url": self.server.url, "runs_locally": True, "auth_type": "oauth_user",
            "oauth_client_id": fake_mcp.CLIENT_ID, "oauth_authorize_url": origin + "/authorize",
            "oauth_token_url": origin + "/token", "state": "active"})
        self.assertEqual(self.sign_in(self.agent), self.signin)
        self.assertFalse(self.env["nudge.connector.token"].sudo().search([("connector_id", "=", twin.id)]))
        with self.assertRaises(UserError):
            self.connector.with_user(self.agent)._oauth_begin()  # a connector with no sign-in

    def test_a_code_not_bound_to_the_verifier_is_refused(self):
        _url, query = self.begin(self.agent)
        code = self.server.code_for("someone-elses-challenge", query["redirect_uri"])
        with self.assertRaises(UserError):
            self.env["nudge.connector.oauth.state"].with_user(self.agent)._complete(query["state"], code)
        self.assertFalse(self.tokens())

    def test_the_return_address_is_never_taken_from_a_request(self):
        self.sign_in(self.agent)
        exchanged = self.server.calls("token")[-1]["body"]
        self.assertEqual(exchanged["redirect_uri"], BASE + "/nudge_connect/oauth/callback")
        self.assertEqual(exchanged["resource"], self.server.url)
        self.assertEqual(exchanged["grant_type"], "authorization_code")
        env = self.env(user=self.agent, context={"redirect_uri": "https://evil.example.com/steal"})
        _url, query = (None, {key: values[0] for key, values in parse_qs(
            urlsplit(self.signin.with_env(env)._oauth_begin()).query).items()})
        self.assertEqual(query["redirect_uri"], BASE + "/nudge_connect/oauth/callback")

    def test_an_address_the_server_names_must_be_usable(self):
        self.server.bad_endpoint = "http://evil.example.com/authorize"
        self.signin.write({"oauth_authorize_url": False, "oauth_token_url": False})
        result = self.signin.action_discover_oauth()
        self.assertEqual(result["params"]["type"], "danger")
        self.assertFalse(self.signin.oauth_authorize_url, "A plain-HTTP address of another host is not kept")
        with self.assertRaises(UserError):
            self.signin.oauth_authorize_url = "http://evil.example.com/authorize"

    def test_discovery_and_registration(self):
        origin = self.server.url[:-len("/mcp")]
        notion = self.env["nudge.connector"].create({
            "name": "Notes of Ebrahim", "key": "notes", "url": self.server.url, "runs_locally": True,
            "auth_type": "oauth_user"})
        result = notion.action_discover_oauth()
        self.assertEqual(result["params"]["type"], "success", result["params"]["message"])
        self.assertEqual((notion.oauth_authorize_url, notion.oauth_token_url, notion.oauth_registration_url),
                         (origin + "/authorize", origin + "/token", origin + "/register"))
        self.assertEqual(notion.sudo().oauth_client_id, "registered-client-raghad")
        self.assertEqual(self.server.registered[0]["redirect_uris"], [BASE + "/nudge_connect/oauth/callback"])
        with self.assertRaises(AccessError):
            notion.with_user(self.agent).action_discover_oauth()

    def test_no_token_means_sign_in_first_never_another_persons(self):
        self.sign_in(self.other)
        result = self.ask(self.agent)
        self.assertTrue(result.is_error)
        self.assertIn("Sign in to Calendar of Raghad first", result.content)
        self.assertIn(BASE + f"/nudge_connect/oauth/start/{self.signin.id}", result.content)
        self.assertFalse(self.server.calls(), "Nothing was sent, and not with the other person's token")
        self.assertNotIn(self.tokens(self.other).access_token, result.content)

    def test_the_token_is_renewed(self):
        self.server.expires_in = 1
        self.sign_in(self.agent)
        old = self.tokens(self.agent).access_token
        self.assertFalse(self.ask(self.agent).is_error)
        new = self.tokens(self.agent).access_token
        self.assertNotEqual(old, new)
        self.assertEqual(self.server.calls("token")[-1]["body"]["grant_type"], "refresh_token")
        self.assertEqual(self.server.calls()[-1]["headers"]["authorization"], "Bearer " + new)

    def test_a_renewal_in_progress_elsewhere_does_not_cost_the_token(self):
        self.sign_in(self.agent)
        row = self.tokens(self.agent)
        old = row.access_token
        row.expires = fields.Datetime.now() + timedelta(seconds=5)
        refreshes = len(self.server.calls("token"))
        held = patch.object(type(row), "lock_for_update", side_effect=LockError("held"))
        with held:
            result = self.ask(self.agent)
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(self.server.calls()[-1]["headers"]["authorization"], "Bearer " + old)
        self.assertEqual(len(self.server.calls("token")), refreshes, "The refresh token is not sent twice")
        self.assertEqual(self.tokens(self.agent), row)
        # An ended token cannot be reused: the person is asked again, and the token is still there
        row.expires = fields.Datetime.now() - timedelta(seconds=5)
        with held:
            result = self.ask(self.agent)
        self.assertTrue(result.is_error)
        self.assertNotIn("Sign in to", result.content)
        self.assertEqual(self.tokens(self.agent), row)
        self.assertEqual(len(self.server.calls("token")), refreshes)

    def test_a_token_renewed_while_waiting_for_the_lock_is_used_as_it_is(self):
        self.sign_in(self.agent)
        row = self.tokens(self.agent)
        row.expires = fields.Datetime.now() + timedelta(seconds=5)
        refreshes = len(self.server.calls("token"))
        self.server.tokens.add("access-renewed-elsewhere")

        def renewed(this, **kwargs):
            this.write({"access_token": "access-renewed-elsewhere", "expires": fields.Datetime.now() + timedelta(hours=1)})

        with patch.object(type(row), "lock_for_update", renewed):
            result = self.ask(self.agent)
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(self.server.calls()[-1]["headers"]["authorization"], "Bearer access-renewed-elsewhere")
        self.assertEqual(len(self.server.calls("token")), refreshes)

    def test_a_token_that_cannot_be_renewed_asks_for_a_sign_in(self):
        self.server.expires_in = 1
        self.sign_in(self.agent)
        self.server.refreshes.clear()
        result = self.ask(self.agent)
        self.assertIn("Sign in to Calendar of Raghad first", result.content)
        self.assertFalse(self.tokens(self.agent), "A refused refresh forgets the token")

    def test_sign_out_and_revoke(self):
        self.sign_in(self.agent)
        self.sign_in(self.other)
        self.signin.with_user(self.agent).action_sign_out()
        self.assertFalse(self.tokens(self.agent))
        self.assertTrue(self.tokens(self.other))
        self.assertIn("Sign in to", self.ask(self.agent).content)
        with self.assertRaises(AccessError):
            self.signin.with_user(self.agent).action_revoke_all()
        self.signin.action_revoke_all()
        self.assertFalse(self.tokens())
        self.assertIn("Sign in to", self.ask(self.other).content)

    def test_a_change_of_the_client_signs_everybody_out(self):
        self.sign_in(self.agent)
        self.signin.oauth_client_id = "another-client"
        self.assertFalse(self.tokens())

    def test_tokens_reach_neither_the_model_nor_the_log(self):
        self.sign_in(self.agent)
        token = self.tokens(self.agent).access_token
        refresh = self.tokens(self.agent).refresh_token
        adapter = self.use_adapter(script=[asking(call(self.read_name, {"query": "Ra"})), saying("Done.")])

        class Keep(logging.Handler):
            lines = []

            def emit(self, record):
                self.lines.append(record.getMessage())

        keep = Keep()
        keep.lines = []
        logging.getLogger().addHandler(keep)
        self.addCleanup(logging.getLogger().removeHandler, keep)
        self.order(self.agent, "Who are the contacts of the calendar?")
        shown = json.dumps(adapter.calls, default=str)
        self.assertNotIn(token, shown)
        self.assertNotIn(refresh, shown)
        self.assertIn("Raghad (asked with Bearer ***", shown, "The token the server echoed is taken out")
        for call_row in self.env["nudge.connector.call"].search([("connector_id", "=", self.signin.id)]):
            self.assertNotIn(token, json.dumps([call_row.arguments, call_row.error]))
        self.assertNotIn(token, "\n".join(keep.lines))
        self.assertNotIn(refresh, "\n".join(keep.lines))
        self.assertNotIn(token, self.signin._failure(oauth.mcp_client.McpError("remote", f"bad {token}")))

    def test_a_company_the_connector_is_not_open_to(self):
        other_company = self.env["res.company"].create({"name": "Your company, branch",
                                                        "nudge_connect_read_only_first_month": False})
        self.agent.write({"company_ids": [(4, other_company.id)]})
        self.sign_in(self.agent)
        outside = self.env(user=self.agent, context={"allowed_company_ids": [other_company.id]})
        with self.assertRaises(UserError):
            self.signin.with_env(outside)._oauth_begin()
        connectors.sync(outside)
        result = nudge_tools.base.call(outside, self.read_name, {"query": "Ra"}, None, "agent")
        self.assertTrue(result.is_error or "no tool named" in result.content)
        self.assertFalse(self.server.calls(), "Nothing was sent from a company the connector is not open to")
        self.signin.company_ids = [(4, other_company.id)]
        self.assertFalse(nudge_tools.base.call(outside, self.read_name, {"query": "Ra"}, None, "agent").is_error)

    def test_the_presets_that_sign_in(self):
        from .. import presets
        self.assertNotIn("teams", presets.BY_ID)
        for name in ("gcalendar", "gmail", "notion", "slack"):
            self.assertEqual(presets.BY_ID[name]["auth"], "oauth_user", name)
        self.assertEqual(presets.BY_ID["github"]["auth"], "bearer")
        self.assertIn("Developer Preview", presets.BY_ID["gmail"]["label"])
        connector = self.env["nudge.connector"].new({"preset": "gcalendar"})
        connector._onchange_preset()
        self.assertEqual((connector.auth_type, connector.oauth_token_url),
                         ("oauth_user", "https://oauth2.googleapis.com/token"))
        self.assertIn("calendar", connector.oauth_scope)

    def test_sign_out_tells_the_provider_when_it_can(self):
        self.signin.write({"oauth_revocation_url": self.server.url[:-len("/mcp")] + "/revoke"})
        self.sign_in(self.agent)
        refresh = self.tokens(self.agent).refresh_token
        self.signin.with_user(self.agent).action_sign_out()
        self.assertEqual(self.server.revoked, [refresh])
        self.assertEqual(self.server.calls("revoke")[0]["body"]["token_type_hint"], "refresh_token")
        self.assertFalse(self.tokens(self.agent))

    def test_a_provider_that_fails_to_revoke_does_not_stop_the_sign_out(self):
        self.signin.write({"oauth_revocation_url": "http://127.0.0.1:1/revoke"})
        self.sign_in(self.agent)
        with self.assertLogs("odoo.addons.nudge_connect.models.nudge_connector_oauth", "WARNING") as logged:
            self.signin.with_user(self.agent).action_sign_out()
        self.assertFalse(self.tokens(self.agent))
        self.assertNotIn("secret", "\n".join(logged.output))

    def test_the_status_of_the_person(self):
        self.assertEqual(self.signin.with_env(self.env(user=self.agent)).sudo().sign_in_status, "Not signed in")
        self.sign_in(self.agent)
        self.env.invalidate_all()
        status = self.signin.with_env(self.env(user=self.agent)).sudo().sign_in_status
        self.assertTrue(status.startswith("Signed in as of "), status)
        self.assertEqual(self.signin.with_env(self.env(user=self.other)).sudo().sign_in_status, "Not signed in")


@tagged("post_install", "-at_install", "nudge_connect")
class TestConnectorSignInHttp(SignInCase, HttpCase):
    """The real callback route, over HTTP; the token address is the fake server of the tests"""

    def setUp(self):
        super().setUp()
        self.boss = new_test_user(
            self.env, login="nudge_boss", groups="base.group_system,nudge.group_nudge_agent")

    def callback(self, user, state, code="x", **extra):
        self.authenticate(user.login, user.login)
        query = urlencode(dict({"state": state, "code": code}, **extra))
        return self.url_open("/nudge_connect/oauth/callback?" + query, allow_redirects=False)

    def started(self, user, connector=None):
        connector = connector or self.signin
        query = {key: values[0] for key, values in parse_qs(
            urlsplit(connector.with_user(user)._oauth_begin()).query).items()}
        return query["state"], self.server.code_for(query["code_challenge"], query["redirect_uri"])

    def test_the_right_person_with_a_valid_state(self):
        state, code = self.started(self.boss)
        response = self.callback(self.boss, state, code)
        self.assertEqual(response.status_code, 303)
        self.assertIn(f"id={self.signin.id}", response.headers["Location"])
        self.assertIn("model=nudge.connector", response.headers["Location"])
        self.assertEqual(self.tokens(self.boss).user_id, self.boss)
        # A person who cannot open the form is told, not sent to it
        state, code = self.started(self.agent)
        page = self.callback(self.agent, state, code)
        self.assertEqual(page.status_code, 200)
        self.assertIn("You are signed in to Calendar of Raghad", page.text)
        self.assertTrue(self.tokens(self.agent))

    def test_a_return_address_in_the_request_is_ignored(self):
        state, code = self.started(self.agent)
        response = self.callback(self.agent, state, code, redirect_uri="https://evil.example.com/steal")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("evil.example.com", response.text)
        self.assertEqual(self.server.calls("token")[-1]["body"]["redirect_uri"], BASE + "/nudge_connect/oauth/callback")

    def test_another_person_is_refused_and_the_state_stays_valid_for_its_owner(self):
        state, code = self.started(self.agent)
        refused = self.callback(self.other, state, code)
        self.assertEqual(refused.status_code, 400)
        self.assertIn("begun by someone else", refused.text)
        self.assertFalse(self.tokens())
        accepted = self.callback(self.agent, state, code)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(self.tokens().user_id, self.agent)

    def test_a_state_that_ran_out_is_refused(self):
        state, code = self.started(self.agent)
        State = self.env["nudge.connector.oauth.state"].sudo()
        State.search([("state_hash", "=", State._hash(state))]).expires = fields.Datetime.now() - timedelta(seconds=1)
        self.assertEqual(self.callback(self.agent, state, code).status_code, 400)
        self.assertFalse(self.tokens())

    def test_a_state_is_used_once(self):
        state, code = self.started(self.agent)
        self.assertEqual(self.callback(self.agent, state, code).status_code, 200)
        again = self.callback(self.agent, state, self.server.code_for("c", BASE + "/nudge_connect/oauth/callback"))
        self.assertEqual(again.status_code, 400)
        self.assertEqual(len(self.tokens()), 1)

    def test_a_state_of_a_connector_that_no_longer_signs_in_is_refused(self):
        state, code = self.started(self.agent)
        self.signin.write({"auth_type": "bearer", "bearer_token": "sk-connector-raghad-7f3a9c"})
        refused = self.callback(self.agent, state, code)
        self.assertEqual(refused.status_code, 400)
        self.assertIn("no longer uses a sign-in", refused.text)
        self.assertFalse(self.env["nudge.connector.token"].sudo().search([]))

    def test_a_state_nobody_gave_is_refused(self):
        self.assertEqual(self.callback(self.agent, "made-up", "x").status_code, 400)
        self.assertEqual(self.callback(self.agent, "", "").status_code, 400)

    def test_the_provider_saying_no_spends_the_state(self):
        state, code = self.started(self.agent)
        response = self.callback(self.agent, state, code="", error="access_denied")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.callback(self.agent, state, code).status_code, 400)
        self.assertFalse(self.tokens())
