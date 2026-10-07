from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestFeedback(NudgeCase, TransactionCase):
    """Whether an answer helped, and why not."""

    def answer_of(self, user, text):
        self.use_adapter(reply="Here is the answer.")
        conversation, _last = self.ask(user, text)
        return conversation.sudo().message_ids.filtered(lambda message: message.role == "assistant")[-1]

    def test_the_person_rates_their_own_answer(self):
        answer = self.answer_of(self.user_a, "How do I confirm an order?")
        answer.with_user(self.user_a).rate("up")
        self.assertEqual(answer.feedback, "up")
        self.assertFalse(answer.feedback_note)
        with self.assertRaises(AccessError):
            answer.with_user(self.user_b).rate("up")
        with self.assertRaises(UserError):
            answer.with_user(self.user_a).rate("sideways")
        question = answer.conversation_id.message_ids.filtered(lambda message: message.role == "user")[0]
        with self.assertRaises(AccessError):
            question.with_user(self.user_a).rate("up")

    def test_a_no_with_a_why_becomes_a_lesson(self):
        answer = self.answer_of(self.user_a, "Where are the salaries?")
        Note = self.env["nudge.note"].sudo()
        before = Note.search_count([])
        answer.with_user(self.user_a).rate("down", "")
        self.assertEqual(answer.feedback, "down")
        self.assertEqual(Note.search_count([]), before, "No why, no lesson")
        answer.with_user(self.user_a).rate("down", "  The salaries are in our own payroll module, not in Odoo's.  ")
        self.assertEqual(answer.feedback_note, "The salaries are in our own payroll module, not in Odoo's.")
        lesson = Note.search([("kind", "=", "lesson")], order="id desc", limit=1)
        self.assertEqual(lesson.state, "proposed")
        self.assertEqual(lesson.body, "The salaries are in our own payroll module, not in Odoo's.")
        self.assertIn("Where are the salaries?", lesson.reason)
        self.assertEqual(lesson.proposed_by, self.user_a)
        self.assertEqual(lesson.conversation_id, answer.conversation_id)
        self.assertNotIn("payroll module", Note._get_text(), "Not before a manager says yes")

    def test_managers_see_the_feedback(self):
        answer = self.answer_of(self.user_a, "Anything")
        answer.with_user(self.user_a).rate("down", "Wrong app")
        rated = self.env["nudge.message"].with_user(self.manager).search([("feedback", "!=", False)])
        self.assertIn(answer, rated)
        self.assertEqual(rated.filtered(lambda message: message == answer).feedback_note, "Wrong app")
        self.assertFalse(self.env["nudge.message"].with_user(self.user_b).search([("feedback", "!=", False)]),
                         "Another person does not see it")
