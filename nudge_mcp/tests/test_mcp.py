import base64
import json
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.nudge.tools import base as tool_base
from odoo.addons.nudge_mcp.models import nudge_mcp

ENDPOINT = "/nudge/mcp"
CLIENT = "Test Client"


@tagged("post_install", "-at_install", "nudge_mcp")
class TestMcp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = "nudge_mcp.group_nudge_mcp,nudge.group_nudge_agent,base.group_partner_manager"
        cls.agent = new_test_user(cls.env, login="mcp_agent", groups=groups)
        cls.reader = new_test_user(
            cls.env, login="mcp_reader", groups="nudge_mcp.group_nudge_mcp,base.group_partner_manager")
        cls.no_group = new_test_user(cls.env, login="mcp_none", groups="nudge.group_nudge_agent")
        cls.other = new_test_user(cls.env, login="mcp_other", groups=groups)
        cls.key = cls.make_key(cls.agent)
        cls.reader_key = cls.make_key(cls.reader)
        cls.other_key = cls.make_key(cls.other)
        cls.raghad = cls.env["res.partner"].create({"name": "Raghad MCP Customer"})

    @classmethod
    def make_key(cls, user, scope=nudge_mcp.SCOPE, days=30):
        return cls.env["res.users.apikeys"].with_user(user).sudo()._generate(
            scope, "test", fields.Datetime.now() + timedelta(days=days))

    def setUp(self):
        super().setUp()
        self.params("True", "read", False)

    def params(self, enabled="True", exposure="read", writes=False):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param(nudge_mcp.ENABLED_PARAM, enabled)
        ICP.set_param(nudge_mcp.EXPOSURE_PARAM, exposure)
        self.env.company.nudge_mcp_allow_writes = writes

    def writes_on(self):
        self.params("True", "write", True)

    # ------------------------------------------------------------------
    def rpc(self, method, params=None, key=None, session=None, message_id=1, raw=None):
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if session:
            headers["Mcp-Session-Id"] = session
        body = raw if raw is not None else json.dumps(
            {"jsonrpc": "2.0", "id": message_id, "method": method, "params": params or {}})
        return self.url_open(ENDPOINT, data=body, headers=headers)

    def initialize(self, key=None):
        response = self.rpc("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": CLIENT, "version": "1"}}, key=key or self.key)
        self.assertEqual(response.status_code, 200)
        return response.headers["Mcp-Session-Id"]

    def call(self, name, arguments=None, key=None, session=None):
        response = self.rpc("tools/call", {"name": name, "arguments": arguments or {}},
                            key=key or self.key, session=session or self.session)
        self.assertEqual(response.status_code, 200)
        return response.json()

    @property
    def session(self):
        if not hasattr(self, "_session"):
            self._session = self.initialize()
        return self._session

    def text(self, answer):
        return answer["result"]["content"][0]["text"]

    def names(self, key=None):
        answer = self.rpc("tools/list", key=key or self.key, session=self.session).json()
        return {tool["name"] for tool in answer["result"]["tools"]}

    # ------------------------------------------------------------------
    # Door
    # ------------------------------------------------------------------
    def test_closed_by_default(self):
        self.params("False")
        response = self.rpc("initialize", key=self.key)
        self.assertEqual(response.status_code, 403)
        self.assertIn("closed", response.json()["error"]["message"])
        self.env["ir.config_parameter"].sudo().search([("key", "=", nudge_mcp.ENABLED_PARAM)]).unlink()
        self.assertEqual(self.rpc("initialize", key=self.key).status_code, 403)

    def test_keys_are_checked(self):
        response = self.rpc("initialize")
        self.assertEqual(response.status_code, 401)
        self.assertIn("Bearer", response.headers["WWW-Authenticate"])
        self.assertIn("resource_metadata=", response.headers["WWW-Authenticate"])
        self.assertEqual(self.rpc("initialize", key="0" * 40).status_code, 401)
        # A key of another scope does not open it; a person out of the group has none
        self.assertEqual(self.rpc("initialize", key=self.make_key(self.agent, scope="rpc")).status_code, 401)
        self.assertEqual(self.rpc("initialize", key=self.make_key(self.agent, scope=None)).status_code, 401)
        self.assertEqual(self.rpc("initialize", key=self.make_key(self.no_group)).status_code, 401)

    def test_expired_key(self):
        key = self.make_key(self.agent)
        self.env.cr.execute(
            "UPDATE res_users_apikeys SET expiration_date = now() at time zone 'utc' - interval '1 day' "
            "WHERE index = %s", [key[:8]])
        self.assertEqual(self.rpc("initialize", key=key).status_code, 401)

    def test_revoked_group(self):
        key = self.make_key(self.other)
        self.assertEqual(self.rpc("initialize", key=key).status_code, 200)
        self.other.group_ids = [(3, self.env.ref("nudge_mcp.group_nudge_mcp").id)]
        self.assertEqual(self.rpc("initialize", key=key).status_code, 401)

    def test_wrong_keys_are_slowed(self):
        from odoo.addons.nudge_mcp.controllers import mcp
        mcp._failures.clear()
        self.addCleanup(mcp._failures.clear)
        codes = [self.rpc("initialize", key="1" * 40).status_code for _index in range(mcp.MAX_FAILURES + 1)]
        self.assertEqual(codes[-1], 429)
        self.assertEqual(set(codes[:-1]), {401})

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------
    def test_initialize(self):
        response = self.rpc("initialize", {
            "protocolVersion": "2025-03-26", "clientInfo": {"name": CLIENT}}, key=self.key)
        result = response.json()["result"]
        self.assertEqual(result["protocolVersion"], "2025-03-26")
        self.assertEqual(result["serverInfo"]["name"], "Nudge")
        self.assertIn("tools", result["capabilities"])
        self.assertIn("resources", result["capabilities"])
        self.assertTrue(response.headers["Mcp-Session-Id"])
        # An unknown version is answered with ours
        answer = self.rpc("initialize", {"protocolVersion": "1999-01-01"}, key=self.key).json()
        self.assertEqual(answer["result"]["protocolVersion"], nudge_mcp.PROTOCOLS[0])

    def test_envelope(self):
        self.assertEqual(self.rpc("ping", key=self.key).json()["result"], {})
        self.assertEqual(self.rpc("nothing/here", key=self.key).json()["error"]["code"], nudge_mcp.METHOD_NOT_FOUND)
        notice = self.rpc("notifications/initialized", key=self.key, raw=json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(notice.status_code, 202)
        self.assertFalse(notice.content)
        self.assertEqual(self.rpc("ping", key=self.key, raw="{not json").status_code, 400)
        self.assertEqual(self.rpc("ping", key=self.key, raw=json.dumps({"id": 1})).json()["error"]["code"],
                         nudge_mcp.INVALID_REQUEST)
        batch = self.rpc("ping", key=self.key, raw=json.dumps([
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"}])).json()
        self.assertEqual([answer["id"] for answer in batch], [1, 2])
        self.assertEqual(self.url_open(ENDPOINT, headers={"Authorization": f"Bearer {self.key}"}).status_code, 405)

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------
    def test_tools_list_is_read_only_by_default(self):
        names = self.names()
        self.assertIn("search_records", names)
        self.assertIn("search_odoo_documentation", names)
        self.assertFalse(names & {"prepare_create", "prepare_write", "call_method", "confirm", "decline"})
        # The tools for developers, the code and the queries are never offered
        for forbidden in ("run_sql", "run_python", "prepare_automation", "prepare_server_action",
                          "prepare_delete", "read_automations", "prepare_view_change", "change_settings"):
            self.assertNotIn(forbidden, names)
        tool = next(tool for tool in self.rpc("tools/list", key=self.key).json()["result"]["tools"]
                    if tool["name"] == "search_records")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_tools_follow_the_rights_of_the_person(self):
        # A person who is not an Agent User has the documentation, not the records
        names = self.names(self.reader_key)
        self.assertIn("search_odoo_documentation", names)
        self.assertNotIn("search_records", names)

    def test_read_is_audited(self):
        answer = self.call("search_records", {"model": "res.partner", "conditions": [
            {"field": "name", "operator": "contains", "value": "Raghad MCP"}]})
        self.assertFalse(answer["result"]["isError"], self.text(answer))
        self.assertIn("Raghad MCP Customer", self.text(answer))
        self.assertIn("<data", self.text(answer))
        run = self.env["nudge.run"].sudo().search([("user_id", "=", self.agent.id)], order="id desc", limit=1)
        self.assertEqual(run.origin, f"MCP: {CLIENT}")
        self.assertEqual(run.state, "done")
        self.assertEqual((run.input_tokens, run.output_tokens, run.cost), (0, 0, 0))
        step = run.step_ids
        self.assertEqual((step.tool, step.state, step.kind, step.origin), ("search_records", "done", "read", f"MCP: {CLIENT}"))
        self.assertEqual(step.arguments["model"], "res.partner")
        self.assertIn(CLIENT, run.name)
        # The conversation that holds the log is archived: nobody chats in it
        self.assertFalse(run.conversation_id.active)

    def test_failed_calls_are_audited(self):
        answer = self.call("search_records", {"model": "not a model"})
        self.assertTrue(answer["result"]["isError"])
        run = self.env["nudge.run"].sudo().search([("user_id", "=", self.agent.id)], order="id desc", limit=1)
        self.assertEqual((run.state, run.step_ids.state), ("failed", "failed"))

    def test_unknown_tool_is_a_protocol_error(self):
        answer = self.call("run_sql", {"query": "select 1"})
        self.assertEqual(answer["error"]["code"], nudge_mcp.INVALID_PARAMS)
        self.assertEqual(self.call("search_records", None)["result"]["isError"], True)

    def test_record_rules_apply(self):
        # What the person cannot open is not found, whatever the assistant asks
        answer = self.call("search_records", {"model": "ir.cron"})
        self.assertTrue(answer["result"]["isError"])
        self.assertNotIn("Base: Auto-vacuum", self.text(answer))
        rule = self.env["ir.rule"].create({
            "name": "Hide Raghad", "model_id": self.env.ref("base.model_res_partner").id,
            "domain_force": "[('name', 'not ilike', 'Raghad MCP')]", "groups": [(4, self.env.ref("base.group_user").id)]})
        self.addCleanup(rule.unlink)
        answer = self.call("search_records", {"model": "res.partner", "conditions": [
            {"field": "name", "operator": "contains", "value": "Raghad"}]})
        self.assertNotIn("Raghad MCP Customer", self.text(answer))

    def test_key_of_a_person_never_gives_another_person(self):
        session = self.initialize(self.key)
        # The session of one is no session of the other: the client is not taken from it
        answer = self.rpc("tools/call", {"name": "search_odoo_documentation", "arguments": {"query": "invoice"}},
                          key=self.other_key, session=session).json()
        self.assertIn("result", answer)
        run = self.env["nudge.run"].sudo().search([("user_id", "=", self.other.id)], order="id desc", limit=1)
        self.assertEqual(run.user_id, self.other)
        self.assertNotEqual(run.origin, f"MCP: {CLIENT}")

    # ------------------------------------------------------------------
    # Changes
    # ------------------------------------------------------------------
    def prepare(self, name="Ebrahim MCP Contact"):
        return self.call("prepare_create", {"model": "res.partner", "values": [{"field": "name", "value": name}]})

    def token_of(self, answer):
        text = self.text(answer)
        return text.split("token ")[1].split(" ")[0]

    def step_of(self, token):
        return self.env["nudge.step"].sudo().browse(int(token.split(".")[0]))

    def approve(self, token, user=None):
        """The person says yes in Odoo"""
        self.step_of(token).with_user(user or self.agent).action_mcp_approve()

    def test_person_is_asked_in_odoo(self):
        self.writes_on()
        last = max(self.env["bus.bus"].sudo().search([]).ids or [0])
        token = self.token_of(self.prepare("Ebrahim Asked"))
        rows = self.env["bus.bus"].sudo().search([("id", ">", last)])
        self.assertIn("An external assistant wants", " ".join(rows.mapped("message")))
        step = self.step_of(token)
        # Nobody else can approve it
        with self.assertRaises(AccessError):
            step.with_user(self.other).action_mcp_approve()
        self.assertEqual(step.state, "pending")
        # Refused in Odoo: nothing can be confirmed
        step.with_user(self.agent).action_mcp_refuse()
        self.assertTrue(self.call("confirm", {"token": token})["result"]["isError"])
        self.assertEqual(self.env["res.partner"].search_count([("name", "=", "Ebrahim Asked")]), 0)

    def test_a_key_never_confirms_alone(self):
        """The company switch of before is gone: a key (legacy) waits for the
        person whatever the allow-list says, which is for OAuth sign-ins only"""
        self.writes_on()
        self.assertNotIn("nudge_mcp_self_confirm", self.env["res.company"]._fields)
        self.env["nudge.mcp.self.confirm"].create({
            "tool": "prepare_create", "model_id": self.env.ref("base.model_res_partner").id})
        token = self.token_of(self.prepare("Ebrahim Alone"))
        self.assertTrue(self.call("confirm", {"token": token})["result"]["isError"])
        self.assertEqual(self.env["res.partner"].search_count([("name", "=", "Ebrahim Alone")]), 0)
        self.approve(token)
        self.assertFalse(self.call("confirm", {"token": token})["result"]["isError"])
        self.assertEqual(self.env["res.partner"].search_count([("name", "=", "Ebrahim Alone")]), 1)
        self.assertFalse(self.step_of(token).mcp_self_confirmed)

    def test_writes_refused_when_off(self):
        # Exposure read
        self.assertEqual(self.prepare()["error"]["code"], nudge_mcp.INVALID_PARAMS)
        # Exposure write, the company has not allowed it
        self.params("True", "write", False)
        self.assertNotIn("prepare_create", self.names())
        self.assertEqual(self.prepare()["error"]["code"], nudge_mcp.INVALID_PARAMS)
        # Allowed, but the person is no Agent User
        self.writes_on()
        self.assertNotIn("prepare_create", self.names(self.reader_key))
        # Nudge itself may not act
        self.env["ir.config_parameter"].sudo().set_param("nudge.agent", "no")
        self.assertNotIn("prepare_create", self.names())
        self.assertEqual(self.env["res.partner"].search_count([("name", "=", "Ebrahim MCP Contact")]), 0)

    def test_write_waits_for_confirm(self):
        self.writes_on()
        names = self.names()
        self.assertTrue({"prepare_create", "prepare_write", "confirm", "decline"} <= names)
        answer = self.prepare()
        self.assertFalse(answer["result"]["isError"])
        self.assertIn("Nothing has been changed yet", self.text(answer))
        self.assertIn("Ebrahim MCP Contact", self.text(answer))
        token = self.token_of(answer)
        Partner = self.env["res.partner"]
        self.assertEqual(Partner.search_count([("name", "=", "Ebrahim MCP Contact")]), 0)
        run = self.env["nudge.run"].sudo().search([("user_id", "=", self.agent.id)], order="id desc", limit=1)
        self.assertEqual((run.state, run.step_ids.state), ("waiting", "pending"))
        # The assistant cannot say yes for the person
        refused = self.call("confirm", {"token": token})
        self.assertTrue(refused["result"]["isError"])
        self.assertIn("not approved", self.text(refused))
        self.assertEqual(Partner.search_count([("name", "=", "Ebrahim MCP Contact")]), 0)
        self.approve(token)

        done = self.call("confirm", {"token": token})
        self.assertFalse(done["result"]["isError"], self.text(done))
        partner = Partner.search([("name", "=", "Ebrahim MCP Contact")])
        self.assertEqual(len(partner), 1)
        # Made by the person, not by Nudge
        self.assertEqual(partner.create_uid, self.agent)
        step = self.env["nudge.step"].sudo().search([("tool", "=", "prepare_create")], order="id desc", limit=1)
        self.assertEqual((step.state, step.kind, step.origin), ("done", "create", f"MCP: {CLIENT}"))
        self.assertEqual(step.run_id.state, "done")
        self.assertTrue(step.decided_at)
        # In the log of the changes
        self.assertIn(step, self.env["nudge.step"].sudo().search([("kind", "in", ("create", "write", "method"))]))
        # A token is used once
        again = self.call("confirm", {"token": token})
        self.assertTrue(again["result"]["isError"])
        self.assertEqual(Partner.search_count([("name", "=", "Ebrahim MCP Contact")]), 1)

    def test_a_yes_given_after_leaving_the_company_is_refused(self):
        self.writes_on()
        token = self.token_of(self.prepare("Ebrahim Left Company"))
        self.approve(token)
        step = self.step_of(token)
        self.assertEqual(step.company_id, self.env.company)
        step.company_id = self.env["res.company"].create({"name": "Nudge MCP Other Company"})
        answer = self.call("confirm", {"token": token})
        self.assertTrue(answer["result"]["isError"])
        self.assertIn("no longer belong", self.text(answer))
        self.assertEqual(self.env["res.partner"].search_count([("name", "=", "Ebrahim Left Company")]), 0)

    def test_decline_and_bad_tokens(self):
        self.writes_on()
        token = self.token_of(self.prepare("Ebrahim Declined"))
        self.assertFalse(self.call("decline", {"token": token})["result"]["isError"])
        self.assertTrue(self.call("confirm", {"token": token})["result"]["isError"])
        self.assertEqual(self.env["res.partner"].search_count([("name", "=", "Ebrahim Declined")]), 0)
        step = self.env["nudge.step"].sudo().search([("tool", "=", "prepare_create")], order="id desc", limit=1)
        self.assertEqual(step.state, "declined")
        # A token is for the person who prepared the change, and nobody can write one
        token = self.token_of(self.prepare("Ebrahim Stolen"))
        self.assertTrue(self.call("confirm", {"token": token}, key=self.other_key)["result"]["isError"])
        forged = token.split(".")[0] + ".0123456789abcdef01234567"
        self.assertTrue(self.call("confirm", {"token": forged})["result"]["isError"])
        self.assertTrue(self.call("confirm", {"token": "nonsense"})["result"]["isError"])
        self.assertEqual(self.env["res.partner"].search_count([("name", "=", "Ebrahim Stolen")]), 0)

    def test_token_expires(self):
        self.writes_on()
        token = self.token_of(self.prepare("Ebrahim Late"))
        step = self.env["nudge.step"].sudo().browse(int(token.split(".")[0]))
        self.env.cr.execute(
            "UPDATE nudge_step SET create_date = now() at time zone 'utc' - interval '2 hours' WHERE id = %s", [step.id])
        step.invalidate_recordset()
        answer = self.call("confirm", {"token": token})
        self.assertTrue(answer["result"]["isError"])
        self.assertIn("expired", self.text(answer))
        self.assertEqual(self.env["res.partner"].search_count([("name", "=", "Ebrahim Late")]), 0)

    def test_confirm_rechecks_the_switches(self):
        self.writes_on()
        token = self.token_of(self.prepare("Ebrahim Switched Off"))
        self.params("True", "write", False)
        # The tool is no longer offered, and the old token opens nothing
        self.assertEqual(self.call("confirm", {"token": token})["error"]["code"], nudge_mcp.INVALID_PARAMS)
        self.assertEqual(self.env["res.partner"].search_count([("name", "=", "Ebrahim Switched Off")]), 0)

    # ------------------------------------------------------------------
    # Limits
    # ------------------------------------------------------------------
    def test_private_mode(self):
        self.env.company.nudge_private_mode = True
        # Records would leave the server for a remote assistant: refused
        answer = self.call("search_records", {"model": "res.partner"})
        self.assertIn("Private mode", answer["error"]["message"])
        # Unless the administrator says the assistant is on the server
        self.env["ir.config_parameter"].sudo().set_param(nudge_mcp.LOCAL_PARAM, "True")
        answer = self.call("search_records", {"model": "res.partner"})
        self.assertFalse(answer["result"]["isError"])
        # What would call a provider is refused
        tool = tool_base.TOOLS["search_odoo_documentation"]
        tool.uses_provider = True
        self.addCleanup(delattr, tool, "uses_provider")
        answer = self.call("search_odoo_documentation", {"query": "invoice"})
        self.assertTrue(answer["result"]["isError"])
        self.assertIn("Private mode", self.text(answer))
        run = self.env["nudge.run"].sudo().search([("user_id", "=", self.agent.id)], order="id desc", limit=1)
        self.assertEqual(run.state, "failed")

    # ------------------------------------------------------------------
    # The places of the line (NP-04): the calls share them with the panel
    # ------------------------------------------------------------------
    def places(self, *states, env=None):
        # ``env``: the one of the request, whose writes the environment of the test does not see before it ends
        return (env or self.env)["nudge.slot"].sudo().search([("state", "in", states or ("claimed", "streaming"))])

    def hold(self, user, provider=None):
        """A conversation of ``user`` that is answering: it holds a place"""
        values = {"user_id": user.id, "name": "Busy", "stream_started_at": fields.Datetime.now()}
        if provider:
            values["provider_id"] = provider.id
        return self.env["nudge.conversation"].sudo().create(values)

    def test_a_call_past_the_limit_is_told_to_come_back(self):
        self.env["ir.config_parameter"].sudo().set_param("nudge.max_streams", "1")
        self.hold(self.other)
        response = self.rpc("tools/call", {"name": "search_records", "arguments": {"model": "res.partner"}},
                            key=self.key, session=self.session)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Retry-After"], str(response.json()["error"]["data"]["retryAfter"]))
        error = response.json()["error"]
        self.assertEqual(error["code"], nudge_mcp.SERVER_ERROR)
        self.assertIn("answering other people", error["message"])
        self.assertEqual(error["data"]["reason"], "busy")
        self.assertGreaterEqual(error["data"]["retryAfter"], 1)
        self.assertFalse(self.places(), "A refused call holds no place")
        run = self.env["nudge.run"].sudo().search([("user_id", "=", self.agent.id)], order="id desc", limit=1)
        self.assertEqual(run.state, "failed")

    def test_calls_count_in_the_stream_cap_through_the_table(self):
        self.env["ir.config_parameter"].sudo().set_param("nudge.max_streams", "1")
        Mcp = self.env["nudge.mcp"].with_user(self.other)
        Mcp._mark_call(True)
        self.addCleanup(Mcp._mark_call, False)
        self.assertIn("error", self.call("search_records", {"model": "res.partner"}))
        Mcp._mark_call(False)
        self.assertFalse(self.call("search_records", {"model": "res.partner"})["result"]["isError"])

    def test_a_call_claims_a_place_and_gives_it_back(self):
        seen = []
        original = tool_base.call

        def spy(env, *args, **kwargs):
            seen.append(self.places(env=env).mapped("state"))
            return original(env, *args, **kwargs)
        self.patch(tool_base, "call", spy)
        self.assertFalse(self.call("search_records", {"model": "res.partner"})["result"]["isError"])
        self.assertEqual(seen, [["streaming"]], "One place while the tool works")
        self.assertFalse(self.places())
        self.assertEqual(self.env["nudge.slot"].sudo()._taken(), 0)
        slot = self.places("done")
        self.assertEqual(slot.user_id, self.agent)
        conversation = self.env["nudge.conversation"].sudo().with_context(active_test=False).search(
            [("user_id", "=", self.agent.id), ("name", "=", nudge_mcp.CONVERSATION_TITLE)])
        self.assertFalse(conversation.stream_started_at)

    def test_the_place_is_given_back_when_the_tool_fails(self):
        def refuse(env, *args, **kwargs):
            raise UserError("The tool failed.")
        self.patch(tool_base, "call", refuse)
        self.assertTrue(self.call("search_records", {"model": "res.partner"})["result"]["isError"])
        self.assertFalse(self.places())
        self.assertEqual(self.env["nudge.slot"].sudo()._taken(), 0)

        def crash(env, *args, **kwargs):
            raise ValueError("Unexpected")
        self.patch(tool_base, "call", crash)
        response = self.rpc("tools/call", {"name": "search_records", "arguments": {"model": "res.partner"}},
                            key=self.key, session=self.session)
        self.assertEqual(response.json()["error"]["code"], -32603)
        self.assertFalse(self.places())
        self.assertEqual(self.env["nudge.slot"].sudo()._taken(), 0)

    def test_stop_frees_the_place_of_a_call_at_once(self):
        stopped = []
        original = tool_base.call

        def stop_then_work(env, *args, **kwargs):
            slot = self.places(env=env)
            self.assertEqual(env["nudge.slot"].sudo()._taken(), 1)
            env["nudge.slot"]._stop(slot.mapped("conversation_ref"))
            stopped.append((slot.state, env["nudge.slot"].sudo()._taken()))
            return original(env, *args, **kwargs)
        self.patch(tool_base, "call", stop_then_work)
        self.call("search_records", {"model": "res.partner"})
        self.assertEqual(stopped, [("stopped", 0)])
        self.assertEqual(len(self.places("stopped")), 1, "Giving it back afterwards does not undo the stop")

    def test_no_place_is_held_while_the_person_decides(self):
        self.writes_on()
        self.env["ir.config_parameter"].sudo().set_param("nudge.max_streams", "1")
        token = self.token_of(self.prepare("Ebrahim Waiting Contact"))
        self.assertEqual(self.step_of(token).state, "pending")
        self.assertFalse(self.places(), "The change waits for the yes of the person, with no place")
        self.assertEqual(self.env["nudge.slot"].sudo()._taken(), 0)
        # Other calls and the panel are served meanwhile
        self.assertFalse(self.call("search_records", {"model": "res.partner"}, key=self.other_key)["result"]["isError"])
        self.approve(token)
        self.assertFalse(self.call("confirm", {"token": token})["result"]["isError"])
        self.assertFalse(self.places())

    def test_a_call_does_not_go_before_the_questions_waiting_in_line(self):
        self.env["ir.config_parameter"].sudo().set_param("nudge.max_streams", "1")
        now = fields.Datetime.now()
        waiting = self.env["nudge.slot"].sudo().create({
            "user_id": self.other.id, "state": "waiting", "waiting_since": now, "seen_at": now})
        response = self.rpc("tools/call", {"name": "search_records", "arguments": {"model": "res.partner"}},
                            key=self.key, session=self.session)
        self.assertEqual(response.json()["error"]["data"]["reason"], "busy")
        self.assertEqual(waiting.state, "ready", "The free place went to the first in line")

    def test_the_limit_of_the_provider_applies(self):
        provider = self.env["nudge.provider"].sudo().create({
            "name": "One at a time", "kind": "openai_compatible", "base_url": "https://llm.invalid/v1",
            "api_key": "sk-test-secret", "model_name": "test-model", "max_parallel": 1})
        self.env["nudge.provider"].sudo().search([("id", "!=", provider.id)]).active = False
        self.env["ir.config_parameter"].sudo().set_param("nudge.max_streams", "5")
        self.env["ir.config_parameter"].sudo().set_param("nudge.default_provider_id", str(provider.id))
        self.hold(self.other, provider)
        self.assertIn("error", self.call("search_records", {"model": "res.partner"}))
        self.env["nudge.conversation"].sudo().search([("name", "=", "Busy")]).unlink()
        self.assertNotIn("error", self.call("search_records", {"model": "res.partner"}))

    def test_calls_per_minute(self):
        self.env["ir.config_parameter"].sudo().set_param(nudge_mcp.CALLS_PARAM, "2")
        for _index in range(2):
            self.assertFalse(self.call("search_records", {"model": "res.partner"})["result"]["isError"])
        answer = self.call("search_records", {"model": "res.partner"})
        self.assertIn("Too many calls", answer["error"]["message"])
        # Another person is not slowed by it
        self.assertFalse(self.call("search_records", {"model": "res.partner"}, key=self.other_key)["result"]["isError"])

    def test_batch_is_capped(self):
        from odoo.addons.nudge_mcp.controllers import mcp
        pings = [{"jsonrpc": "2.0", "id": index, "method": "ping"} for index in range(mcp.MAX_BATCH)]
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"}
        ok = self.url_open(ENDPOINT, data=json.dumps(pings), headers=headers)
        self.assertEqual((ok.status_code, len(ok.json())), (200, mcp.MAX_BATCH))
        many = self.url_open(ENDPOINT, data=json.dumps(pings + pings[:1]), headers=headers)
        self.assertEqual(many.status_code, 400)

    def test_lockout_follows_the_key_id(self):
        from odoo.addons.nudge_mcp.controllers import mcp
        mcp._failures.clear()
        self.addCleanup(mcp._failures.clear)
        # Wrong keys that start alike lock that key id, not the address
        codes = [self.rpc("initialize", key="2" * 8 + str(index) * 32).status_code
                 for index in range(mcp.MAX_FAILURES + 1)]
        self.assertEqual(codes[-1], 429)
        # A person with a right key, from the same address, is not shut out
        self.assertEqual(self.rpc("initialize", key=self.key).status_code, 200)
        self.assertEqual(self.rpc("initialize", key="3" * 40).status_code, 401)

    def test_read_results_are_kept_short(self):
        for index in range(30):
            self.env["res.partner"].create({"name": f"Raghad Long Name Customer Number {index}"})
        answer = self.call("search_records", {"model": "res.partner", "conditions": [
            {"field": "name", "operator": "contains", "value": "Raghad Long Name"}]})
        self.assertFalse(answer["result"]["isError"], self.text(answer))
        self.assertGreater(len(self.text(answer)), 500)
        step = self.env["nudge.step"].sudo().search([("tool", "=", "search_records")], order="id desc", limit=1)
        self.assertTrue(step.result)
        self.assertLessEqual(len(step.result), nudge_mcp.RESULT_PREVIEW)

    def test_budget(self):
        self.env["ir.config_parameter"].sudo().set_param("nudge.budget_user_tokens", "100")
        self.env["nudge.usage"]._add(
            "chat", {"input_tokens": 500}, 0.0, self.agent, self.env.company)
        answer = self.call("search_records", {"model": "res.partner"})
        self.assertTrue(answer["result"]["isError"])
        self.assertIn("tokens", self.text(answer))

    # ------------------------------------------------------------------
    # Resources
    # ------------------------------------------------------------------
    def test_resources(self):
        page = self.env["nudge.doc.page"].sudo().create({
            "path": "mcp/raghad.rst", "url": "https://example.invalid/raghad", "category": "applications",
            "app": "contacts", "title": "Raghad Guide", "description": "How Raghad works"})
        self.env["nudge.doc.passage"].sudo().create({
            "page_id": page.id, "sequence": 1, "heading": "Start", "body": "Ebrahim opens the contact."})
        uris, cursor = {}, None
        while True:
            listing = self.rpc("resources/list", {"cursor": cursor} if cursor else {}, key=self.key).json()["result"]
            self.assertLessEqual(len(listing["resources"]), nudge_mcp.PAGE_SIZE)
            uris.update({resource["uri"]: resource for resource in listing["resources"]})
            cursor = listing.get("nextCursor")
            if not cursor:
                break
        self.assertIn(f"nudge://doc/{page.id}", uris)
        self.assertEqual(uris[f"nudge://doc/{page.id}"]["name"], "Raghad Guide")
        read = self.rpc("resources/read", {"uri": f"nudge://doc/{page.id}"}, key=self.key, session=self.session).json()
        self.assertIn("Ebrahim opens the contact.", read["result"]["contents"][0]["text"])
        bad = self.rpc("resources/read", {"uri": "file:///etc/passwd"}, key=self.key).json()
        self.assertEqual(bad["error"]["code"], nudge_mcp.INVALID_PARAMS)
        missing = self.rpc("resources/read", {"uri": "nudge://doc/99999999"}, key=self.key).json()
        self.assertEqual(missing["error"]["code"], nudge_mcp.INVALID_PARAMS)
        run = self.env["nudge.run"].sudo().search([("user_id", "=", self.agent.id)], order="id desc", limit=1)
        self.assertEqual((run.origin, run.state), (f"MCP: {CLIENT}", "done"))

    def test_company_files_follow_their_groups(self):
        source = self.env["nudge.source"].sudo().create({
            "name": "Leave Policy Raghad", "file_name": "leave.txt",
            "file": base64.b64encode(b"Twenty days.")})
        self.assertEqual(source.state, "ready")
        listed = {resource["uri"] for resource in self.rpc("resources/list", key=self.key).json()["result"]["resources"]}
        self.assertIn(f"nudge://file/{source.id}", listed)
        read = self.rpc("resources/read", {"uri": f"nudge://file/{source.id}"}, key=self.key).json()
        self.assertIn("Twenty days.", read["result"]["contents"][0]["text"])
        # Closed to a group the person is not in
        source.group_ids = [(6, 0, [self.env.ref("base.group_erp_manager").id])]
        listed = {resource["uri"] for resource in self.rpc("resources/list", key=self.key).json()["result"]["resources"]}
        self.assertNotIn(f"nudge://file/{source.id}", listed)
        read = self.rpc("resources/read", {"uri": f"nudge://file/{source.id}"}, key=self.key).json()
        self.assertEqual(read["error"]["code"], nudge_mcp.INVALID_PARAMS)

    # ------------------------------------------------------------------
    # Keys and the page of help
    # ------------------------------------------------------------------
    def test_key_wizard(self):
        Wizard = self.env["nudge.mcp.key"]
        wizard = Wizard.with_user(self.agent).create({"name": "Claude Desktop", "duration": "30"})
        wizard.action_create()
        self.assertEqual(len(wizard.key), 40)
        self.assertEqual(self.rpc("initialize", key=wizard.key).status_code, 200)
        row = self.env["res.users.apikeys"].sudo().search([("user_id", "=", self.agent.id), ("name", "=", "Claude Desktop")])
        self.assertEqual(row.scope, nudge_mcp.SCOPE)
        self.assertTrue(row.expiration_date)
        # Only the group makes keys; and only when Nudge is open
        with self.assertRaises(AccessError):
            Wizard.with_user(self.no_group).create({"name": "Nope"}).action_create()
        self.params("False")
        with self.assertRaises(UserError):
            Wizard.with_user(self.agent).create({"name": "Closed"}).action_create()

    def test_help_page_and_test_button(self):
        page = self.env["nudge.mcp.help"].with_user(self.agent).create({})
        self.assertTrue(page.server_url.endswith("/nudge/mcp"))
        self.assertIn("mcp-remote", page.claude_config)
        self.assertIn(page.server_url, page.claude_config)
        # Signed in with OAuth: no key in the configurations, only in the one of keys (legacy)
        self.assertNotIn("Authorization", page.claude_config + page.generic_config)
        self.assertIn(page.server_url, page.claude_code_command)
        self.assertIn("Authorization", page.legacy_config)
        page.test_key = self.key
        page.action_test()
        self.assertIn("It works", page.test_result)
        self.assertFalse(page.test_key)
        page.test_key = self.other_key
        page.action_test()
        self.assertIn("somebody else", page.test_result)
        page.test_key = "0" * 40
        page.action_test()
        self.assertIn("not valid", page.test_result)
        self.params("False")
        page.action_test()
        self.assertIn("closed", page.test_result)

    def test_settings(self):
        self.env["ir.config_parameter"].sudo().search([("key", "=", nudge_mcp.EXPOSURE_PARAM)]).unlink()
        settings = self.env["res.config.settings"].create({
            "nudge_mcp_enabled": True, "nudge_mcp_exposure": "write", "nudge_mcp_allow_writes": True})
        settings.execute()
        Mcp = self.env["nudge.mcp"]
        self.assertTrue(Mcp._is_enabled())
        self.assertEqual(Mcp._exposure(), "write")
        self.assertTrue(self.env.company.nudge_mcp_allow_writes)
        self.env["res.config.settings"].create({"nudge_mcp_enabled": False}).execute()
        self.assertFalse(Mcp._is_enabled())
