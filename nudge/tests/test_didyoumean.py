"""NP-11 and NP-12: the sentence nearly understood is offered back, the day of the person is counted
by rules, the manager sees what the rules missed, and an Arabic question finds an English page."""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.tools import noai_commands
from odoo.addons.nudge.tools.noai_commands import bridge

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestDidYouMean(NudgeCase, TransactionCase):
    """The chips and the agenda: rules only, no provider."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.raghad = new_test_user(
            cls.env, login="nudge_dym_raghad", groups="nudge.group_nudge_agent,base.group_partner_manager")
        cls.ebrahim = new_test_user(cls.env, login="nudge_dym_ebrahim", groups="nudge.group_nudge_user")
        cls.worker = new_test_user(cls.env, login="nudge_dym_worker", groups="nudge.group_nudge_agent")
        # Not a provider: whatever reaches the model would fail
        cls.provider.active = False

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("nudge.noai_first", "1")

    def text_of(self, events):
        return "".join(event.get("text", "") for event in events if event["type"] == "delta")

    def commands_of(self, events):
        return [command for event in events if event["type"] == "found" for command in event.get("commands") or []]

    def test_a_slip_in_a_write_is_offered_back_and_nothing_is_done(self):
        before = self.env["res.partner"].search_count([("name", "=", "Ebrahim Ali")])
        _conversation, events = self.order(self.raghad, "creat contact Ebrahim Ali")
        chips = self.commands_of(events)
        self.assertEqual(len(chips), 1, events)
        self.assertEqual(chips[0]["words"], ["create contact Ebrahim Ali"])
        self.assertTrue(chips[0]["at_once"], "Complete: it is sent as it is when chosen")
        self.assertIn("Did you mean", self.text_of(events))
        self.assertFalse([event for event in events if event["type"] in ("confirm", "waiting")], "A chip never executes")
        self.assertEqual(self.env["res.partner"].search_count([("name", "=", "Ebrahim Ali")]), before)

    def test_the_chat_can_ask_whether_a_text_is_a_slip_before_it_asks_how(self):
        # NP-05: "sales ordrs" got the choice of how first and its "Did you mean" only after
        noai = self.env["nudge.noai"].with_user(self.raghad)
        for slip in ("sales ordrs", "qoutations"):
            self.assertTrue(noai.slip_offered(slip), slip)
        for text in ("Thanks!", "First", "Once more", "", None, "sales ordrs " + "x" * 200):
            self.assertFalse(noai.slip_offered(text), text)

    def test_the_chosen_sentence_is_understood_as_asked(self):
        _conversation, events = self.order(self.raghad, "creat contact Ebrahim Ali")
        sentence = self.commands_of(events)[0]["words"][0]
        _conversation, events = self.order(self.raghad, sentence)
        self.assertEqual(len([event for event in events if event["type"] == "confirm"]), 1, events)

    def test_what_is_missing_is_asked(self):
        _conversation, events = self.order(self.raghad, "create contct")
        chips = self.commands_of(events)
        self.assertTrue(chips and not chips[0]["at_once"], events)
        self.assertTrue(chips[0]["words"][0].endswith(" "), "Put in the message box, to be finished")
        self.assertIn("What is its name?", self.text_of(events))

    def test_talk_does_not_offer_a_write(self):
        conversation, _last = self.ask(self.raghad, "creat contact Ebrahim Ali")
        shown = [event for message in conversation.message_ids for event in message.screen_events or []]
        self.assertFalse([event for event in shown if event.get("commands")], shown)

    def test_the_agenda_is_counted_by_rules_as_the_person(self):
        # Today as the person counts it (their zone): near midnight it is not the test's own day
        today = fields.Date.context_today(self.env["res.users"].with_user(self.raghad))
        partner = self.env["res.partner"].create({"name": "Raghad Partner"})
        Activity = self.env["mail.activity"].sudo()
        kind = self.env.ref("mail.mail_activity_data_todo")
        for user, days in ((self.raghad, 0), (self.raghad, -2), (self.worker, 0)):
            Activity.create({
                "res_model_id": self.env["ir.model"]._get_id("res.partner"), "res_id": partner.id,
                "activity_type_id": kind.id, "user_id": user.id, "date_deadline": today + timedelta(days=days)})
        _conversation, events = self.order(self.raghad, "what is waiting for me today")
        text = self.text_of(events)
        self.assertIn("Activities due today: 1", text)
        self.assertIn("Activities overdue: 1", text)
        self.assertFalse(self.env["nudge.step"].search([("conversation_id.user_id", "=", self.raghad.id)]),
                         "No tool and no provider were used")
        _conversation, events = self.order(self.worker, "what do I have today")
        self.assertIn("Activities due today: 1", self.text_of(events))
        self.assertNotIn("overdue", self.text_of(events))

    def test_the_agenda_says_when_nothing_waits(self):
        _conversation, events = self.order(self.raghad, "what is waiting for me today")
        self.assertIn("Nothing is waiting for you today.", self.text_of(events))

    def test_a_question_about_one_kind_of_record_is_not_the_agenda(self):
        self.assertFalse(noai_commands.agenda.asks("invoices due today"))
        self.assertTrue(noai_commands.agenda.asks("ايش عندي اليوم"))


@tagged("post_install", "-at_install")
class TestCoverage(NudgeCase, TransactionCase):
    """NP-12: the sentences the rules missed, for a manager only."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider.active = False
        cls.Coverage = cls.env["nudge.coverage"]

    def missed(self, text, times=3):
        """A sentence counted ``times`` times in the company of the manager"""
        question = self.env["nudge.question"].with_user(self.manager)
        for _index in range(times):
            question._record(text)
        return self.env["nudge.question"].search([("sample", "=", text)])

    def lines(self):
        return self.Coverage.with_user(self.manager).search([("create_uid", "=", self.manager.id)])

    def test_only_a_manager_opens_the_page(self):
        for user in (self.user_a, self.agent, self.outsider):
            with self.subTest(user=user.login), self.assertRaises(AccessError):
                self.Coverage.with_user(user).action_open()
        action = self.Coverage.with_user(self.manager).action_open()
        self.assertEqual(action["res_model"], "nudge.coverage")

    def test_a_nudge_user_cannot_read_or_make_lines(self):
        with self.assertRaises(AccessError):
            self.Coverage.with_user(self.user_a).create({"sample": "x"})

    def test_a_missed_sentence_is_listed_and_a_understood_one_is_not(self):
        self.missed("please zorb the invoices", 4)
        self.missed("open invoices", 6)
        self.Coverage.with_user(self.manager).action_open()
        samples = self.lines().mapped("sample")
        self.assertIn("please zorb the invoices", samples)
        self.assertNotIn("open invoices", samples)
        line = self.lines().filtered(lambda one: one.sample == "please zorb the invoices")
        self.assertEqual((line.times, line.phrase), (4, "zorb"))

    def test_the_sample_stays_redacted(self):
        self.missed("zorb raghad@example.com 0501234567890", 2)
        self.Coverage.with_user(self.manager).action_open()
        for sample in self.lines().mapped("sample"):
            self.assertNotIn("raghad@example.com", sample)
            self.assertNotIn("0501234567890", sample)

    def test_a_common_word_is_not_proposed_nor_accepted_as_an_entry(self):
        # NP-12: "Make entry" proposed "do" = open
        self.missed("please do zorb the invoices", 4)
        self.Coverage.with_user(self.manager).action_open()
        line = self.lines().filtered(lambda one: one.sample == "please do zorb the invoices")
        self.assertEqual(line.phrase, "zorb")
        entries = self.env["nudge.lexicon.entry"].search_count([])
        for word in ("do", "the", "Make", "show", "هل"):
            with self.subTest(word=word):
                line.with_user(self.manager).write({"phrase": word, "intent": "open"})
                with self.assertRaises(UserError):
                    line.with_user(self.manager).action_add_entry()
        self.assertEqual(self.env["nudge.lexicon.entry"].search_count([]), entries)
        # Several words are a phrase, not a generic word
        line.with_user(self.manager).write({"phrase": "do zorb", "intent": "open"})
        line.with_user(self.manager).action_add_entry()

    def test_an_entry_added_from_the_page_changes_the_next_parse(self):
        self.missed("please zorb the invoices", 4)
        self.Coverage.with_user(self.manager).action_open()
        line = self.lines().filtered(lambda one: one.sample == "please zorb the invoices")
        self.assertEqual(line.intent, "open", "A sentence that names a kind of record is guessed to open it")
        line.with_user(self.manager).write({"intent": False})
        with self.assertRaises(UserError):
            line.with_user(self.manager).action_add_entry()
        line.with_user(self.manager).write({"phrase": "zorb", "intent": "open"})
        with self.env["nudge.noai"]._company_words():
            before = noai_commands.parse("please zorb the invoices", {})
        self.assertLess(before.confidence, noai_commands.THRESHOLD)
        notice = line.with_user(self.manager).action_add_entry()
        self.assertEqual(notice["params"]["type"], "success", notice)
        self.assertEqual(line.state, "added")
        with self.env["nudge.noai"].with_user(self.manager)._company_words():
            after = noai_commands.parse("please zorb the invoices", {})
        self.assertEqual((after.intent, after.kind), ("open", "invoice"))
        self.assertGreaterEqual(after.confidence, noai_commands.THRESHOLD)
        # The same word is not added twice
        entries = self.env["nudge.lexicon.entry"].search([("synonym", "=", "zorb")])
        self.assertEqual(len(entries), 1)

    def test_a_key_a_password_or_a_number_typed_in_a_question_is_not_kept(self):
        secrets = (
            "please zorb the invoices with key sk-ant-api03-AbCdEf123456789xyz",
            "please zorb the invoices password hunter2 for Raghad",
            "please zorb the invoices for ebrahim@example.com on 0501234567",
            "please zorb the invoices token AbCdEfGh1234567890XyZq",
        )
        for text in secrets:
            self.env["nudge.question"].with_user(self.manager)._record(text)
        samples = self.env["nudge.question"].search([("sample", "like", "zorb")]).mapped("sample")
        self.assertTrue(samples)
        for sample in samples:
            for leaked in ("sk-ant", "hunter2", "ebrahim@", "0501234567", "AbCdEfGh1234567890"):
                self.assertNotIn(leaked, sample)
        self.assertTrue(all(sample.startswith("please zorb the invoices") for sample in samples))

    def test_nothing_is_recorded_in_private_mode(self):
        self.provider.active = True
        self.provider.runs_locally = True
        self.env["ir.config_parameter"].sudo().set_param("nudge.default_provider", str(self.provider.id))
        self.use_adapter()
        self.env.company.nudge_private_mode = True
        before = self.env["nudge.question"].search_count([])
        self.ask(self.user_a, "please zorb the invoices of Raghad twice")
        self.assertEqual(self.env["nudge.question"].search_count([]), before)
        self.env.company.nudge_private_mode = False
        self.ask(self.user_a, "please zorb the invoices of Raghad twice")
        self.assertEqual(self.env["nudge.question"].search_count([]), before + 1)

    def test_a_manager_sees_only_the_sentences_of_their_companies(self):
        other = self.env["res.company"].create({"name": "Another company"})
        self.missed("please zorb the invoices", 3)
        self.env["nudge.question"].with_company(other)._record("please blorp the quotations")
        row = self.env["nudge.question"].sudo().search([("sample", "=", "please blorp the quotations")])
        self.assertEqual(row.company_id, other)
        seen = self.env["nudge.question"].with_user(self.manager).search([]).mapped("sample")
        self.assertIn("please zorb the invoices", seen)
        self.assertNotIn("please blorp the quotations", seen)
        self.Coverage.with_user(self.manager).action_open()
        self.assertNotIn("please blorp the quotations", self.lines().mapped("sample"))

    def test_a_remembered_answer_is_saved_from_the_page(self):
        self.missed("please zorb the invoices", 4)
        self.Coverage.with_user(self.manager).action_open()
        line = self.lines().filtered(lambda one: one.sample == "please zorb the invoices")
        action = line.with_user(self.manager).action_keep_answer()
        self.assertEqual(action["res_model"], "nudge.memory")
        self.assertEqual(action["context"]["default_question"], "please zorb the invoices")


@tagged("post_install", "-at_install")
class TestBridge(NudgeCase, TransactionCase):
    """NP-12: an Arabic question finds the English pages."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        page = cls.env["nudge.doc.page"].sudo().create({
            "path": "applications/finance/didyoumean_tax_test", "url": "https://example.invalid/tax",
            "category": "didyoumean_app", "app": "didyoumean_app", "title": "Zxqtax rules"})
        cls.passage = cls.env["nudge.doc.passage"].sudo().create({
            "page_id": page.id, "heading": "Zxqtax", "body": "How to configure the tax of your invoices zxqtax.",
            "title": page.title, "app": page.app})

    def test_the_words_of_an_arabic_question_become_english(self):
        self.assertEqual(bridge.terms("كيف أنشئ فاتورة مورد"), ["vendor", "bill"])
        self.assertIn("tax", bridge.terms("كيف اضبط الضريبة"))
        self.assertEqual(bridge.terms("كيف"), [])

    def test_a_word_the_company_added_is_carried_too(self):
        self.assertEqual(bridge.terms("ريّح الفاتورة"), ["invoice"])
        words = bridge.terms("ريّحلي الحسبة", [("الحسبة", "invoice")])
        self.assertIn("invoice", words)

    def test_an_arabic_question_finds_an_english_page(self):
        Passage = self.env["nudge.doc.passage"].with_user(self.user_a)
        found = Passage._search_text("كيف اضبط الضريبة", app="didyoumean_app")
        self.assertIn(self.passage, found)

    def test_the_words_of_the_company_find_the_page_too(self):
        Passage = self.env["nudge.doc.passage"].with_user(self.user_a)
        self.assertNotIn(self.passage, Passage._search_text("وين الحسبة", app="didyoumean_app"))
        self.env["nudge.lexicon.entry"].create({"synonym": "الحسبة", "intent": "invoice"})
        self.assertIn(self.passage, Passage._search_text("وين الحسبة", app="didyoumean_app"))

    def test_an_english_question_is_searched_as_before(self):
        found = self.env["nudge.doc.passage"].with_user(self.user_a)._search_text("zxqtax", app="didyoumean_app")
        self.assertIn(self.passage, found)
