from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge.llm import base as llm
from odoo.addons.nudge.models.nudge_conversation import TOO_MANY_AT_ONCE

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

    def test_a_conversation_where_nudge_acted_is_refused_in_words_and_keeps_its_log(self):
        conversation, run, step = self._conversation_with_run(self.agent, state="done")
        with self.assertRaisesRegex(UserError, "Archive it instead"):
            conversation.with_user(self.manager).unlink()
        self.assertTrue(conversation.exists() and run.exists() and step.exists())

    def test_deleting_a_conversation_without_a_log_takes_its_files_and_keeps_the_ledger(self):
        conversation = self.env["nudge.conversation"].sudo().create({"user_id": self.agent.id, "name": "Raghad"})
        message = self.env["nudge.message"].sudo().create({
            "conversation_id": conversation.id, "role": "user", "content": "Ebrahim's picture"})
        picture = self.env["ir.attachment"].sudo().create({
            "name": "raghad.png", "raw": b"x", "res_model": "nudge.message", "res_id": message.id})
        message.attachment_id = picture
        usage = self.env["nudge.usage"].sudo().create({
            "user_id": self.agent.id, "conversation_id": conversation.id, "date": fields.Date.today()})
        conversation.with_user(self.manager).unlink()
        self.assertFalse(conversation.exists() or message.exists() or picture.exists())
        self.assertTrue(usage.exists())
        self.assertFalse(usage.conversation_id)

    def test_the_log_is_still_refused_outside_the_deletion_of_a_conversation(self):
        _conversation, run, step = self._conversation_with_run(self.agent)
        with self.assertRaises(AccessError):
            step.sudo().unlink()
        with self.assertRaises(AccessError):
            run.sudo().unlink()

    def test_the_retention_forgets_only_the_old_ones_that_are_idle(self):
        # The cron commits after each batch; a test is one transaction it undoes
        self.patch(type(self.env["ir.cron"]), "_commit_progress", lambda cron, *args, **kwargs: 10 ** 6)
        Conversation = self.env["nudge.conversation"].sudo().with_context(active_test=False)
        old = Conversation.create({"user_id": self.agent.id, "name": "Raghad old"})
        acted, _run, _step = self._conversation_with_run(self.agent, state="done")
        streaming = Conversation.create({"user_id": self.agent.id})
        streaming.stream_started_at = fields.Datetime.now()
        waiting, _run, _step = self._conversation_with_run(self.agent)
        recent = Conversation.create({"user_id": self.agent.id, "name": "Ebrahim recent"})
        old_ones = old | acted | streaming | waiting
        # what the ORM still holds would be written after, with a write_date of now
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE nudge_conversation SET write_date = %s WHERE id IN %s",
            (fields.Datetime.subtract(fields.Datetime.now(), days=100), tuple(old_ones.ids)))
        old_ones.invalidate_recordset()
        everyone = old_ones | recent

        self.env["ir.config_parameter"].sudo().set_param("nudge.retention_days", "0")
        self.assertEqual(Conversation._cron_apply_retention(), 0)
        self.assertEqual(everyone.exists(), everyone)

        self.env["ir.config_parameter"].sudo().set_param("nudge.retention_days", "30")
        # a batch large enough for whatever older conversations the database already holds
        Conversation._cron_apply_retention(batch=100000)
        self.assertFalse(old.exists(), "Old and idle: deleted")
        self.assertTrue(acted.exists(), "The log of the Agent is kept")
        self.assertFalse(acted.active, "and the conversation put away")
        self.assertEqual((streaming | waiting | recent).exists(), streaming | waiting | recent)
        self.assertTrue(all((streaming | waiting | recent).mapped("active")))

    def test_the_retention_goes_on_until_nothing_older_is_left(self):
        """Review (odoo-tech #4): a fixed batch a day never caught up with a database that makes more."""
        committed = []
        self.patch(type(self.env["ir.cron"]), "_commit_progress",
                   lambda cron, processed=0, **kwargs: committed.append(processed) or 10 ** 6)
        Conversation = self.env["nudge.conversation"].sudo().with_context(active_test=False)
        old = Conversation.create([{"user_id": self.agent.id, "name": f"Raghad old {index}"} for index in range(5)])
        acted = Conversation.browse()
        for _index in range(3):
            conversation, _run, _step = self._conversation_with_run(self.agent, state="done")
            acted |= conversation
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE nudge_conversation SET write_date = %s WHERE id IN %s",
            (fields.Datetime.subtract(fields.Datetime.now(), days=100), tuple((old | acted).ids)))
        self.env.invalidate_all()
        self.env["ir.config_parameter"].sudo().set_param("nudge.retention_days", "30")
        done = Conversation._cron_apply_retention(batch=2)
        self.assertGreaterEqual(done, 8)
        self.assertFalse(old.exists(), "Every batch of old ones, in one pass")
        self.assertFalse(any(acted.mapped("active")), "Every log put away")
        self.assertTrue(committed, "Each full batch is committed")

    def test_one_step_of_talk_runs_a_bounded_number_of_tools(self):
        """Review (ai #9): forty reads asked at once in Talk were all run and replayed in every round after."""
        calls = [llm.tool_use_event(f"call_{index}", "find_screen", {"words": "quotation"}) for index in range(12)]
        self.use_adapter(script=[calls + [llm.stop_event(llm.STOP_TOOL_USE)],
                                 [llm.text_event("Here they are."), llm.stop_event(llm.STOP_END)]])
        conversation, _event = self.ask(self.user_a, "Find the screens of quotations twelve times")
        results = conversation.sudo().message_ids.filtered(lambda message: message.role == "tool")[0].tool_results
        refused = [result for result in results if result["content"] == TOO_MANY_AT_ONCE]
        self.assertEqual((len(results), len(refused)), (12, 2))
        self.assertTrue(all(result["is_error"] for result in refused))

    def test_a_tool_error_tells_only_what_the_model_can_act_on(self):
        from odoo.addons.nudge.tools.base import guarded

        def refuse():
            raise UserError("Raghad may not do this")

        def mistake():
            raise ValueError("amount must be a number")

        def broken():
            self.env.cr.execute("SELECT * FROM table_of_ebrahim")

        refused = guarded(self.env, "t", refuse).content
        self.assertIn("Raghad may not do this", refused)
        self.assertIn("amount must be a number", guarded(self.env, "t", mistake).content)
        hidden = guarded(self.env, "t", broken).content
        self.assertIn("the action failed on the server", hidden)
        self.assertNotIn("table_of_ebrahim", hidden)
