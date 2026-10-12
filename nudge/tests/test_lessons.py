from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import prompt

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestLessons(NudgeCase, TransactionCase):
    """What people correct becomes a lesson, once a manager says so."""

    def remember(self, lesson, why="They said so", user=None):
        return tools.base.call(self.env(user=user or self.user_a), "remember_lesson",
                               {"lesson": lesson, "why": why})

    def test_a_lesson_waits_for_a_manager(self):
        result = self.remember("Salaries live in acme.payslip (acme_payroll), not in hr.payslip.",
                               "They said the payroll module is theirs")
        self.assertFalse(result.is_error, result.content)
        self.assertIn("approve", result.content)
        lesson = self.env["nudge.note"].sudo().search([("kind", "=", "lesson")], order="id desc", limit=1)
        self.assertEqual(lesson.state, "proposed")
        self.assertEqual(lesson.proposed_by, self.user_a)
        self.assertEqual(lesson.reason, "They said the payroll module is theirs")
        self.assertIn("acme.payslip", lesson.body)
        text = self.env["nudge.note"].sudo()._get_text()
        self.assertNotIn("acme.payslip", text, "Nothing is learnt before the yes")

        lesson.with_user(self.manager).action_approve()
        self.assertEqual(lesson.state, "approved")
        self.assertEqual(lesson.approved_by, self.manager)
        self.assertIn("acme.payslip", self.env["nudge.note"].sudo()._get_text())
        self.assertIn("acme.payslip", self.env["nudge.profile"]._get_overview())

        lesson.with_user(self.manager).action_refuse()
        self.assertEqual(lesson.state, "refused")
        self.assertNotIn("acme.payslip", self.env["nudge.note"].sudo()._get_text())

    def test_the_same_lesson_twice_is_once(self):
        self.remember("We call quotations offers in this company.")
        again = self.remember("We call quotations offers in this company.")
        self.assertIn("already written down", again.content)
        self.assertEqual(self.env["nudge.note"].sudo().search_count(
            [("kind", "=", "lesson"), ("body", "ilike", "offers")]), 1)

    def test_notes_of_managers_count_at_once(self):
        note = self.env["nudge.note"].with_user(self.manager).create(
            {"name": "Words", "body": "Deliveries are called dispatches here."})
        self.assertEqual(note.kind, "note")
        self.assertEqual(note.state, "approved")
        self.assertIn("dispatches", self.env["nudge.note"].sudo()._get_text())

    def test_only_managers_read_the_lessons(self):
        self.remember("Something to learn about this business.")
        with self.assertRaises(AccessError):
            self.env["nudge.note"].with_user(self.user_a).search([]).read(["body"])
        self.assertTrue(self.env["nudge.note"].with_user(self.manager).search([("kind", "=", "lesson")]))

    def test_asked_to_remember_the_words_are_the_persons(self):
        # "تذكّر: الخصم عنا ما بيتجاوز 10%" was told back as "a discount above
        # 10% is a must", and nothing was written down
        self.assertEqual(tools.lessons.taught("Remember: our discount never goes above 10% on any quotation"),
                         "our discount never goes above 10% on any quotation")
        self.assertEqual(tools.lessons.taught("تذكّر: الخصم عنا ما بيتجاوز 10%"), "الخصم عنا ما بيتجاوز 10%")
        for text in ("How do I remember a password?", "Remembering is hard", "remember"):
            self.assertEqual(tools.lessons.taught(text), "", text)

        # A small model: Nudge writes it down itself, and tells the model
        self.provider.tool_set = "essential"
        adapter = self.use_adapter(reply="Noted.")
        self.ask(self.user_a, "Remember: our discount never goes above 10% on any quotation")
        lesson = self.env["nudge.note"].sudo().search([("kind", "=", "lesson")], order="id desc", limit=1)
        self.assertEqual(lesson.body, "our discount never goes above 10% on any quotation")
        self.assertEqual(lesson.proposed_by, self.user_a)
        self.assertIn('"lesson"', adapter.calls[0]["turns"][-1]["context"])

        # Called anyway, with its own sentence: the person's words are kept
        conversation = self.env["nudge.conversation"].with_user(self.user_a)._chat_open(None)
        conversation._chat_prepare("Remember: invoices are sent on the last day of the month")
        result = tools.base.call(
            self.env(user=self.user_a, context=dict(self.env.context, nudge_conversation_id=conversation.id)),
            "remember_lesson",
            {"lesson": "Invoices are sent at the start of the month.", "why": "They said so"})
        self.assertIn("already written down", result.content, "Nudge wrote their words already: not twice")

    def test_for_everybody_and_told(self):
        for mode in ("talk", "agent"):
            names = [tool.name for tool in tools.base.available(self.env(user=self.user_a), mode)]
            self.assertIn("remember_lesson", names)
        self.assertIn("remember_lesson", prompt.SYSTEM_PERSONA)
