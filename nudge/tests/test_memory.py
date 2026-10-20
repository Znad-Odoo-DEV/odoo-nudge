from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.llm import loop, memory_match

from .common import NudgeCase

HOW = "How do I create a quotation in Sales?"
SAME = "how can I create quotations in sales"
STEPS = "Open Sales, press New, pick the customer and add the products."


@tagged("post_install", "-at_install")
class TestMatching(TransactionCase):
    """Matching without any AI."""

    def test_arabic_is_folded(self):
        self.assertEqual(
            memory_match.words("كَيْفَ أُنْشِئُ الفاتورةَ؟"),
            memory_match.words("كيف أنشئ فاتوره"))
        self.assertEqual(memory_match.fold("إأآ ى ة ـ"), "ااا ي ه ")
        self.assertEqual(memory_match.language("كيف أنشئ فاتورة"), "ar")
        self.assertEqual(memory_match.language("How to"), "en")

    def test_english_is_stemmed(self):
        self.assertEqual(memory_match.words("Creating quotations"), memory_match.words("create quotation"))

    def test_the_best_is_found_and_ties_are_not_answered(self):
        candidates = [(1, HOW), (2, "How do I confirm a sales order?")]
        self.assertEqual(memory_match.best(SAME, candidates, 0.75)[0], 1)
        self.assertIsNone(memory_match.best("weather in Paris", candidates, 0.75)[0])
        twins = [(1, HOW), (2, HOW)]
        self.assertIsNone(memory_match.best(HOW, twins, 0.75)[0], "Two equal answers to choose from: ask the AI")


@tagged("post_install", "-at_install")
class TestAnswerMemory(NudgeCase, TransactionCase):

    def setUp(self):
        super().setUp()
        self.Memory = self.env["nudge.memory"]
        self.env["ir.config_parameter"].sudo().set_param("nudge.answer_memory", "True")

    def remember(self, state="approved", **values):
        return self.Memory.create({"question": HOW, "answer": STEPS, "state": state, **values})

    def answer_of(self, user, text):
        self.use_adapter(reply=STEPS)
        conversation, _last = self.ask(user, text)
        return conversation, conversation.sudo().message_ids.filtered(lambda m: m.role == "assistant")[-1]

    def test_a_thumbs_up_waits_for_a_manager(self):
        conversation, answer = self.answer_of(self.user_a, HOW)
        answer.with_user(self.user_a).rate("up")
        memory = self.Memory.search([("conversation_id", "=", conversation.id)])
        self.assertEqual((memory.state, memory.question, memory.answer), ("proposed", HOW, STEPS))
        self.assertEqual(memory.proposed_by, self.user_a)
        self.assertIsNone(self.Memory.with_user(self.user_b)._find(SAME)[0], "Nothing is served before the yes")
        answer.with_user(self.user_a).rate("up")
        self.assertEqual(self.Memory.search_count([("conversation_id", "=", conversation.id)]), 1)

    def test_a_manager_thumbs_up_counts_at_once(self):
        conversation, answer = self.answer_of(self.manager, HOW)
        answer.with_user(self.manager).rate("up")
        self.assertEqual(self.Memory.search([("conversation_id", "=", conversation.id)]).state, "approved")

    def test_what_changed_or_showed_data_is_not_kept(self):
        for label, question_values, call in (
                ("a screen was shared", {"snapshot": {"model": "res.partner"}}, None),
                ("a record was read", {}, "read_record"),
                ("something was written", {}, "prepare_write"),
                ("agent mode", {"mode": "agent"}, None),
                ("a command", {"command": "count"}, None)):
            conversation = self.env["nudge.conversation"].with_user(self.user_a)._chat_open()
            Message = self.env["nudge.message"].sudo()
            Message.create({"conversation_id": conversation.id, "role": "user", "content": f"{HOW} {label}",
                            **question_values})
            answer = Message.create({
                "conversation_id": conversation.id, "role": "assistant", "content": STEPS,
                "tool_calls": [{"id": "1", "name": call, "input": {}}] if call else False})
            answer.with_user(self.user_a).rate("up")
            self.assertFalse(self.Memory.search([("conversation_id", "=", conversation.id)]), label)

    def test_documentation_tools_are_kept(self):
        conversation = self.env["nudge.conversation"].with_user(self.user_a)._chat_open()
        Message = self.env["nudge.message"].sudo()
        Message.create({"conversation_id": conversation.id, "role": "user", "content": HOW})
        Message.create({"conversation_id": conversation.id, "role": "assistant", "content": "",
                        "tool_calls": [{"id": "1", "name": "search_odoo_documentation", "input": {}}]})
        answer = Message.create({"conversation_id": conversation.id, "role": "assistant", "content": STEPS})
        answer.with_user(self.user_a).rate("up")
        self.assertTrue(self.Memory.search([("conversation_id", "=", conversation.id)]))

    def test_the_memory_answers_before_the_provider(self):
        memory = self.remember()
        adapter = self.use_adapter(reply="From the AI")
        conversation, last = self.ask(self.user_b, SAME)
        self.assertEqual(adapter.calls, [], "No provider was called")
        self.assertEqual(last["type"], "done")
        self.assertEqual(last["memory"], {"id": memory.id})
        answer = conversation.sudo().message_ids.filtered(lambda m: m.role == "assistant")
        self.assertEqual(answer.content, STEPS)
        self.assertTrue(answer.is_remembered)
        self.assertEqual((memory.uses, bool(memory.last_used)), (1, True))
        usage = self.env["nudge.usage"].search([("kind", "=", "memory"), ("message_id", "=", answer.id)])
        self.assertEqual((usage.tokens, usage.cost, usage.user_id), (0, 0.0, self.user_b))

    def test_ask_the_ai_instead(self):
        self.remember()
        adapter = self.use_adapter(reply="From the AI")
        conversation = self.env["nudge.conversation"].with_user(self.user_b)._chat_open()
        job = conversation._chat_prepare(SAME, fresh=True)
        list(loop.run(job, conversation._chat_record))
        self.assertEqual(len(adapter.calls), 1)

    def test_not_confident_goes_to_the_provider(self):
        self.remember()
        adapter = self.use_adapter(reply="From the AI")
        self.ask(self.user_b, "Where is the weather report of Paris?")
        self.assertEqual(len(adapter.calls), 1)

    def test_only_what_the_person_may_see(self):
        group = self.env.ref("base.group_system")
        self.remember(group_ids=[(6, 0, group.ids)])
        adapter = self.use_adapter(reply="From the AI")
        self.ask(self.user_b, SAME)
        self.assertEqual(len(adapter.calls), 1, "A group they are not in")
        other = self.env["res.company"].create({"name": "Other Company"})
        self.Memory.search([]).unlink()
        self.remember(company_id=other.id)
        self.ask(self.user_b, SAME)
        self.assertEqual(len(adapter.calls), 2, "Another company")

    def test_proposed_and_refused_are_not_served(self):
        for state in ("proposed", "refused"):
            self.Memory.search([]).unlink()
            self.remember(state=state)
            adapter = self.use_adapter(reply="From the AI")
            self.ask(self.user_b, SAME)
            self.assertEqual(len(adapter.calls), 1, state)

    def test_switched_off(self):
        self.remember()
        self.env["ir.config_parameter"].sudo().set_param("nudge.answer_memory", "False")
        adapter = self.use_adapter(reply="From the AI")
        self.ask(self.user_b, SAME)
        self.assertEqual(len(adapter.calls), 1)
        conversation, answer = self.answer_of(self.manager, "How do I print an invoice?")
        answer.with_user(self.manager).rate("up")
        self.assertFalse(self.Memory.search([("conversation_id", "=", conversation.id)]), "Nothing is kept either")
        self.assertFalse(self.Memory.with_user(self.user_b).suggest(SAME))

    def test_without_a_provider_the_best_above_the_floor_is_offered(self):
        self.remember()
        self.env["nudge.provider"].search([]).write({"active": False})
        suggestion = self.Memory.with_user(self.user_b).suggest("create quotation")
        self.assertEqual(suggestion["answer"], STEPS)
        self.assertFalse(self.Memory.with_user(self.user_b).suggest("weather of Paris"))

    def test_people_do_not_read_the_table(self):
        memory = self.remember()
        with self.assertRaises(AccessError):
            memory.with_user(self.user_a).read(["answer"])
        memory.with_user(self.manager).write({"answer": "Edited by a manager"})
        self.assertEqual(memory.answer, "Edited by a manager")

    def test_a_plain_user_cannot_approve_edit_or_read_remembered_answers(self):
        memory = self.remember(state="proposed")
        for user in (self.user_a, self.outsider):
            with self.assertRaises(AccessError):
                memory.with_user(user).action_approve()
            with self.assertRaises(AccessError):
                memory.with_user(user).write({"answer": "Raghad changed it"})
            with self.assertRaises(AccessError):
                self.Memory.with_user(user).search([])
        self.assertEqual((memory.state, memory.answer), ("proposed", STEPS))

    def test_the_answers_of_another_company_are_not_served(self):
        company_b = self.env["res.company"].create({"name": "Ebrahim Memory Co"})
        self.remember()
        reader_b = new_test_user(
            self.env, login="nudge_mem_b", groups="nudge.group_nudge_user", company_id=company_b.id,
            company_ids=[(6, 0, company_b.ids)])
        self.assertIsNone(self.Memory.with_user(reader_b)._find(SAME)[0], "Company A's answer is not company B's")
        self.assertFalse(self.Memory.with_user(reader_b).suggest(SAME))


@tagged("post_install", "-at_install")
class TestMemoryDefaults(TransactionCase):
    """Answer memory is on after an install, and a choice made in Settings is never overwritten."""

    def test_the_defaults_fill_only_what_is_not_set(self):
        Parameter = self.env["ir.config_parameter"].sudo()
        Memory = self.env["nudge.memory"]
        for key in ("nudge.answer_memory", "nudge.memory_threshold"):
            Parameter.search([("key", "=", key)]).unlink()
        Memory._set_defaults()
        self.assertTrue(Memory._is_enabled(), "On when nothing was chosen")
        self.assertEqual(Parameter.get_param("nudge.memory_threshold"), "0.75")
        Parameter.set_param("nudge.memory_threshold", "0.9")
        Parameter.set_param("nudge.answer_memory", "0")
        Memory._set_defaults()
        self.assertFalse(Memory._is_enabled(), "Raghad's choice stays")
        self.assertEqual(Parameter.get_param("nudge.memory_threshold"), "0.9")

    def test_common_requests_without_ai_are_on_when_nothing_was_chosen(self):
        Parameter = self.env["ir.config_parameter"].sudo()
        Noai = self.env["nudge.noai"]
        Parameter.search([("key", "=", "nudge.noai_first")]).unlink()
        self.assertFalse(Noai._is_enabled(), "Unset is not a choice the code may read as on")
        self.env["nudge.memory"]._set_defaults()
        self.assertTrue(Noai._is_enabled(), "On after the defaults, as the README says")
        Parameter.set_param("nudge.noai_first", "0")
        self.env["nudge.memory"]._set_defaults()
        self.assertFalse(Noai._is_enabled(), "An explicit off stays off")
