import base64

from odoo.tests import BaseCase, TransactionCase, tagged

from odoo.addons.nudge.llm import loop, ollama, prompt
from odoo.addons.nudge.models.nudge_provider import AGENT_PROVIDER_PARAM, DEFAULT_PROVIDER_PARAM
from odoo.addons.nudge.tools import commands
from odoo.addons.nudge.tools.guidance import steps_from_text

from .common import NudgeCase

SCREEN = {
    "model": "sale.order",
    "buttons": [
        {"name": "action_quotation_send", "label": "Send"},
        {"name": "action_quotation_send_by_whatsapp", "label": "Send by WhatsApp"},
        {"name": "action_confirm", "label": "Confirm"},
    ],
    "tabs": [{"name": "other_information", "label": "Other Info"}],
    "fields": [
        {"name": "partner_id", "label": "Customer", "on_screen": True},
        {"name": "payment_term_id", "label": "Payment Terms", "on_screen": True},
    ],
}
ARABIC_SCREEN = {
    "buttons": [{"name": "action_quotation_send", "label": "إرسال"},
                {"name": "action_confirm", "label": "تأكيد"}],
    "fields": [{"name": "payment_term_id", "label": "شروط الدفع"}],
}


@tagged("post_install", "-at_install")
class TestPointerFromText(BaseCase):
    """A model asked to show wrote the steps: what they name is pointed at."""

    def targets(self, text, screen=SCREEN):
        return [(step["target"]["type"], step["target"]["name"]) for step in steps_from_text(text, screen)]

    def test_buttons_and_tabs_in_the_order_they_are_named(self):
        text = ("1. Open the **Other Info** tab to check the terms.\n"
                "2. Then press **Send**: the email opens with the quotation attached.")
        self.assertEqual(self.targets(text), [("tab", "other_information"), ("button", "action_quotation_send")])
        steps = steps_from_text(text, SCREEN)
        self.assertEqual(steps[1]["text"], "Then press Send: the email opens with the quotation attached.")

    def test_the_longest_label_wins(self):
        self.assertEqual(self.targets("Use **Send by WhatsApp** for the customer."),
                         [("button", "action_quotation_send_by_whatsapp")])

    def test_a_field_only_when_it_is_meant(self):
        self.assertEqual(self.targets("Tell the customer the price."), [],
                         "A common word is not a field")
        self.assertEqual(self.targets("Fill the **Customer** first."), [("field", "partner_id")])
        self.assertEqual(self.targets("Set the Payment Terms field."), [("field", "payment_term_id")])

    def test_in_arabic(self):
        text = "عبّي حقل شروط الدفع، وبعدين اضغط على زر **إرسال**."
        self.assertEqual(self.targets(text, ARABIC_SCREEN),
                         [("field", "payment_term_id"), ("button", "action_quotation_send")])

    def test_nothing_named_nothing_shown(self):
        self.assertEqual(steps_from_text("Ask your manager.", SCREEN), [])
        self.assertEqual(steps_from_text("Press **Send**.", None), [])


@tagged("post_install", "-at_install")
class TestWords(BaseCase):

    def test_taken_somewhere(self):
        for text in ("Take me to the vendor bills", "open the quotations", "خدني على فواتير الموردين",
                     "افتحلي عروض الأسعار", "وديني على المخزون"):
            self.assertTrue(commands.asks_to_go(text), text)
        for text in ("How do I open a quotation?", "شو هي هالشاشة؟", ""):
            self.assertFalse(commands.asks_to_go(text), text)

    def test_the_language_of_the_answer(self):
        self.assertEqual(commands.language_of("كم يوم إجازة سنوية إلنا؟"), "Arabic")
        self.assertEqual(commands.language_of("How many days of leave do we get?"), "English")
        self.assertEqual(commands.language_of("شو يعني Incoterm؟"), "Arabic")

    def test_a_step_reads_without_markup(self):
        from odoo.addons.nudge.tools.guidance import plain  # noqa: PLC0415

        self.assertEqual(plain("Click on **Invoicing**, then `Bills`"), "Click on Invoicing, then Bills")


@tagged("post_install", "-at_install")
class TestArabicRoots(BaseCase):

    def test_words_as_people_write_them(self):
        fold, root = commands.fold, commands.root_of
        self.assertEqual(root(fold("هالعروض")), root(fold("عرض")))
        self.assertEqual(root(fold("الأسعار")), root(fold("سعر")))
        self.assertEqual(root(fold("بالفاتورة")), root(fold("فاتورة")))
        self.assertEqual(root("quotations"), "quotations", "A Latin word is left as it is")


@tagged("post_install", "-at_install")
class TestSmallModels(NudgeCase, TransactionCase):
    """A small model reads the short instructions, gets what Nudge found with
    the question, and fits on the graphics card."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(DEFAULT_PROVIDER_PARAM, str(cls.provider.id))

    def small(self):
        self.provider.tool_set = "essential"

    def ask_shown(self, text, snapshot, reply):
        adapter = self.use_adapter(reply=reply)
        Conversation = self.env["nudge.conversation"].with_user(self.user_contacts)
        conversation = Conversation._chat_open(None)
        job = conversation._chat_prepare(text, snapshot=snapshot, explain="screen")
        return adapter, list(loop.run(job, conversation._chat_record))

    def test_short_instructions_and_what_was_found(self):
        self.small()
        self.env["nudge.source"].create({
            "name": "Leave policy",
            "file": base64.b64encode(b"Annual leave is thirty days for every employee."),
            "file_name": "leave.txt",
        })
        adapter = self.use_adapter()
        self.ask(self.manager, "Where are the AI providers, and how many days of annual leave do we get?")
        call = adapter.calls[0]
        self.assertEqual(call["system"][0], prompt.SYSTEM_PERSONA_COMPACT)
        self.assertLess(len(prompt.SYSTEM_PERSONA_COMPACT), len(prompt.SYSTEM_PERSONA) / 3)
        context = call["turns"][-1]["context"]
        self.assertIn("<nudge_found>", context)
        self.assertIn("Nudge/Configuration/AI Providers", context)
        self.assertIn("thirty days", context)

    def test_the_tools_of_the_mode_and_the_groups_of_the_question(self):
        self.small()
        adapter = self.use_adapter()
        self.order(self.agent, "How many quotations do we have?")
        names = {tool["name"] for tool in adapter.calls[0]["tools"]}
        self.assertIn("search_records", names)
        self.assertIn("more_tools", names, "The rest is a call away")
        self.assertNotIn("make_spreadsheet", names, "A group is offered when the question asks for it")
        self.assertNotIn("change_settings", names)

        adapter = self.use_adapter()
        self.order(self.agent, "بالـ SQL: أكتر 5 زباين عندهم طلبات")
        names = {tool["name"] for tool in adapter.calls[0]["tools"]}
        self.assertIn("make_spreadsheet", names, "The words of the question bring their group, in Arabic too")

    def test_more_tools_are_given_for_the_rest_of_the_conversation(self):
        from odoo.addons.nudge.llm import base as llm_base  # noqa: PLC0415

        self.small()
        asking = [llm_base.tool_use_event("call_1", "more_tools", {"groups": ["data"]}),
                  llm_base.stop_event(llm_base.STOP_TOOL_USE)]
        adapter = self.use_adapter(script=[asking])
        conversation, _events = self.order(self.agent, "Give me the totals by customer")
        before = {tool["name"] for tool in adapter.calls[0]["tools"]}
        after = {tool["name"] for tool in adapter.calls[1]["tools"]}
        self.assertNotIn("make_spreadsheet", before)
        self.assertIn("make_spreadsheet", after, "Offered from the next answer on")
        self.assertEqual(conversation.sudo().tool_groups, "data")
        # And to the next question of the same conversation
        adapter = self.use_adapter()
        self.order(self.agent, "And by country?", conversation_id=conversation.id)
        self.assertIn("make_spreadsheet", {tool["name"] for tool in adapter.calls[0]["tools"]})

    def test_a_large_model_has_every_tool_and_no_groups(self):
        adapter = self.use_adapter()
        self.order(self.agent, "How many quotations do we have?")
        names = {tool["name"] for tool in adapter.calls[0]["tools"]}
        self.assertIn("make_spreadsheet", names)
        self.assertNotIn("more_tools", names)

    def test_words_of_the_groups(self):
        from odoo.addons.nudge.tools import groups  # noqa: PLC0415

        self.assertEqual(groups.asked("فعّلي خيار الأسعار المتعددة من الإعدادات"), ["settings"])
        self.assertEqual(groups.asked("كم عدد الطلبات؟"), [], "A count is not a setting")
        self.assertIn("reports", groups.asked("اطبعلي تقرير PDF للطلب"))
        self.assertIn("assessment", groups.asked("Audit my database"))
        # The context of a small model keeps room for its mode's instructions
        self.small()
        self.assertGreater(self.provider._tokens_needed("dev"), self.provider._tokens_needed("agent"))
        self.assertGreater(self.provider._tokens_needed("agent"), self.provider._tokens_needed("talk"))

    def test_a_mention_is_given_what_was_found_too(self):
        # "@Nudge how do we record landed costs?" went to the small model bare: "I cannot help"
        self.small()
        adapter = self.use_adapter()
        conversation = self.env["nudge.conversation"].with_user(self.manager).create({})
        job = conversation._chat_prepare(
            "Where are the AI providers?", focus="mention", extra="<nudge_record>\n{}\n</nudge_record>")
        list(loop.run(job, conversation._chat_record))
        context = adapter.calls[0]["turns"][-1]["context"]
        self.assertIn("<nudge_found>", context)
        self.assertIn("Nudge/Configuration/AI Providers", context)
        self.assertEqual(adapter.calls[0]["system"][0], prompt.FOCUS_PERSONA)
        self.assertIn("<nudge_found>", prompt.FOCUS_PERSONA)

    def test_a_large_model_reads_everything_and_looks_itself(self):
        adapter = self.use_adapter()
        self.ask(self.manager, "Where are the AI providers?")
        call = adapter.calls[0]
        self.assertEqual(call["system"][0], prompt.SYSTEM_PERSONA)
        self.assertNotIn("<nudge_found>", call["turns"][-1]["context"])

    def test_found_in_arabic_as_written(self):
        quick = self.env["nudge.quick"].with_user(self.manager)
        menu = self.env.ref("nudge.nudge_menu_providers")
        self.env["res.lang"]._activate_lang("ar_001")
        menu.with_context(lang="ar_001").name = "مزودو الذكاء الاصطناعي"
        found = quick.with_context(lang="ar_001")._ground("وين بلاقي مزودين الذكاء؟")
        self.assertIn("Nudge/Configuration/AI Providers", [screen["path_en"] for screen in found["screens"]])
        self.assertEqual(quick._ground("zzqxw vvbtk"), {}, "Nothing like it, nothing found")

    def test_the_pointer_from_the_text_of_the_answer(self):
        snapshot = dict(SCREEN, model="res.partner")
        _adapter, events = self.ask_shown("How do I send it?", snapshot, "Press **Send** to email it.")
        guidance = [event for event in events if event.get("type") == "guidance"]
        self.assertEqual(guidance[0]["steps"][0]["target"], {"type": "button", "name": "action_quotation_send"})
        # Asked in writing: nothing is pointed at
        adapter = self.use_adapter(reply="Press **Send** to email it.")
        conversation = self.env["nudge.conversation"].with_user(self.user_contacts)._chat_open(None)
        job = conversation._chat_prepare("How do I send it?", snapshot=snapshot, explain="text")
        self.assertFalse([event for event in loop.run(job, conversation._chat_record)
                          if event.get("type") == "guidance"])
        self.assertTrue(adapter.calls)

    def test_taken_nowhere_the_screens_found_are_offered(self):
        adapter = self.use_adapter(reply="Go to Nudge, then Configuration.")
        conversation, _event = self.ask(self.manager, "Take me to the AI providers")
        events = [event for message in conversation.sudo().message_ids for event in (message.screen_events or [])]
        found = [event for event in events if event.get("type") == "found"]
        menu = self.env.ref("nudge.nudge_menu_providers")
        self.assertIn(menu.id, [entry["id"] for entry in found[0]["menus"]])
        self.assertTrue(adapter.calls)
        # Not asked to go anywhere: nothing offered
        conversation, _event = self.ask(self.manager, "What is an AI provider?")
        events = [event for message in conversation.sudo().message_ids for event in (message.screen_events or [])]
        self.assertFalse([event for event in events if event.get("type") == "found"])

    def test_every_model_is_told_the_language_of_its_answer(self):
        adapter = self.use_adapter()
        self.ask(self.user_a, "How many days of leave do we get?")
        self.assertIn('"answer_in":"English"', adapter.calls[0]["turns"][-1]["context"])
        self.small()
        self.ask(self.user_a, "كم يوم إجازة سنوية إلنا؟")
        self.assertIn('"answer_in":"Arabic"', adapter.calls[1]["turns"][-1]["context"])

    def test_the_context_is_for_the_modes_it_serves(self):
        self.small()
        everything = self.provider._tokens_needed()
        work = self.provider.copy({"name": "Work", "api_key": "sk-work", "tool_set": "full"})
        self.env["ir.config_parameter"].sudo().set_param(AGENT_PROVIDER_PARAM, str(work.id))
        self.assertEqual(self.provider._served_mode(), "talk")
        self.assertLess(self.provider._tokens_needed(), everything, "Talk only: fewer tools, less context")
        self.assertIn(work._served_mode(), ("agent", "dev"))


@tagged("post_install", "-at_install")
class TestOnTheCard(BaseCase):

    def test_the_share_on_the_processor(self):
        self.assertEqual(ollama.on_processor(6_220, 3_940), 37)
        self.assertEqual(ollama.on_processor(4_000, 4_000), 0)
        self.assertIsNone(ollama.on_processor(0, 0), "Not said: not known")
        self.assertEqual(ollama.context_for(11_000, 262_144), 12_288, "Steps of 4096")
