from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge.llm import base as llm, loop, prompt

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestExplain(NudgeCase, TransactionCase):
    """How the person wants the answer: on the screen, or in writing."""

    def context_of(self, user, **kwargs):
        conversation = self.env["nudge.conversation"].with_user(user)._chat_open(None)
        conversation._chat_prepare("How do I confirm this order?", **kwargs)
        # The answer never comes here: the next question must not be told to wait
        conversation.sudo().write({"stream_started_at": False})
        question = conversation.sudo().message_ids.filtered(lambda message: message.role == "user")
        return question[-1].sudo().context_text

    def test_the_model_is_told_how(self):
        self.assertIn('"explain":"on the screen"', self.context_of(self.user_a, explain="screen"))
        self.assertIn('"explain":"in writing"', self.context_of(self.user_a, explain="text"))
        self.assertNotIn('"explain"', self.context_of(self.user_a))
        self.assertNotIn('"explain"', self.context_of(self.user_a, explain="loudly"))

    def test_the_agent_is_told_too(self):
        """The person pressed On the screen on the card in Agent mode: the
        choice reaches the model there as well (the browser alone decides
        that a standing preference leaves the agent its orders)."""
        self.assertIn('"explain":"on the screen"',
                      self.context_of(self.agent, explain="screen", mode="agent"))
        self.assertNotIn('"explain"', self.context_of(self.agent, mode="agent"))
        self.assertIn("they asked how", prompt.AGENT_MODE)

    def test_the_pointer_makes_the_model_reach_for_a_tool(self):
        conversation = self.env["nudge.conversation"].with_user(self.user_a)._chat_open(None)
        job = conversation._chat_prepare("How do I open the users?", explain="screen")
        self.assertEqual(job["tool_choice"], "required")
        conversation.sudo().write({"stream_started_at": False})
        job = conversation._chat_prepare("How do I open the users?", explain="text")
        self.assertIsNone(job["tool_choice"])
        conversation.sudo().write({"stream_started_at": False})
        self.assertIsNone(conversation._chat_prepare("How do I open the users?")["tool_choice"])

    def test_everyone_is_asked_by_default(self):
        settings = self.env["res.users.settings"]._find_or_create_for_user(self.user_a)
        self.assertEqual(settings.nudge_explain, "ask")
        settings.nudge_explain = "screen"
        self.assertEqual(settings._res_users_settings_format()["nudge_explain"], "screen",
                         "The browser reads the choice with the other settings")

    def test_the_prompt_knows_the_words(self):
        self.assertIn('"explain": "on the screen"', prompt.SYSTEM_PERSONA)
        self.assertIn('"explain": "in writing"', prompt.SYSTEM_PERSONA)
        self.assertIn("show_menu_path", prompt.SYSTEM_PERSONA)

    def test_a_report_as_a_spreadsheet(self):
        from odoo.addons.nudge import tools
        can = "make_spreadsheet" in tools.base.TOOLS
        self.assertEqual(self.env["nudge.conversation"].with_user(self.agent)._can_make_spreadsheet(), can)
        self.assertFalse(self.env["nudge.conversation"].with_user(self.user_a)._can_make_spreadsheet(),
                         "No agent, no spreadsheet")
        conversation = self.env["nudge.conversation"].with_user(self.agent)._chat_open(None)
        job = conversation._chat_prepare("Compare the salaries", mode="agent", report="sheet")
        question = conversation.sudo().message_ids.filtered(lambda message: message.role == "user")[-1]
        if can:
            self.assertIn('"report":"spreadsheet"', question.context_text)
            self.assertEqual(job["tool_choice"], "required")
        else:
            self.assertNotIn('"report"', question.context_text)
            self.assertIsNone(job["tool_choice"])
        conversation.sudo().write({"stream_started_at": False})
        job = conversation._chat_prepare("Compare the salaries", mode="agent", report="chat")
        self.assertIsNone(job["tool_choice"])
        self.assertIn('"report": "spreadsheet"', prompt.SYSTEM_PERSONA)

    def test_the_first_rounds_are_forced_and_the_last_is_free(self):
        """A model that keeps looking is forced twice, then left to answer."""
        adapter = self.use_adapter(script=[
            [llm.tool_use_event("call_1", "find_screen", {"words": "users"}),
             llm.stop_event(llm.STOP_TOOL_USE)],
            [llm.tool_use_event("call_2", "find_screen", {"words": "settings"}),
             llm.stop_event(llm.STOP_TOOL_USE)],
            [llm.text_event("Settings, then Users."), llm.stop_event(llm.STOP_END)],
        ])
        conversation = self.env["nudge.conversation"].with_user(self.user_a)._chat_open(None)
        job = conversation._chat_prepare("How do I open the users?", explain="screen")
        events = list(loop.run(job, conversation._chat_record))
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual([call["tool_choice"] for call in adapter.calls],
                         ["required", "required", None])

        adapter = self.use_adapter(reply="Settings, then Users.")
        job = conversation._chat_prepare("How do I open the users?", explain="text")
        list(loop.run(job, conversation._chat_record))
        self.assertEqual([call["tool_choice"] for call in adapter.calls], [None])

    def test_the_way_through_the_menus_is_shown_at_once(self):
        Menu = self.env["ir.ui.menu"]
        app = Menu.create({"name": "Nudge Test App"})
        leaf = Menu.create({
            "name": "Nudge Users",
            "parent_id": app.id,
            "action": f"ir.actions.act_window,{self.env.ref('base.action_partner_form').id}",
        })
        adapter = self.use_adapter(script=[
            [llm.tool_use_event("call_1", "show_menu_path",
                                {"menu_id": leaf.id, "texts": ["افتح التطبيق", "اضغط هون"]}),
             llm.stop_event(llm.STOP_TOOL_USE)],
        ])
        conversation = self.env["nudge.conversation"].with_user(self.agent)._chat_open(None)
        job = conversation._chat_prepare("كيف بفتح المستخدمين؟", mode="agent", explain="screen")
        events = list(loop.run(job, conversation._chat_record))
        guidance = [event for event in events if event["type"] == "guidance"]
        self.assertEqual(len(guidance), 1)
        self.assertEqual([step["target"]["name"] for step in guidance[0]["steps"]],
                         [str(app.id), str(leaf.id)])
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual([call["tool_choice"] for call in adapter.calls], ["required"])
