from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestSecurity(NudgeCase, TransactionCase):

    def test_groups_are_nested(self):
        self.assertTrue(self.manager.has_group("nudge.group_nudge_agent"))
        self.assertTrue(self.manager.has_group("nudge.group_nudge_user"))
        self.assertFalse(self.user_a.has_group("nudge.group_nudge_agent"))
        self.assertFalse(self.outsider.has_group("nudge.group_nudge_user"))

    def test_conversations_are_private(self):
        self.use_adapter()
        conversation, _result = self.ask(self.user_a, "Hello")
        Conversation = self.env["nudge.conversation"]
        Message = self.env["nudge.message"]

        # No domain: what a user finds is decided by the rules alone
        self.assertEqual(Conversation.with_user(self.user_a).search([]), conversation)
        self.assertFalse(Conversation.with_user(self.user_b).search([]))
        self.assertFalse(Message.with_user(self.user_b).search([]))
        with self.assertRaises(AccessError):
            conversation.with_user(self.user_b).read(["name"])
        with self.assertRaises(AccessError):
            conversation.message_ids.with_user(self.user_b).read(["content"])

        # The database may hold conversations of other people
        self.assertIn(conversation, Conversation.with_user(self.manager).search([]))
        self.assertEqual(
            Message.with_user(self.manager).search([("conversation_id", "=", conversation.id)]),
            conversation.message_ids,
        )

    def test_outsider_has_no_access(self):
        with self.assertRaises(AccessError):
            self.env["nudge.conversation"].with_user(self.outsider).search([])
        with self.assertRaises(AccessError):
            self.env["nudge.conversation"].with_user(self.outsider).create({})

    def test_nobody_posts_in_the_conversation_of_someone_else(self):
        self.use_adapter()
        conversation, _result = self.ask(self.user_a, "Hello")
        for user in (self.user_b, self.manager):
            with self.assertRaises(AccessError):
                self.env["nudge.conversation"].with_user(user)._chat_open(conversation.id)

    def test_messages_are_written_by_the_server_only(self):
        self.use_adapter()
        conversation, _result = self.ask(self.user_a, "Hello")
        message = conversation.message_ids[0].with_user(self.user_a)
        with self.assertRaises(AccessError):
            message.write({"content": "rewritten", "input_tokens": 0})
        with self.assertRaises(AccessError):
            message.unlink()
        with self.assertRaises(AccessError):
            self.env["nudge.message"].with_user(self.user_a).create({
                "conversation_id": conversation.id, "role": "assistant", "content": "forged",
            })

    def test_user_changes_the_title_only(self):
        self.use_adapter()
        conversation, _result = self.ask(self.user_a, "Hello")
        conversation = conversation.with_user(self.user_a)
        conversation.write({"name": "Renamed", "active": False})
        self.assertEqual(conversation.name, "Renamed")
        with self.assertRaises(AccessError):
            conversation.write({"user_id": self.user_b.id})
        with self.assertRaises(AccessError):
            conversation.write({"provider_id": False})
        with self.assertRaises(AccessError):
            conversation.unlink()

    def test_user_cannot_create_for_someone_else(self):
        conversation = self.env["nudge.conversation"].with_user(self.user_a).create({
            "name": "Mine", "user_id": self.user_b.id, "model_name": "forged",
        })
        self.assertEqual(conversation.user_id, self.user_a)
        self.assertFalse(conversation.model_name)

    def test_api_key_is_for_administrators(self):
        provider = self.provider.with_user(self.manager)
        self.assertTrue(provider.has_api_key)
        self.assertTrue(provider.is_configured)
        with self.assertRaises(AccessError):
            provider.read(["api_key"])
        self.assertNotIn("api_key", provider.fields_get())
        with self.assertRaises(AccessError):
            provider.write({"model_name": "other"})
        with self.assertRaises(AccessError):
            self.provider.with_user(self.user_a).read(["name"])

    def test_api_key_is_not_in_the_representation(self):
        self.assertNotIn("sk-test-secret", repr(self.provider._get_config()))
