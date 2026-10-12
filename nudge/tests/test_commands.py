import base64

from odoo.exceptions import UserError
from odoo.tests import BaseCase, TransactionCase, new_test_user, tagged

from odoo.addons.nudge.llm import prompt
from odoo.addons.nudge.models.nudge_conversation import DEVELOPER_PARAM
from odoo.addons.nudge.tools import commands
from odoo.addons.nudge.tools.sql import SQL_PARAM

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestParsing(BaseCase):
    """A word after a slash, in English or in Arabic, says where to look."""

    def test_the_command_and_its_words(self):
        cases = {
            "/screen invoices": ("screen", "invoices"),
            "/Screen   sales  orders ": ("screen", "sales orders"),
            "/شاشة فواتير": ("screen", "فواتير"),
            "/find\nS00042": ("find", "S00042"),
            "/count unpaid invoices": ("count", "unpaid invoices"),
            "/كم فاتورة": ("count", "فاتورة"),
            "/help": ("help", ""),
        }
        for text, (name, rest) in cases.items():
            command, words = commands.parse(text)
            self.assertEqual((command.name, words), (name, rest), text)

    def test_arabic_as_people_type_it(self):
        self.assertEqual(commands.fold("عروضُ الأسعار"), commands.fold("عروض اسعار"))
        self.assertEqual(commands.fold("الفاتورة"), commands.fold("فاتوره"))
        self.assertEqual(commands.fold("إعداد"), commands.fold("اعداد"))
        self.assertEqual(commands.fold("Quotations"), "quotations")
        self.assertTrue(commands.matches(commands.fold("عروض اسعار").split(), "المبيعات/الطلبات/عروض الأسعار"))
        self.assertFalse(commands.matches(commands.fold("فواتير").split(), "المبيعات/الطلبات/عروض الأسعار"))
        self.assertEqual(commands.fold("الى"), "الي", "A short word keeps its first letters")

    def test_a_query_keeps_its_lines(self):
        _command, query = commands.parse("/sql select id\nfrom res_partner")
        self.assertEqual(query, "select id\nfrom res_partner")

    def test_anything_else_is_a_question(self):
        for text in ("screen invoices", "/screens", "what is /screen", "", "/", "/unknown words", "/mention hi"):
            self.assertIsNone(commands.parse(text)[0], text)

    def test_every_command_has_its_words_once(self):
        words = [word for command in commands.COMMANDS for word in command.words]
        self.assertEqual(len(words), len(set(words)))
        self.assertTrue(all(word.startswith("/") and word == word.lower() for word in words))
        self.assertEqual(set(commands.FOCUS) - set(commands.INTERNAL_FOCUS), {command.name for command in commands.COMMANDS
                                               if command.kind == commands.FOCUSED})


@tagged("post_install", "-at_install")
class TestDirectCommands(NudgeCase, TransactionCase):
    """Answered by the server at once, without any AI, as the person."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = new_test_user(
            cls.env, login="nudge_cmd_admin",
            groups="nudge.group_nudge_manager,nudge.group_nudge_agent,base.group_system,base.group_partner_manager")
        cls.admin.company_ids = [(6, 0, cls.env["res.company"].sudo().search([]).ids)]
        cls.partner = cls.env["res.partner"].create({"name": "Nudge Command Partner", "ref": "NCP-77"})

    def run_command(self, user, text, model_name=None):
        return self.env["nudge.quick"].with_user(user).run_command(text, model_name)

    def names(self, user):
        return [command["name"] for command in commands.listed(self.env(user=user))]

    def test_no_command_no_answer(self):
        self.assertEqual(self.run_command(self.user_a, "what is a quotation"), {"command": False})

    def test_help_lists_what_the_person_may_use(self):
        answer = self.run_command(self.user_a, "/help")
        listed = [command["name"] for command in answer["commands"]]
        self.assertEqual(listed, self.names(self.user_a))
        for name in ("help", "screen", "doc", "find", "explain"):
            self.assertIn(name, listed)
        for name in ("setting", "rules", "sql", "count"):
            self.assertNotIn(name, listed, "Not for a person without the rights")
        self.assertTrue(all(command["hint"] and command["example"] for command in answer["commands"]))
        self.assertIn("setting", self.names(self.admin))
        self.assertIn("count", self.names(self.agent))

    def test_the_session_carries_the_commands(self):
        info = self.env["ir.http"].with_user(self.user_a)._get_nudge_session_info()
        self.assertEqual([command["name"] for command in info["commands"]], self.names(self.user_a))

    def test_screens_the_person_can_open(self):
        answer = self.run_command(self.manager, "/screen ai providers")
        self.assertIn("Nudge/Configuration/AI Providers", [menu["path"] for menu in answer["menus"]])
        # In the language of the person too, however the cache of the menus was filled
        menu = self.env.ref("nudge.nudge_menu_providers")
        self.env["res.lang"]._activate_lang("ar_001")
        menu.with_context(lang="ar_001").name = "مزودو الذكاء الاصطناعي"
        self.manager.lang = "ar_001"
        found = self.env["nudge.quick"].with_user(self.manager).with_context(lang="ar_001")._find_menus(["مزودو", "الذكاء"])
        self.assertIn(menu.id, [entry["id"] for entry in found])
        self.assertIn("مزودو الذكاء الاصطناعي", [entry["path"] for entry in found][0])
        answer = self.run_command(self.user_a, "/شاشة ai providers")
        self.assertEqual(answer["menus"], [])
        self.assertTrue(answer["content"], "Nothing found is said, without any AI")

    def test_settings_are_an_administrators(self):
        answer = self.run_command(self.admin, "/setting microphone")
        self.assertIn("nudge_voice", [setting["field"] for setting in answer["settings"]])
        answer = self.run_command(self.user_a, "/setting microphone")
        self.assertNotIn("settings", answer)
        self.assertIn("/setting", answer["content"])

    def test_words_are_asked_for(self):
        answer = self.run_command(self.user_a, "/find")
        self.assertIn("/find", answer["content"])
        self.assertNotIn("records", answer)

    def test_a_record_by_its_name_or_reference(self):
        for text in ("/find Nudge Command Partner", "/سجل Contact: NCP-77", "/find res.partner: NCP-77"):
            answer = self.run_command(self.admin, text)
            found = [(record["model"], record["id"]) for record in answer["records"]]
            self.assertIn(("res.partner", self.partner.id), found, text)
        # A reference alone, without its kind: looked for as it is written, everywhere
        answer = self.run_command(self.admin, "/find ncp-77")
        self.assertIn(("res.partner", self.partner.id), [(r["model"], r["id"]) for r in answer["records"]])
        answer = self.run_command(self.admin, "/find zzqx nothing like it")
        self.assertEqual(answer["records"], [])
        self.assertTrue(answer["content"])

    def test_company_files_with_their_page(self):
        self.assertNotIn("file", self.names(self.user_a), "No file, no command")
        source = self.env["nudge.source"].create({
            "name": "Leave policy",
            "file": base64.b64encode(b"Annual leave is thirty days for every employee."),
            "file_name": "leave.txt",
        })
        if source.state != "ready":
            self.skipTest("The file could not be read here")
        self.assertIn("file", self.names(self.user_a))
        answer = self.run_command(self.user_a, "/file annual leave")
        self.assertEqual(answer["passages"][0]["file"], "Leave policy")
        self.assertIn("thirty days", answer["passages"][0]["text"])
        self.assertTrue(answer["passages"][0]["url"].startswith(f"/web/content/nudge.source/{source.id}/"))

    def test_rules_are_for_developer_mode(self):
        self.env["ir.config_parameter"].sudo().set_param(DEVELOPER_PARAM, "yes")
        answer = self.run_command(self.admin, "/rules res.partner")
        self.assertIn("Scheduled actions:", answer["code"])
        listed_on = {line.rsplit(" on ", 1)[1].split()[0] for line in answer["code"].splitlines()
                     if line.startswith("- #") and " on " in line}
        self.assertLessEqual(listed_on, {"res.partner"}, "Only what runs on the model asked for")
        # Without words, the screen in front says which
        self.assertIn("Scheduled actions:", self.run_command(self.admin, "/قواعد", "res.partner")["code"])
        self.assertIn("/rules", self.run_command(self.agent, "/rules res.partner")["content"])
        self.env["ir.config_parameter"].sudo().set_param(DEVELOPER_PARAM, "no")
        self.assertNotIn("code", self.run_command(self.admin, "/rules res.partner"))

    def test_sql_reads_only_and_only_when_allowed(self):
        self.assertNotIn("code", self.run_command(self.admin, "/sql select 1"))
        self.assertNotIn("sql", self.names(self.admin), "Off until an administrator says so")
        self.env["ir.config_parameter"].sudo().set_param(SQL_PARAM, "yes")
        answer = self.run_command(
            self.admin, "/sql select name from res_partner where ref = 'NCP-77'")
        self.assertEqual(answer["table"]["columns"], ["name"])
        self.assertEqual(answer["table"]["rows"], [["Nudge Command Partner"]])
        answer = self.run_command(self.admin, "/sql delete from res_partner where ref = 'NCP-77'")
        self.assertNotIn("table", answer)
        self.assertTrue(self.partner.exists(), "Reading only")
        self.assertNotIn("table", self.run_command(self.user_a, "/sql select name from res_partner"))

    def test_focused_commands_are_left_to_the_conversation(self):
        answer = self.run_command(self.user_a, "/explain analytic account")
        self.assertTrue(answer["focused"])
        self.assertEqual(answer["content"], "")
        self.assertIn("/count", self.run_command(self.user_a, "/count partners")["content"])


@tagged("post_install", "-at_install")
class TestFocusedCommands(NudgeCase, TransactionCase):
    """/count and /explain reach the AI with their tools and a short brief only."""

    def test_explain_reads_a_short_brief_and_no_history(self):
        adapter = self.use_adapter()
        conversation, _event = self.ask(self.user_a, "What is a quotation?")
        self.ask(self.user_a, "/explain analytic account", conversation_id=conversation.id)
        full, focused = adapter.calls
        self.assertEqual(focused["system"], [prompt.FOCUS_PERSONA, prompt.FOCUS_BRIEFS["explain"]])
        self.assertLess(sum(map(len, focused["system"])), sum(map(len, full["system"])) / 3)
        names = {tool["name"] for tool in focused["tools"]}
        self.assertLessEqual(names, set(commands.FOCUS["explain"]["tools"]))
        self.assertIn("search_odoo_documentation", names)
        self.assertEqual(len(focused["turns"]), 1, "What came before is not sent again")
        self.assertEqual(focused["turns"][0]["text"], "/explain analytic account")
        self.assertIsNone(focused["tool_choice"])
        # The next question is asked in full again
        self.ask(self.user_a, "And a journal?", conversation_id=conversation.id)
        self.assertGreater(len(adapter.calls[2]["turns"]), 1)
        self.assertNotEqual(adapter.calls[2]["system"][0], prompt.FOCUS_PERSONA)

    def test_count_reaches_for_the_numbers(self):
        adapter = self.use_adapter()
        self.ask(self.agent, "/count contacts by country")
        call = adapter.calls[0]
        names = {tool["name"] for tool in call["tools"]}
        self.assertIn("aggregate_records", names)
        self.assertLessEqual(names, set(commands.FOCUS["count"]["tools"]))
        self.assertEqual(call["tool_choice"], "required")
        self.assertEqual(call["system"][1], prompt.FOCUS_BRIEFS["count"])

    def test_the_last_round_is_for_the_answer(self):
        from odoo.addons.nudge.llm import base as llm_base  # noqa: PLC0415

        def looking(index):
            return [llm_base.tool_use_event(f"call_{index}", "describe_model", {"model": "res.partner"}),
                    llm_base.stop_event(llm_base.STOP_TOOL_USE)]

        adapter = self.use_adapter(script=[looking(1), looking(2), looking(3)])
        _conversation, event = self.ask(self.agent, "/count contacts by country")
        self.assertEqual([call["tool_choice"] for call in adapter.calls], ["required", "required", None, "none"])
        self.assertEqual(event["state"], "complete", "Answered, not cut")

    def test_count_is_not_for_everyone(self):
        self.use_adapter()
        with self.assertRaises(UserError):
            self.ask(self.user_a, "/count contacts")

    def test_words_are_asked_for(self):
        self.use_adapter()
        with self.assertRaises(UserError):
            self.ask(self.user_a, "/explain")
