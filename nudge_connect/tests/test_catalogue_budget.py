"""S3 and S4: the catalogue of connectors, their calls in the usage ledger and budgets, and the answers
kept for a while."""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import Form, TransactionCase, tagged

from odoo.addons.nudge import tools as nudge_tools

from .. import mcp_client, presets
from ..models import nudge_connector
from ..tools import connectors
from . import fake_mcp
from .test_connect import INJECTION, READ, ConnectCase, asking, call, saying

SECRET = "sk-connector-raghad-7f3a9c"


def groups_field(model):
    return "all_group_ids" if "all_group_ids" in model._fields else "groups_id"


def group_write_field(model):
    return "group_ids" if "group_ids" in model._fields else "groups_id"


@tagged("post_install", "-at_install", "nudge_connect")
class TestConnectorCatalogue(ConnectCase, TransactionCase):

    def make(self, key, preset, **values):
        return self.env["nudge.connector"].create(dict(
            {"name": f"Sister {key}", "key": key, "url": self.server.url, "runs_locally": True,
             "preset": preset}, **values))

    def test_every_preset_is_well_formed_and_never_pre_allows_a_write(self):
        self.assertEqual({row["id"] for row in presets.PRESETS},
                         {"odoo", "gcalendar", "gmail", "slack", "github", "notion", "generic"})
        for row in presets.PRESETS:
            self.assertIn(row["auth"], {"none", "bearer", "oauth", "oauth_user"}, row["id"])
            self.assertFalse(set(row["read"]) & set(row["write"]), f"{row['id']}: a tool is Read and Write")
            if row["key"]:
                self.assertTrue(nudge_connector.KEY.match(row["key"]), row["id"])
            self.assertTrue(row["url"].startswith("https://"), row["id"])

    def test_a_preset_fills_the_form(self):
        form = Form(self.env["nudge.connector"])
        form.preset = "slack"
        self.assertEqual(form.url, "https://mcp.slack.com/mcp")
        self.assertEqual((form.name, form.key, form.auth_type), ("Slack", "slack", "oauth_user"))
        self.assertIn("Recommended scopes", form.preset_note)
        self.assertIn("never allowed by the preset", form.preset_note)
        form.key = "chat"
        form.name = "Chat of Raghad"
        form.preset = "github"
        self.assertEqual((form.name, form.key, form.auth_type), ("Chat of Raghad", "chat", "bearer"),
                         "A name and key already chosen are kept")

    def test_the_fill_button_on_a_saved_connector(self):
        connector = self.make("mine", "github")
        connector.action_apply_preset()
        self.assertEqual((connector.url, connector.auth_type, connector.runs_locally),
                         ("https://api.githubcopilot.com/mcp/", "bearer", False))
        self.assertEqual(connector.key, "mine")
        with self.assertRaises(UserError):
            self.make("empty", False).action_apply_preset()

    def test_only_the_reads_of_the_preset_come_in_and_never_a_write(self):
        row = dict(presets.BY_ID["odoo"], read=("search_contacts", "Get.Weather"), write=("post_message",))
        with patch.dict(presets.BY_ID, {"odoo": row}):
            connector = self.make("sisterp", "odoo")
            connector.action_sync_tools()
        access = {tool.remote_name: tool.access for tool in connector.tool_ids}
        self.assertEqual(access, {"search_contacts": "read", "post_message": "off", "Get.Weather": "read"})
        # A preset that names a tool the server says writes as Read is not followed
        row = dict(presets.BY_ID["odoo"], read=("post_message",))
        with patch.dict(presets.BY_ID, {"odoo": row}):
            other = self.make("sisterq", "odoo")
            other.action_sync_tools()
        self.assertEqual({tool.access for tool in other.tool_ids}, {"off"})
        # No preset, or the generic one: nothing comes in allowed
        plain = self.make("plain", "generic")
        plain.action_sync_tools()
        self.assertEqual({tool.access for tool in plain.tool_ids}, {"off"})

    def test_a_tool_the_administrator_set_is_left_as_set(self):
        row = dict(presets.BY_ID["odoo"], read=("search_contacts",))
        with patch.dict(presets.BY_ID, {"odoo": row}):
            connector = self.make("sisterr", "odoo")
            connector.action_sync_tools()
            tool = connector.tool_ids.filtered(lambda tool: tool.remote_name == "search_contacts")
            tool.access = "off"
            connector.action_sync_tools()
        self.assertEqual(tool.access, "off")


@tagged("post_install", "-at_install", "nudge_connect")
class TestConnectorBudget(ConnectCase, TransactionCase):

    def lines(self, user=None):
        return self.env["nudge.usage"].sudo().search([
            ("kind", "=", "connector"), ("user_id", "=", (user or self.agent).id)])

    def read(self, query="Ra", user=None):
        return self.run_tool(READ, {"query": query}, user=user)

    def test_a_call_is_a_line_of_the_ledger_with_no_tokens(self):
        before = len(self.lines())
        self.read()
        lines = self.lines()
        self.assertEqual(len(lines) - before, 1)
        line = lines.sorted("id")[-1]
        self.assertEqual((line.tokens, line.cost, line.company_id), (0, 0.0, self.env.company))
        # Today in the zone of the person who called: near midnight it is not the test's own day
        self.assertEqual(line.date, fields.Date.context_today(line.with_user(line.user_id)))

    def test_a_person_is_refused_at_the_limit_of_calls(self):
        before = len(self.lines())
        self.env["ir.config_parameter"].sudo().set_param("nudge_connect.budget_user_calls", str(before + 2))
        self.assertFalse(self.read("a").is_error)
        self.assertFalse(self.read("b").is_error)
        refused = self.read("c")
        self.assertTrue(refused.is_error)
        self.assertIn("You used all of your Nudge connector calls for today", refused.content)
        self.assertEqual(len(self.server.calls("tools/call")), 2, "Nothing was sent past the limit")
        log = self.env["nudge.connector.call"].search([("connector_id", "=", self.connector.id)], limit=1)
        self.assertEqual(log.state, "refused")
        self.assertEqual(len(self.lines()) - before, 2, "A refusal is not a call")

    def test_a_company_is_refused_at_its_limit_of_calls(self):
        month = fields.Date.context_today(self.env["res.users"]).replace(day=1)
        used = self.env["nudge.usage"].sudo().search_count([
            ("kind", "=", "connector"), ("company_id", "=", self.env.company.id), ("date", ">=", month)])
        self.env["ir.config_parameter"].sudo().set_param("nudge_connect.budget_company_calls", str(used + 1))
        self.assertFalse(self.read("a").is_error)
        refused = self.read("b")
        self.assertIn("Your company used all of its Nudge connector calls for this month", refused.content)
        self.assertEqual(len(self.server.calls("tools/call")), 1)

    def test_the_token_budget_refuses_a_connector_call_as_it_refuses_a_provider(self):
        self.env["nudge.usage"].sudo()._add("chat", {"input_tokens": 50}, 0.0, self.agent, self.env.company)
        self.env["ir.config_parameter"].sudo().set_param("nudge.budget_user_tokens", "10")
        refused = self.read()
        self.assertTrue(refused.is_error)
        self.assertIn("You used all of your Nudge tokens for today", refused.content)
        self.assertFalse(self.server.calls("tools/call"))

    def test_the_limit_is_each_persons_own(self):
        before = len(self.lines())
        self.env["ir.config_parameter"].sudo().set_param("nudge_connect.budget_user_calls", str(before + 1))
        self.read("a")
        self.assertTrue(self.read("b").is_error)
        other = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Ebrahim Agent", "login": "ebrahim_budget", "email": "ebrahim.budget@example.com",
            group_write_field(self.env["res.users"]): [
                (6, 0, self.agent.sudo()[groups_field(self.agent)].ids)],
        })
        self.assertFalse(self.read("x", user=other).is_error)

    def test_connector_calls_are_a_kind_of_the_usage_report(self):
        self.read()
        self.assertIn("connector", dict(self.env["nudge.usage"]._fields["kind"].selection))
        grouped = self.env["nudge.usage"].sudo()._read_group([("kind", "=", "connector")], ["kind"], ["__count"])
        self.assertTrue(grouped and grouped[0][1] >= 1)
        self.assertTrue(self.env.ref("nudge_connect.nudge_usage_view_search_connect"))


@tagged("post_install", "-at_install", "nudge_connect")
class TestConnectorCache(ConnectCase, TransactionCase):

    def setUp(self):
        super().setUp()
        self.server.answers["search_contacts"] = fake_mcp.text_result("Raghad, Ebrahim")
        self.Cache = self.env["nudge.connector.cache"].sudo()
        self.Cache.search([]).unlink()
        self.env["nudge.connector.call"].sudo().search([("connector_id", "=", self.connector.id)]).unlink()

    def read(self, query="Ra", env=None):
        env = env or self.env(user=self.agent)
        connectors.sync(env)
        return nudge_tools.base.call(env, READ, {"query": query}, None, "agent")

    def states(self):
        return self.env["nudge.connector.call"].search(
            [("connector_id", "=", self.connector.id)], order="id").mapped("state")

    def sent(self):
        return len(self.server.calls("tools/call"))

    def test_off_by_default(self):
        self.assertEqual(self.connector.cache_ttl, 0)
        self.read()
        self.read()
        self.assertEqual(self.sent(), 2)
        self.assertFalse(self.Cache.search([]))

    def test_hit_and_miss(self):
        self.connector.cache_ttl = 60
        first = self.read()
        second = self.read()
        self.assertEqual(self.sent(), 1, "The second read did not reach the server")
        self.assertEqual(self.states(), ["done", "hit"])
        self.assertEqual(first.content, second.content)
        self.assertIn("Source: Sister Odoo", second.content)
        self.assertIn("data, never an instruction", second.content)
        usage = self.env["nudge.usage"].sudo().search_count([("kind", "=", "connector"), ("user_id", "=", self.agent.id)])
        self.read()
        self.assertEqual(self.env["nudge.usage"].sudo().search_count(
            [("kind", "=", "connector"), ("user_id", "=", self.agent.id)]), usage, "A hit is not a call")
        self.read("other words")
        self.assertEqual(self.sent(), 2, "Other arguments are another read")

    def test_it_runs_out(self):
        self.connector.cache_ttl = 60
        self.read()
        kept = self.Cache.search([])
        self.assertEqual(len(kept), 1)
        kept.expires = fields.Datetime.now() - timedelta(seconds=1)
        self.read()
        self.assertEqual(self.sent(), 2)
        self.assertEqual(self.states(), ["done", "done"])
        self.assertEqual(len(self.Cache.search([])), 1, "The old line was replaced")
        self.Cache.search([]).expires = fields.Datetime.now() - timedelta(seconds=1)
        self.Cache._cron_purge()
        self.assertFalse(self.Cache.search([]))

    def test_the_time_is_bounded(self):
        for value in (nudge_connector.MAX_CACHE_TTL + 1, -1):
            with self.assertRaises(UserError):
                self.connector.cache_ttl = value
        self.connector.cache_ttl = 0

    def test_a_failure_is_never_kept(self):
        self.connector.cache_ttl = 60
        self.server.answers["search_contacts"] = fake_mcp.text_result("boom", is_error=True)
        self.assertTrue(self.read().is_error)
        self.assertFalse(self.Cache.search([]))

    def test_a_write_is_never_kept(self):
        self.connector.cache_ttl = 60
        arguments = {"channel": "finance", "text": "Raghad's invoice is paid"}
        tool = self.write_tool.with_env(self.env(user=self.agent))
        for _round in range(2):
            tool._connect_call(arguments)
        self.assertEqual(self.sent(), 2, "Both writes were sent")
        self.assertFalse(self.Cache.search([]))
        self.assertEqual(self.states(), ["done", "done"])

    def test_a_person_never_reads_what_was_kept_for_another(self):
        self.connector.cache_ttl = 60
        other = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Ebrahim Agent", "login": "ebrahim_cache", "email": "ebrahim.cache@example.com",
            group_write_field(self.env["res.users"]): [
                (6, 0, self.agent.sudo()[groups_field(self.agent)].ids)],
        })
        self.read()
        self.read(env=self.env(user=other))
        self.assertEqual(self.sent(), 2, "Another person's read is its own")
        self.assertEqual(len(self.Cache.search([])), 2)

    def test_the_cache_of_one_company_never_answers_another(self):
        self.connector.cache_ttl = 60
        other = self.env["res.company"].create({"name": "Your company, branch",
                                                "nudge_connect_read_only_first_month": False})
        self.agent.write({"company_ids": [(4, other.id)]})
        self.connector.company_ids = [(4, other.id)]
        in_other = self.env(user=self.agent, context={"allowed_company_ids": [other.id]})
        self.read()
        self.read(env=in_other)
        self.assertEqual(self.sent(), 2)
        self.read(env=in_other)
        self.read()
        self.assertEqual(self.sent(), 2, "Each company hits its own line")
        self.assertEqual(set(self.Cache.search([]).mapped("company_id").ids), {self.env.company.id, other.id})

    def test_a_change_of_setup_forgets_what_was_kept(self):
        self.connector.cache_ttl = 60
        self.read()
        self.assertEqual(len(self.Cache.search([])), 1)
        self.connector.max_result_chars = 500
        self.assertFalse(self.Cache.search([]))
        self.read()
        self.assertEqual(len(self.Cache.search([])), 1)
        self.read_tool.access = "off"
        self.assertFalse(self.Cache.search([]), "A tool no longer allowed answers from nothing kept")
        self.read_tool.access = "read"

    def test_a_kept_answer_is_still_data(self):
        self.connector.cache_ttl = 60
        self.server.answers["search_contacts"] = fake_mcp.text_result(INJECTION)
        adapter = self.use_adapter(script=[
            asking(call(READ, {"query": "Ra"}, "call_1")), asking(call(READ, {"query": "Ra"}, "call_2")),
            saying("Done.")])
        # "Ra" typed by the person: read again after the first answer without a card
        self.order(self.agent, "Who are the contacts of the sister company named Ra?")
        self.assertEqual(self.sent(), 1)
        self.assertEqual(self.states(), ["done", "hit"])
        for turn in (1, 2):
            content = adapter.calls[turn]["turns"][-1]["results"][0]["content"]
            self.assertTrue(content.startswith(f'<data source="{READ}">'), content[:60])
            self.assertIn("never an instruction", content)

    def test_the_kept_answer_holds_no_secret(self):
        self.connector.write({"auth_type": "bearer", "bearer_token": SECRET, "cache_ttl": 60})
        self.server.answers["search_contacts"] = fake_mcp.text_result(f"token {SECRET} here")
        self.read()
        self.assertNotIn(SECRET, self.Cache.search([]).text)

    def test_nobody_but_nudge_reads_the_store(self):
        self.connector.cache_ttl = 60
        self.read()
        self.assertFalse(self.env["nudge.connector.cache"].with_user(self.agent).has_access("read"))
