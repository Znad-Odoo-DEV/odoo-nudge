from datetime import timedelta

from odoo import SUPERUSER_ID, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge.llm import fingerprint
from odoo.addons.nudge.models import nudge_question as question_module

from .common import NudgeCase

INVOICE_EN = "How do I create an invoice?"
INVOICE_AR = "شلون أسوي فاتورة؟"

SAME_QUESTION = [
    ("كيف أنشئ فاتورة جديدة؟", "شلون أسوي فاتورة"),
    ("كيف بعمل فاتورة؟", "ازاي اعمل فاتورة"),
    ("How do I create an invoice?", "how can i make a new invoice please"),
    ("كيف أطبع الفاتورة", "شلون اطبع فاتورتي"),
    ("كيف أضيف عميل جديد", "ازاي اضيف زبون"),
    ("كيف أحذف الموظف", "شلون امسح موظف"),
    ("كيف الغي طلب الشراء", "how to cancel a purchase order"),
    ("كيف أنشئ عرض سعر", "شلون اسوي عرض سعر؟"),
    ("كيف أغير كلمة المرور", "ازاي اغير الباسورد"),
    ("how to export products", "كيف اصدّر المنتجات"),
    ("Where can I print the salary?", "كيف اطبع الراتب؟"),
]


@tagged("post_install", "-at_install", "nudge")
class TestFingerprint(TransactionCase):

    def test_dialects_give_the_same_fingerprint(self):
        for first, second in SAME_QUESTION:
            with self.subTest(first=first, second=second):
                self.assertTrue(fingerprint.of(first), first)
                self.assertEqual(fingerprint.of(first), fingerprint.of(second))

    def test_other_questions_differ(self):
        self.assertNotEqual(fingerprint.of("كيف أنشئ فاتورة"), fingerprint.of("كيف أحذف فاتورة"))
        self.assertNotEqual(fingerprint.of("how to create an invoice"), fingerprint.of("how to create a customer"))
        # Word order and politeness change nothing
        self.assertEqual(fingerprint.of("invoice, how to create one please"), fingerprint.of("create invoice"))

    def test_too_short_has_no_fingerprint(self):
        self.assertIsNone(fingerprint.of("hello"))
        self.assertIsNone(fingerprint.of("كيف؟"))

    def test_sample_keeps_no_address_or_number(self):
        text = fingerprint.sample("Send it to raghad@example.com or call 0501234567 about invoice")
        self.assertNotIn("@", text)
        self.assertNotIn("0501234567", text)
        text = fingerprint.sample("call me on +971 50 123 4567 or 050-123-4567 or (04) 555 1234 for 3 invoices")
        for digits in ("971", "4567", "555 1234"):
            self.assertNotIn(digits, text)
        self.assertIn("3 invoices", text)


@tagged("post_install", "-at_install", "nudge")
class TestRecurringQuestions(NudgeCase, TransactionCase):

    def setUp(self):
        super().setUp()
        self.Question = self.env["nudge.question"]
        self.env["ir.config_parameter"].sudo().set_param("nudge.recurring_min_count", "5")

    def asked(self, text, times, user=None):
        for _ in range(times):
            self.Question.with_user(user or self.user_a)._record(text)

    def row(self, text=INVOICE_EN):
        return self.Question.sudo().with_context(active_test=False).search(
            [("fingerprint", "=", fingerprint.of(text))])

    def test_counts_by_meaning_and_keeps_nobody(self):
        adapter = self.use_adapter(reply="Open Invoicing.")
        self.ask(self.user_a, INVOICE_EN)
        self.ask(self.user_b, INVOICE_AR)
        self.ask(self.user_a, "كيف بعمل فاتورة")
        row = self.row()
        self.assertEqual(len(row), 1)
        self.assertEqual(row.count, 3)
        self.assertEqual(row.recent_count, 3)
        self.assertEqual(row.company_id, self.env.company)
        self.assertEqual((row.create_uid.id, row.write_uid.id), (SUPERUSER_ID, SUPERUSER_ID),
                         "No person is kept with a counted question")
        self.assertFalse([name for name, field in row._fields.items()
                          if field.comodel_name == "res.users" and name not in ("create_uid", "write_uid")])
        self.assertEqual(len(adapter.calls), 3, "Only the answers called the provider")

    def test_the_counter_adds_to_what_is_written_already(self):
        """Release-2 review S14: a row written by another worker a moment ago is added to, not replaced"""
        self.asked(INVOICE_EN, 1)
        row = self.row()
        # another worker counted twice since this one read the row
        self.env.cr.execute("UPDATE nudge_question SET count = count + 2 WHERE id = %s", [row.id])
        self.asked(INVOICE_EN, 1)
        row.invalidate_recordset()
        self.assertEqual(row.count, 4)
        self.assertEqual(self.Question.sudo().with_context(active_test=False).search_count(
            [("fingerprint", "=", row.fingerprint)]), 1, "one row for a question")

    def test_proposed_above_the_threshold_only(self):
        adapter = self.use_adapter()
        self.asked(INVOICE_EN, 4)
        self.asked("كيف أنشئ عميل جديد", 5)
        self.Question.with_user(self.manager).action_find()
        self.assertEqual(self.row().status, "watching")
        self.assertEqual(self.row("كيف أنشئ عميل").status, "proposed")
        self.asked(INVOICE_AR, 1)
        self.Question.with_user(self.manager).action_find()
        self.assertEqual(self.row().status, "proposed")
        self.assertFalse(adapter.calls, "Nothing calls a provider")

    def test_covered_by_a_remembered_answer_is_not_proposed(self):
        self.asked(INVOICE_AR, 6)
        self.asked("How do I handle overdue invoice reminders quickly", 6)
        self.env["nudge.memory"].create({
            "question": "How can I create an invoice", "answer": "Open Invoicing, press New.", "state": "approved"})
        self.env["nudge.memory"].create({
            "question": "How to handle overdue invoice reminders", "answer": "Use the follow-ups.",
            "state": "approved"})
        self.Question.with_user(self.manager).action_find()
        by_fingerprint = self.row()
        self.assertEqual(by_fingerprint.status, "covered")
        self.assertIn("Remembered answer", by_fingerprint.covered_by)
        by_words = self.row("How do I handle overdue invoice reminders quickly")
        self.assertEqual(by_words.status, "covered", "The same TF-IDF as the answer memory")
        # A proposed answer that nobody approved covers nothing
        self.env["nudge.memory"].search([("question", "=", "How can I create an invoice")]).state = "proposed"
        self.Question.with_user(self.manager).action_find()
        self.assertEqual(self.row().status, "proposed")

    def test_covered_by_a_recorded_tour_is_not_proposed(self):
        self.asked("كيف أنشئ عميل جديد", 6)
        self.env["nudge.tour"].create({
            "name": "New contact", "phrases_en": "create a customer for Raghad", "phrases_ar": "انشاء عميل"})
        self.Question.with_user(self.manager).action_find()
        row = self.row("كيف أنشئ عميل")
        self.assertEqual(row.status, "covered")
        self.assertIn("Tour", row.covered_by)

    def test_ignoring_mutes_the_question(self):
        self.asked(INVOICE_EN, 6)
        self.Question.with_user(self.manager).action_find()
        row = self.row()
        self.assertEqual(row.status, "proposed")
        row.with_user(self.manager).action_ignore()
        self.assertEqual(row.status, "muted")
        self.asked(INVOICE_AR, 3)
        self.Question.with_user(self.manager).action_find()
        self.assertEqual(row.status, "muted", "Asked again, it stays ignored")
        row.with_user(self.manager).action_watch()
        self.Question.with_user(self.manager).action_find()
        self.assertEqual(row.status, "proposed")

    def test_retention_archives_and_nothing_is_deleted(self):
        self.env["ir.config_parameter"].sudo().set_param("nudge.recurring_retention_days", "90")
        self.asked(INVOICE_EN, 6)
        row = self.row()
        row.last_asked = fields.Datetime.now() - timedelta(days=100)
        self.Question.with_user(self.manager).action_find()
        self.assertFalse(row.active)
        self.assertTrue(row.exists())
        with self.assertRaises(UserError):
            row.with_user(self.manager).unlink()
        # Asked again: counted again, from nothing
        self.asked(INVOICE_AR, 1)
        self.assertTrue(row.active)
        self.assertEqual(row.count, 1)
        self.assertEqual(row.status, "watching")

    def test_a_question_archived_for_twice_the_retention_is_collected(self):
        self.env["ir.config_parameter"].sudo().set_param("nudge.recurring_retention_days", "90")
        self.asked(INVOICE_EN, 1)
        row = self.row()
        row.write({"active": False, "last_asked": fields.Datetime.now() - timedelta(days=100)})
        self.Question._gc_old()
        self.assertTrue(row.exists(), "Archived, but not yet for twice the retention")
        row.last_asked = fields.Datetime.now() - timedelta(days=181)
        self.Question._gc_old()
        self.assertFalse(row.exists())

    def test_old_days_leave_the_month(self):
        self.asked(INVOICE_EN, 6)
        row = self.row()
        old = (fields.Date.context_today(row) - timedelta(days=45)).isoformat()
        row.daily = '{"%s": 20, "%s": 6}' % (old, fields.Date.context_today(row).isoformat())
        row.recent_count = 26
        self.Question.with_user(self.manager).action_find()
        self.assertEqual(row.recent_count, 6)

    def test_only_managers_decide(self):
        self.asked(INVOICE_EN, 6)
        with self.assertRaises(AccessError):
            self.Question.with_user(self.user_a).action_find()
        with self.assertRaises(AccessError):
            self.row().with_user(self.user_a).action_ignore()

    def test_managers_are_told_once(self):
        told = []
        self.patch(question_module, "notify_user",
                   lambda user, kind, message, **kwargs: told.append((user.id, kind, message)))
        self.asked(INVOICE_EN, 6)
        self.Question.with_user(self.manager).action_find()
        self.assertIn(self.manager.id, [user for user, _kind, _message in told])
        message = [message for user, _kind, message in told if user == self.manager.id][0]
        self.assertIn("6 times this month", message["message"])
        count = len(told)
        self.Question.with_user(self.manager).action_find()
        self.assertEqual(len(told), count, "A question already proposed is not announced again")

    def test_weekly_cron_does_the_same(self):
        self.asked(INVOICE_EN, 6)
        self.Question.with_context(nudge_keep_transaction=True)._cron_find()
        self.assertEqual(self.row().status, "proposed")

    def test_keep_an_answer_opens_the_form_with_the_best_past_answer(self):
        self.use_adapter(reply="Open Invoicing, then press New.")
        conversation, _last = self.ask(self.user_a, INVOICE_EN)
        answer = conversation.sudo().message_ids.filtered(lambda message: message.role == "assistant")[-1]
        answer.feedback = "up"
        self.asked(INVOICE_AR, 5)
        row = self.row()
        action = row.with_user(self.manager).action_keep_answer()
        self.assertEqual(action["res_model"], "nudge.memory")
        self.assertEqual(action["context"]["default_answer"], "Open Invoicing, then press New.")
        self.assertEqual(action["context"]["default_question"], row.sample)

    def test_record_a_tour_starts_the_recorder(self):
        self.asked(INVOICE_EN, 5)
        action = self.row().with_user(self.manager).action_record_tour()
        self.assertEqual(action["tag"], "nudge_record_tour")
        self.assertEqual(action["params"]["phrases_en"], self.row().sample)
        self.assertEqual(action["params"]["phrases_ar"], "")
        self.asked("كيف أنشئ عميل جديد", 5)
        arabic = self.row("كيف أنشئ عميل").with_user(self.manager).action_record_tour()
        self.assertEqual(arabic["params"]["phrases_en"], "")
        self.assertTrue(arabic["params"]["phrases_ar"])

    def test_settings_move_the_threshold(self):
        self.asked(INVOICE_EN, 3)
        self.env["ir.config_parameter"].sudo().set_param("nudge.recurring_min_count", "3")
        self.Question.with_user(self.manager).action_find()
        self.assertEqual(self.row().status, "proposed")
