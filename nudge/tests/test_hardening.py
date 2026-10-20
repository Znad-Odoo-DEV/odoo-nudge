from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from .common import NudgeCase
from .test_snapshot import SNAPSHOT

SCREEN_FIELDS = (
    "context_text", "snapshot", "tool_calls", "tool_results", "content_blocks", "pending_results",
)


@tagged("post_install", "-at_install")
class TestHardening(NudgeCase, TransactionCase):
    """What the security review settled: who reads what of a conversation."""

    def test_what_a_message_holds_of_a_screen_is_for_the_system_only(self):
        self.use_adapter(reply="This is a contact.")
        conversation, _event = self.ask(self.user_contacts, "What is this?", snapshot=SNAPSHOT)
        question = conversation.sudo().message_ids.filtered(lambda message: message.role == "user")[0]

        # Odoo itself keeps the screen as it was sent to the model, to answer
        self.assertIn("<screen_snapshot>", question.context_text)
        self.assertTrue(question.snapshot)

        # The person reads what they wrote and the answer: the screen was theirs anyway
        own = question.with_user(self.user_contacts)
        self.assertEqual(own.content, "What is this?")
        for name in SCREEN_FIELDS:
            with self.assertRaises(AccessError, msg=name):
                own.read([name])

        # A manager of Nudge reads every conversation, never the screens of other people
        theirs = question.with_user(self.manager)
        self.assertEqual(theirs.content, "What is this?")
        for name in SCREEN_FIELDS:
            with self.assertRaises(AccessError, msg=name):
                theirs.read([name])

    def test_the_key_of_a_provider_is_for_the_system_only(self):
        provider = self.provider.with_user(self.manager)
        self.assertEqual(provider.name, "Test provider", "Managers see the providers")
        with self.assertRaises(AccessError):
            provider.read(["api_key"])
        with self.assertRaises(AccessError):
            provider.action_fetch_models()
        with self.assertRaises(AccessError):
            provider.write({"base_url": "http://127.0.0.1:8069"})

    def _conversation_with_run(self, user, state="pending"):
        conversation = self.env["nudge.conversation"].sudo().create({"user_id": user.id, "name": "Raghad"})
        run = self.env["nudge.run"].sudo().create({
            "name": "Ebrahim asked", "conversation_id": conversation.id, "user_id": user.id})
        step = self.env["nudge.step"].sudo().create({
            "run_id": run.id, "tool": "search_records", "kind": "read", "state": state})
        return conversation, run, step

    def test_deleting_a_conversation_deletes_its_runs_and_steps(self):
        conversation, run, step = self._conversation_with_run(self.agent)
        conversation.waiting_step_id = step
        usage = self.env["nudge.usage"].sudo().create({
            "user_id": self.agent.id, "conversation_id": conversation.id})
        conversation.sudo().unlink()
        self.assertFalse(conversation.exists())
        self.assertFalse(run.exists())
        self.assertFalse(step.exists())
        self.assertTrue(usage.exists())
        self.assertFalse(usage.conversation_id)

    def test_the_log_is_still_refused_outside_the_deletion_of_a_conversation(self):
        _conversation, run, step = self._conversation_with_run(self.agent)
        with self.assertRaises(AccessError):
            step.sudo().unlink()
        with self.assertRaises(AccessError):
            run.sudo().unlink()

    def test_the_retention_deletes_only_the_old_ones_that_are_idle(self):
        Conversation = self.env["nudge.conversation"].sudo()
        old, _run, _step = self._conversation_with_run(self.agent, state="done")
        streaming = Conversation.create({"user_id": self.agent.id})
        streaming.stream_started_at = fields.Datetime.now()
        waiting, _run, _step = self._conversation_with_run(self.agent)
        recent = Conversation.create({"user_id": self.agent.id, "name": "Raghad recent"})
        old_ones = old | streaming | waiting
        self.env.cr.execute(
            "UPDATE nudge_conversation SET write_date = %s WHERE id IN %s",
            (fields.Datetime.subtract(fields.Datetime.now(), days=100), tuple(old_ones.ids)))
        old_ones.invalidate_recordset()

        self.env["ir.config_parameter"].sudo().set_param("nudge.retention_days", "0")
        Conversation._cron_apply_retention()
        self.assertEqual((old | streaming | waiting | recent).exists(), old | streaming | waiting | recent)

        self.env["ir.config_parameter"].sudo().set_param("nudge.retention_days", "30")
        Conversation._cron_apply_retention()
        self.assertFalse(old.exists())
        self.assertEqual((streaming | waiting | recent).exists(), streaming | waiting | recent)

    def test_a_tool_error_tells_only_what_the_model_can_act_on(self):
        from odoo.addons.nudge.tools.base import guarded

        def refuse():
            raise UserError("Raghad may not do this")

        def mistake():
            raise ValueError("amount must be a number")

        def broken():
            self.env.cr.execute("SELECT * FROM table_of_ebrahim")

        self.assertIn("Raghad may not do this", refused)
        self.assertIn("amount must be a number", guarded(self.env, "t", mistake).content)
        hidden = guarded(self.env, "t", broken).content
        self.assertIn("ProgrammingError", hidden)
        self.assertNotIn("table_of_ebrahim", hidden)
