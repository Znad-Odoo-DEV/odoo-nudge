import json

from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import base as llm
from odoo.addons.nudge.tests.common import NudgeCase


@tagged("post_install", "-at_install")
class TestBridge(NudgeCase, TransactionCase):
    """Nudge and the AI app, together."""

    def test_a_provider_takes_the_key_of_the_ai_app(self):
        Parameter = self.env["ir.config_parameter"].sudo()
        Parameter.set_param("ai.google_key", "AIza-test-key")
        provider = self.provider.copy({"name": "Gemini through the AI app", "api_key": False})
        provider.key_source = "ai_google"
        self.assertTrue(provider._has_credentials())
        self.assertEqual(provider._get_config().api_key, "AIza-test-key")
        self.assertTrue(provider.is_configured)

        Parameter.set_param("ai.google_key", "")
        provider.invalidate_recordset()
        self.assertFalse(provider._has_credentials())
        provider.key_source = "own"
        self.assertFalse(provider._has_credentials(), "Nothing typed here either")

    def test_what_the_agents_are_told_is_told_to_nudge(self):
        agent = self.env["ai.agent"].create({
            "name": "Returns helper",
            "subtitle": "Returns and refunds",
            "system_prompt": "Returns are accepted within 14 days of delivery.",
        })
        agent.topic_ids = self.env["ai.topic"].create({
            "name": "Refunds",
            "instructions": "Refund to the original card only.",
        })
        overview = self.env["nudge.conversation"].with_user(self.user_a)._chat_overview()
        self.assertIn("## Agents of the AI app", overview)
        self.assertIn("Returns helper (Returns and refunds): Returns are accepted within 14 days", overview)
        self.assertIn("Topic Refunds: Refund to the original card only.", overview)

        listed = tools.base.call(self.env(user=self.user_a), "list_company_agents", {})
        self.assertFalse(listed.is_error, listed.content)
        agents = {entry["name"]: entry for entry in json.loads(listed.content)["agents"]}
        self.assertEqual(agents["Returns helper"]["topics"], ["Refunds"])
        self.assertEqual(agents["Returns helper"]["sources"], 0)

        nothing = tools.base.call(
            self.env(user=self.user_a), "search_company_sources", {"query": "returns"})
        self.assertIn("No source this person may read", nothing.content)
        nobody = tools.base.call(
            self.env(user=self.user_a), "search_company_sources",
            {"query": "returns", "agent": "Nowhere"})
        self.assertIn("No agent named like 'Nowhere'", nobody.content)

    def make_action(self):
        action = self.env["ir.actions.server"].create({
            "name": "Say hello",
            "model_id": self.env.ref("base.model_res_partner").id,
            "state": "code",
            "code": "ai['result'] = 'Hello ' + name + (', on ' + record.name if record else '')",
            "use_in_ai": True,
            "ai_tool_description": "Says hello to a name",
            "ai_tool_schema": json.dumps({
                "type": "object",
                "properties": {"name": {"type": "string", "description": "A name"}},
                "required": ["name"],
            }),
        })
        self.assertTrue(action.use_in_ai, "A code action can be an AI tool")
        return action, f"action_{action.id}"

    def test_the_ai_tools_are_listed_and_run_after_a_yes(self):
        action, name = self.make_action()
        env = self.env(user=self.agent)
        # The database holds the AI tools of the AI app as well: the whole list stays JSON
        everything = json.loads(tools.base.call(env, "list_ai_actions", {}, mode="agent").content)
        self.assertTrue(everything["actions"])
        listed = json.loads(tools.base.call(
            env, "list_ai_actions", {"words": "Says hello"}, mode="agent").content)
        found = {entry["name"]: entry for entry in listed["actions"]}
        self.assertEqual(found[name]["description"], "Says hello to a name")
        self.assertEqual(found[name]["model"], "res.partner")
        self.assertEqual(found[name]["required"], ["name"])

        # Nothing runs before the person says yes
        pending = tools.base.call(
            env, "run_ai_action", {"action": name, "arguments": {"name": "Bob"}}, mode="agent")
        self.assertFalse(pending.is_error, pending.content)
        self.assertIn("Say hello", pending.pending["summary"])
        self.assertTrue(pending.pending["is_sensitive"])
        self.assertEqual(pending.pending["preview"]["lines"], [{"label": "name", "value": "Bob"}])

        refused = tools.base.call(env, "run_ai_action", {"action": name, "arguments": {}}, mode="agent")
        self.assertTrue(refused.is_error)
        unknown = tools.base.call(env, "run_ai_action", {"action": "nowhere"}, mode="agent")
        self.assertTrue(unknown.is_error)
        self.assertIn("no AI tool named 'nowhere'", unknown.content)

        # Through the agent: the card, the yes, the result
        adapter = self.use_adapter(script=[
            [llm.tool_use_event("call_1", "run_ai_action", {"action": name, "arguments": {"name": "Bob"}}),
             llm.stop_event(llm.STOP_TOOL_USE)],
            [llm.text_event("Said hello."), llm.stop_event(llm.STOP_END)],
        ])
        conversation, _events = self.order(self.agent, "Say hello to Bob")
        step = conversation.sudo().waiting_step_id
        self.assertEqual(step.tool, "run_ai_action")
        self.assertEqual(step.state, "pending")
        self.decide(conversation, "confirm")
        self.assertEqual(step.state, "done")
        self.assertIn("Hello Bob", step.result)
        self.assertIn("Hello Bob", adapter.calls[-1]["turns"][-1]["results"][0]["content"])

    def test_who_gets_the_ai_tools(self):
        self.make_action()
        agent = [tool.name for tool in tools.base.available(self.env(user=self.agent), "agent")]
        self.assertIn("list_ai_actions", agent)
        self.assertIn("run_ai_action", agent)
        talk = [tool.name for tool in tools.base.available(self.env(user=self.agent), "talk")]
        self.assertNotIn("run_ai_action", talk)
        self.assertIn("search_company_sources", talk, "Reading the sources is for everybody of Nudge")
        plain = [tool.name for tool in tools.base.available(self.env(user=self.user_a), "agent")]
        self.assertNotIn("run_ai_action", plain)
