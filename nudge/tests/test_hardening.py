from odoo.exceptions import AccessError
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
