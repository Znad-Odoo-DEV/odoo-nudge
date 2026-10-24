"""NP-03, the size of a call: every call carries the core tools of its mode and the base instructions;
the tools of a kind of work (invoices, imports, reports, automations, the website...) and its
instructions come only with the questions that need them, with no AI. Measured on the real request,
the system blocks and the tool schema, at 4 characters a token. Never a provider is reached."""
import logging

from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.llm import base as llm, loop, prompt
from odoo.addons.nudge.models.nudge_conversation import DEVELOPER_PARAM
from odoo.addons.nudge.models.nudge_skill import INLINE_PARAM
from odoo.addons.nudge.tools import base, groups
from odoo.addons.nudge.tools.python import PYTHON_PARAM
from odoo.addons.nudge.tools.sql import SQL_PARAM

from .common import NudgeCase

_logger = logging.getLogger(__name__)

# What a call may weigh, in tokens: the card asks under 12k for Agent; the base prompt, the overview of
# the database and the index of the skills leave about 14k for a typical question of that mode. Talk measured
# 11.1k for an import question once 24 skills were in the index (about 35 tokens each)
BUDGET = {"talk": 11500, "agent": 15000, "agent_heaviest": 16500, "dev": 20000}

PLAIN = "How do I record a payment from a customer?"
INVOICE = "Which invoices of Raghad are still unpaid?"
IMPORT = "How do I import my contacts from a CSV file?"
REPORT = "Make a report of the sales per month, as a chart, with SQL if needed"
AUTOMATION = "When a sales order is confirmed, send an email to the customer"
WEBSITE = "Build a website for my company with a contact page"


def tokens(call):
    """The system text and the tool schema as sent, at 4 characters a token (S8: the mark of the tools
    every question is offered is read by the adapters, never sent)"""
    text = "\n".join(llm.system_texts(call["system"]))
    sent = [{key: value for key, value in tool.items() if key != llm.CACHE_POINT} for tool in call["tools"] or []]
    return (len(text) + len(base.dump(sent))) // 4


def names(call):
    return {tool["name"] for tool in call["tools"] or ()}


@tagged("post_install", "-at_install")
class TestPromptBudget(NudgeCase, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Parameter = cls.env["ir.config_parameter"].sudo()
        # Every switch on: the largest set of tools an administrator can be offered
        for param in (DEVELOPER_PARAM, PYTHON_PARAM, SQL_PARAM):
            cls.Parameter.set_param(param, "yes")
        cls.Parameter.set_param(INLINE_PARAM, False)
        cls.admin = new_test_user(
            cls.env, login="nudge_budget_admin",
            groups="nudge.group_nudge_manager,nudge.group_nudge_agent,base.group_system,base.group_partner_manager")

    def first_call(self, user, text, mode="agent", conversation_id=None, script=None, snapshot=None):
        """Ask as the controller does. :return: (conversation, the calls to the model)"""
        adapter = self.use_adapter(script=script)
        conversation = self.env["nudge.conversation"].with_user(user)._chat_open(conversation_id)
        job = conversation._chat_prepare(text, mode=mode, snapshot=snapshot)
        list(loop.run(job, conversation._chat_record))
        return conversation, adapter.calls

    # ---------------------------------------------------------------- the budget
    def test_a_call_stays_within_its_budget(self):
        measured = {}
        for mode, text in (("talk", PLAIN), ("talk", IMPORT), ("agent", PLAIN), ("agent", INVOICE),
                           ("agent", IMPORT), ("agent", REPORT), ("dev", PLAIN), ("dev", AUTOMATION),
                           ("dev", WEBSITE)):
            _conversation, calls = self.first_call(self.admin, text, mode=mode)
            measured[mode, text] = tokens(calls[0])
        # The same questions with every tool and every skill in full, as before NP-03
        self.Parameter.set_param(INLINE_PARAM, True)
        before = {mode: tokens(self.first_call(self.admin, PLAIN, mode=mode)[1][0]) for mode in ("talk", "agent", "dev")}
        _logger.info("NP-03 call sizes in tokens, every tool in full: %s; routed: %s", before,
                     {f"{mode}: {text[:30]}": size for (mode, text), size in measured.items()})
        self.Parameter.set_param(INLINE_PARAM, False)
        for mode in ("talk", "agent", "dev"):
            call = self.first_call(self.admin, PLAIN, mode=mode)[1][0]
            _logger.info("NP-03 %s, a plain question: system %s tokens, tools %s", mode,
                         len("\n".join(llm.system_texts(call["system"]))) // 4,
                         sorted((len(base.dump(tool)) // 4, tool["name"]) for tool in call["tools"]))
        for text in (PLAIN, INVOICE, IMPORT):
            self.assertLess(measured["agent", text], BUDGET["agent"], f"Agent: {text}")
        self.assertLess(measured["agent", REPORT], BUDGET["agent_heaviest"])
        for text in (PLAIN, IMPORT):
            self.assertLess(measured["talk", text], BUDGET["talk"], f"Talk: {text}")
        for text in (PLAIN, AUTOMATION, WEBSITE):
            self.assertLess(measured["dev", text], BUDGET["dev"], f"Developer: {text}")
        for mode in ("talk", "agent", "dev"):
            self.assertLess(measured[mode, PLAIN], before[mode], f"{mode}: shorter than every tool in full")
        self.assertLess(measured["agent", PLAIN], before["agent"] - 4000, "Agent: at least 4k tokens fewer")

    def test_a_later_question_of_a_conversation_carries_the_overview_once(self):
        """S8: the same question asked first and then again in the same conversation, measured as above
        with the context of the question: the system prompt keeps what every answer needs of the overview
        of the database and the notes, the same in both calls; the rest of the overview goes with the
        first question only; the tools every question is offered come first, in the same order"""
        self.env["nudge.note"].with_user(self.manager).create({"name": "Words", "body": "We call quotations offers."})
        self.env["nudge.profile"].sudo()._ensure_current()
        overview = self.env["nudge.profile"].with_user(self.admin).sudo()._get_overview()
        kept, rest = prompt.split_overview(overview)
        self.assertTrue(rest, "An administrator reads an overview with a rest")
        sizes = {}
        for mode in ("talk", "agent", "dev"):
            conversation, first = self.first_call(self.admin, PLAIN, mode=mode)
            _conversation, later = self.first_call(self.admin, PLAIN, mode=mode, conversation_id=conversation.id)
            asked = [first[0], later[0]]
            contexts = [next(turn for turn in reversed(call["turns"]) if turn["role"] == "user")["context"] for call in asked]
            total = [tokens(call) + len(context) // 4 for call, context in zip(asked, contexts)]
            # What every call weighed before S8: the whole overview in the system prompt, no rest in the context
            before = total[1] + (len(overview) - len(kept)) // 4
            sizes[mode] = {"before": before, "first": total[0], "later": total[1],
                           "overview": len(overview) // 4, "kept": len(kept) // 4}
            self.assertEqual(first[0]["system"], later[0]["system"], f"{mode}: one system prompt, read from the cache")
            self.assertIn("We call quotations offers.", first[0]["system"][1], f"{mode}: the notes are rules, in every call")
            self.assertIn(prompt.render_overview_rest(rest), contexts[0])
            self.assertNotIn("<nudge_overview>", contexts[1])
            self.assertLess(total[1], before, mode)
            self.assertLessEqual(total[0], before + len(prompt.OVERVIEW_GIVEN) // 4 + 40, f"{mode}: the first call about as before")
            self.assertEqual([tool["name"] for tool in later[0]["tools"]], [tool["name"] for tool in first[0]["tools"]])
            stable = next((index for index, tool in enumerate(first[0]["tools"]) if tool.get(llm.CACHE_POINT)), None)
            if stable is not None:
                self.assertTrue(all(tool["name"] in base.CORE_BY_MODE[mode] or tool["name"] not in groups.routed_tools()
                                    for tool in first[0]["tools"][:stable + 1]), f"{mode}: the core first")
            self.assertLess(total[1], BUDGET[mode], mode)
        _logger.info("S8 call sizes in tokens (system, tools and the context of the question): %s", sizes)

    # ------------------------------------------------------------- the right tools
    def test_each_question_gets_the_tools_and_instructions_of_its_work(self):
        _conversation, calls = self.first_call(self.admin, PLAIN)
        plain = names(calls[0])
        core = {"search_records", "read_record", "find_record", "aggregate_records", "prepare_create",
                "prepare_write", "call_method", "show_on_screen", "search_odoo_documentation", "locate_data",
                "more_tools"}
        self.assertLessEqual(core, plain, "The core of Agent mode is in every call")
        for name in ("document_to_record", "test_import", "make_spreadsheet", "run_sql", "start_website_builder",
                     "prepare_plan", "bulk_apply", "prepare_task", "show_tour"):
            self.assertNotIn(name, plain, f"{name}: not for a plain question")
        self.assertNotIn("Many records, or a few steps", "\n".join(llm.system_texts(calls[0]["system"])))

        _conversation, calls = self.first_call(self.admin, INVOICE)
        self.assertIn("document_to_record", names(calls[0]))
        _conversation, calls = self.first_call(self.admin, IMPORT)
        self.assertLessEqual({"test_import", "match_import_columns"}, names(calls[0]))
        self.assertIn("## Importing files", calls[0]["system"][-1], "The instructions of the import come too")
        _conversation, calls = self.first_call(self.admin, REPORT)
        self.assertLessEqual({"show_chart", "run_python", "save_report"}, names(calls[0]))
        _conversation, calls = self.first_call(self.admin, "Confirm all the draft quotations")
        self.assertIn("prepare_plan", names(calls[0]))
        # The Import screen brings the import tools whatever the words
        _conversation, calls = self.first_call(self.admin, "What do these errors mean?",
                                               snapshot={"model": "res.partner", "import": {"errors": ["x"]}})
        self.assertIn("test_import", names(calls[0]))

        _conversation, calls = self.first_call(self.admin, AUTOMATION, mode="dev")
        dev = names(calls[0])
        self.assertLessEqual({"prepare_automation", "try_automation", "prepare_email_template",
                              "read_automations", "check_python_code", "prepare_undo"}, dev)
        self.assertNotIn("prepare_view_change", dev)
        _conversation, calls = self.first_call(self.admin, WEBSITE, mode="dev")
        if "start_website_builder" in base.TOOLS:
            self.assertIn("start_website_builder", names(calls[0]))
        _conversation, calls = self.first_call(self.admin, "Add a field Raghad's code on the contact form", mode="dev")
        self.assertLessEqual({"read_view", "prepare_field", "prepare_view_change"}, names(calls[0]))
        # Developer mode always has something to build with
        _conversation, calls = self.first_call(self.admin, "Hello", mode="dev")
        self.assertIn("prepare_automation", names(calls[0]))

    def test_an_answer_goes_on_with_the_tools_of_the_question_it_answers(self):
        conversation, calls = self.first_call(self.admin, "Make me a spreadsheet of the sales by month")
        spreadsheet = "make_spreadsheet" in base.TOOLS
        if spreadsheet:
            self.assertIn("make_spreadsheet", names(calls[0]))
        _conversation, calls = self.first_call(self.admin, "By salesperson please", conversation_id=conversation.id)
        if spreadsheet:
            self.assertIn("make_spreadsheet", names(calls[0]), "The answer to Nudge's question keeps its tools")
        self.assertIn("show_chart", names(calls[0]))

    # ------------------------------------------------------------- never a dead end
    def test_a_tool_called_without_being_offered_runs_and_is_offered_next(self):
        script = [[llm.tool_use_event("call_1", "record_changes", {"model": "res.partner", "id": self.admin.partner_id.id}),
                   llm.stop_event(llm.STOP_TOOL_USE)],
                  [llm.text_event("Done."), llm.stop_event(llm.STOP_END)]]
        conversation, calls = self.first_call(self.admin, PLAIN, script=script)
        self.assertNotIn("record_changes", names(calls[0]))
        self.assertIn("record_changes", names(calls[1]), "Offered from the next step, with its schema")
        result = self.env["nudge.message"].sudo().search(
            [("conversation_id", "=", conversation.id), ("role", "=", "tool")], limit=1)
        self.assertNotIn("There is no tool named", str(result.tool_results))

    def test_the_model_asks_for_a_group(self):
        script = [[llm.tool_use_event("call_1", "more_tools", {"groups": ["tasks"]}), llm.stop_event(llm.STOP_TOOL_USE)],
                  [llm.text_event("Ready."), llm.stop_event(llm.STOP_END)]]
        conversation, calls = self.first_call(self.admin, PLAIN, script=script)
        self.assertNotIn("prepare_task", names(calls[0]))
        self.assertIn("prepare_task", names(calls[1]))
        _conversation, calls = self.first_call(self.admin, "Thanks", conversation_id=conversation.id)
        self.assertIn("prepare_task", names(calls[0]), "Kept for the rest of the conversation")

    def test_every_tool_in_full_is_the_fallback(self):
        self.Parameter.set_param(INLINE_PARAM, True)
        _conversation, calls = self.first_call(self.admin, PLAIN)
        offered = names(calls[0])
        self.assertLessEqual({"document_to_record", "test_import", "run_python", "prepare_plan", "prepare_task"}, offered)
        self.assertNotIn("more_tools", offered, "Nothing to ask for: every tool is there")

    def test_a_small_model_keeps_its_essential_tools(self):
        self.provider.tool_set = "essential"
        _conversation, calls = self.first_call(self.admin, PLAIN)
        self.assertEqual(calls[0]["system"][0], prompt.SYSTEM_PERSONA_COMPACT)
        self.assertLessEqual({"prepare_plan", "bulk_apply", "prepare_task"}, names(calls[0]),
                             "The essential tools of the mode, as before")

    # ------------------------------------------------------------- permissions
    def test_words_screens_and_skills_never_give_a_tool(self):
        """A Nudge User may not act: no word, screen or skill brings them a tool of Agent mode, nor SQL
        to an administrator whose switch is off"""
        self.Skill = self.env["nudge.skill"]
        self.Skill.with_user(self.manager).create({
            "name": "Sales data", "key": "sales-data", "summary": "The data of the sales.", "modes": "talk,agent,dev",
            "instructions": "## Sales data\n- Read them all.\n", "state": "published",
            "trigger_words": "sales data", "tool_names": "run_sql,prepare_write,prepare_plan,bulk_apply"})
        text = "Sales data: confirm all the quotations, import this CSV, run SQL, make a spreadsheet report"
        _conversation, calls = self.first_call(self.user_a, text, mode="talk")
        offered = names(calls[0])
        self.assertIn("## Sales data", calls[0]["system"][-1])
        for name in ("run_sql", "prepare_write", "prepare_plan", "bulk_apply", "prepare_create", "call_method",
                     "make_spreadsheet", "save_report"):
            self.assertNotIn(name, offered, f"{name}: never through a word or a skill")
        self.assertTrue(all(base.TOOLS[name].is_available(self.env(user=self.user_a), "talk") for name in offered))
        # A tool asked for by name, without the right, is refused as before
        result = base.call(self.env(user=self.user_a), "prepare_write", {}, mode="talk")
        self.assertTrue(result.is_error)
        # The administrator with SQL switched off
        self.Parameter.set_param(SQL_PARAM, "no")
        _conversation, calls = self.first_call(self.admin, text)
        self.assertNotIn("run_sql", names(calls[0]))
        self.assertIn("prepare_plan", names(calls[0]))

    def test_every_tool_of_a_group_is_reachable(self):
        """What a call does not carry, more_tools can ask for: each group is in its list"""
        definition = base.TOOLS["more_tools"].definition()
        self.assertLessEqual(groups.NP03_GROUPS, set(definition["input_schema"]["properties"]["groups"]["items"]["enum"]))
        for mode in base.MODES:
            core = base.CORE_BY_MODE[mode]
            self.assertIn("more_tools", core)
            self.assertFalse({"run_sql", "prepare_task", "document_to_record", "prepare_automation"} & core)
