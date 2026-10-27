from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge.tests.common import NudgeCase


@tagged("post_install", "-at_install")
class TestAgentDiscuss(NudgeCase, TransactionCase):
    """A published agent in Discuss: answered when mentioned in a bound channel or written to in a direct
    chat, as the one who writes, posted as the agent's partner."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Mention = cls.env["nudge.mention"].with_context(nudge_keep_transaction=True)
        cls.agent = cls.env["nudge.agent"].create({
            "name": "Tile Expert", "role": "Answers about tiles",
            "user_ids": [Command.set(cls.user_a.ids)],
        })
        cls.partner = cls.agent.partner_id
        cls.channel = cls.env["discuss.channel"].create({
            "name": "Agent Tiles", "channel_type": "channel",
            "channel_member_ids": [Command.create({"partner_id": cls.user_a.partner_id.id}),
                                   Command.create({"partner_id": cls.user_b.partner_id.id})],
        })
        cls.binding = cls.env["nudge.agent.binding"].create({
            "agent_id": cls.agent.id, "channel": "discuss", "acts_as": "person",
            "channel_ids": [Command.set(cls.channel.ids)],
        })
        cls.agent.sudo().write({"state": "published"})
        cls.binding.write({"published": True})

    def members(self, channel=None):
        return (channel or self.channel).sudo().channel_member_ids.partner_id

    def ask(self, user, text="hello", channel=None, mention=True):
        channel = channel or self.channel
        return channel.with_user(user).message_post(
            body=text, message_type="comment", subtype_xmlid="mail.mt_comment",
            partner_ids=self.partner.ids if mention else [])

    def chat(self, name, partner):
        """A direct chat of user_a and ``partner``, made by user_a: Odoo puts whoever makes a channel in it, and a
        chat made in sudo would have OdooBot as a third member"""
        return self.env["discuss.channel"].with_user(self.user_a).sudo().create({
            "name": name, "channel_type": "chat",
            "channel_member_ids": [Command.create({"partner_id": self.user_a.partner_id.id}),
                                   Command.create({"partner_id": partner.id})]})

    def posts(self, channel=None):
        return self.env["mail.message"].search([
            ("model", "=", "discuss.channel"), ("res_id", "=", (channel or self.channel).id),
            ("author_id", "=", self.partner.id)])

    def test_publishing_adds_the_partner_and_unpublishing_removes_it(self):
        self.assertIn(self.partner, self.members())
        self.binding.write({"published": False})
        self.assertNotIn(self.partner, self.members())
        self.binding.write({"published": True})
        self.assertIn(self.partner, self.members())
        self.agent.sudo().write({"active": False})
        self.assertNotIn(self.partner, self.members())

    def test_a_channel_taken_off_the_binding_loses_the_partner(self):
        other = self.env["discuss.channel"].create({"name": "Agent Other", "channel_type": "channel"})
        self.binding.write({"channel_ids": [Command.link(other.id)]})
        self.assertIn(self.partner, self.members(other))
        self.binding.write({"channel_ids": [Command.unlink(other.id)]})
        self.assertNotIn(self.partner, self.members(other))
        self.assertIn(self.partner, self.members())

    def test_a_mention_is_answered_by_the_agent_partner(self):
        adapter = self.use_adapter(reply="Tiles come in **5 days**.")
        question = self.ask(self.user_a, "@Tile Expert when do tiles come?")
        mention = self.Mention.search([("message_id", "=", question.id)])
        self.assertEqual((mention.agent_id, mention.user_id), (self.agent, self.user_a))
        self.Mention._cron_answer()
        self.assertEqual(mention.state, "done")
        answer = self.posts()
        self.assertEqual(len(answer), 1)
        self.assertIn("<b>5 days</b>", answer.body)
        self.assertEqual(len(adapter.calls), 1)
        # One conversation per (channel, author): the second question goes on in it
        first = mention.conversation_id
        self.assertTrue(first)
        self.assertEqual(first.agent_id, self.agent)
        self.ask(self.user_a, "@Tile Expert and the grout?")
        self.Mention._cron_answer()
        self.assertEqual(self.Mention.search([("state", "=", "done")], order="id desc", limit=1).conversation_id, first)
        self.assertEqual(len(self.posts()), 2)

    def test_no_mention_no_answer(self):
        self.use_adapter()
        self.ask(self.user_a, "just talking", mention=False)
        self.assertFalse(self.Mention.search([("agent_id", "=", self.agent.id)]))

    def test_a_direct_chat_is_answered_without_a_mention(self):
        self.use_adapter(reply="Yes, we do.")
        chat = self.chat("Chat Tile Expert", self.partner)
        self.ask(self.user_a, "do you sell tiles?", channel=chat, mention=False)
        self.Mention._cron_answer()
        self.assertIn("Yes, we do.", self.posts(chat).body)

    def test_a_user_outside_the_audience_is_refused_once(self):
        adapter = self.use_adapter()
        self.ask(self.user_b, "@Tile Expert hello")
        self.Mention._cron_answer()
        answer = self.posts()
        self.assertEqual(len(answer), 1)
        self.assertIn("This agent is not open to you.", answer.body)
        self.ask(self.user_b, "@Tile Expert hello again")
        self.Mention._cron_answer()
        self.assertEqual(len(self.posts()), 1, "Told once")
        self.assertEqual(adapter.calls, [])

    def test_the_agent_never_answers_itself_or_another_bot(self):
        self.use_adapter()
        before = self.Mention.search_count([])
        # Its own message, mentioning itself
        self.channel.sudo().message_post(
            body="@Tile Expert", author_id=self.partner.id, message_type="comment",
            subtype_xmlid="mail.mt_comment", partner_ids=self.partner.ids)
        # Another agent writes to it
        other = self.env["nudge.agent"].create({"name": "Other Bot", "user_ids": [Command.set(self.user_a.ids)]})
        self.channel.sudo().message_post(
            body="@Tile Expert", author_id=other.partner_id.id, message_type="comment",
            subtype_xmlid="mail.mt_comment", partner_ids=self.partner.ids)
        # OdooBot and the Nudge bot
        for bot in (self.env.ref("base.partner_root"), self.env.ref("nudge_discuss.partner_nudge")):
            self.channel.sudo().message_post(
                body="@Tile Expert", author_id=bot.id, message_type="comment",
                subtype_xmlid="mail.mt_comment", partner_ids=self.partner.ids)
        self.assertEqual(self.Mention.search_count([]), before)

    # ------------------------------------------------------------------
    # Security findings
    # ------------------------------------------------------------------
    def fake_answer(self, text="ok", error=None):
        """Replaces nudge.agent._answer; :return: the list of (knowledge_only, question) calls"""
        calls = []

        def _answer(agent, question, conversation=None, **kw):
            calls.append((bool(agent.env.context.get("nudge_agent_knowledge_only")), question))
            if error:
                raise error
            # What the engine of an earlier draft could return: never acted on any more
            return {"text": text, "conversation_id": False, "needs_records": True}

        self.patch(type(self.env["nudge.agent"]), "_answer", _answer)
        return calls

    def test_sync_never_touches_a_partner_that_has_a_user(self):
        # The agent's partner pointed at a person's: nothing is added to or removed from them
        self.channel.sudo().channel_member_ids.filtered(
            lambda m: m.partner_id == self.user_b.partner_id).unlink()
        self.agent.sudo().write({"partner_id": self.user_b.partner_id.id})
        self.assertNotIn(self.user_b.partner_id, self.members())
        self.agent.sudo().write({"partner_id": self.user_a.partner_id.id})
        self.binding.write({"published": False})
        self.assertIn(self.user_a.partner_id, self.members(), "Not removed from the person either")

    def test_a_binding_lists_channels_and_groups_only(self):
        chat = self.chat("Chat X", self.user_b.partner_id)
        with self.assertRaises(ValidationError):
            self.binding.write({"channel_ids": [Command.link(chat.id)]})

    def test_a_writer_outside_the_channel_cannot_list_it(self):
        group = self.env["discuss.channel"].sudo().create({
            "name": "Private Group", "channel_type": "group",
            "channel_member_ids": [Command.create({"partner_id": self.user_a.partner_id.id})]})
        self.binding.sudo().write({"channel_ids": [Command.link(group.id)]})
        with self.assertRaises(ValidationError):
            self.binding.with_user(self.user_b)._check_discuss_channels()
        self.binding.with_user(self.user_a)._check_discuss_channels()

    def test_every_channel_is_answered_from_knowledge_only(self):
        # 2.1: what is posted in a channel is read by its members, and by guests, who have no user
        calls = self.fake_answer("Tiles come in 5 days.")
        # Everyone in the channel is in the audience except user_b: knowledge only
        self.ask(self.user_a, "@Tile Expert price of tile T-1?")
        self.Mention._cron_answer()
        self.assertEqual([c[0] for c in calls], [True])
        # Only the audience in the channel: knowledge only still (a guest may join, audience is not rights)
        self.channel.sudo().channel_member_ids.filtered(
            lambda m: m.partner_id == self.user_b.partner_id).unlink()
        self.ask(self.user_a, "@Tile Expert price of tile T-2?")
        self.Mention._cron_answer()
        self.assertEqual([c[0] for c in calls], [True, True])
        # A direct chat of the author and the agent alone: the author's records
        chat = self.chat("Chat Tile Expert", self.partner)
        self.ask(self.user_a, "my open orders?", channel=chat, mention=False)
        self.Mention._cron_answer()
        self.assertEqual([c[0] for c in calls], [True, True, False])

    def test_a_channel_answer_is_never_sent_privately(self):
        # 2.1: the answer is posted where the question was, from knowledge only; nothing else is asked
        calls = self.fake_answer("Secret total is 42.")
        self.ask(self.user_a, "@Tile Expert my open orders?")
        self.Mention._cron_answer()
        self.assertEqual([c[0] for c in calls], [True])
        self.assertEqual(len(self.posts()), 1)
        self.assertFalse(self.env["discuss.channel"].sudo().search_count([
            ("channel_type", "=", "chat"), ("channel_member_ids.partner_id", "=", self.partner.id)]),
            "No direct chat is made")

    def test_a_conversation_read_by_others_is_stored_as_such(self):
        # 2.1: the real engine, in a channel: the conversation is knowledge only, and so is the next turn
        self.use_adapter(reply="Tiles come in 5 days.")
        self.channel.sudo().channel_member_ids.filtered(
            lambda m: m.partner_id == self.user_b.partner_id).unlink()
        question = self.ask(self.user_a, "@Tile Expert when do tiles come?")
        self.Mention._cron_answer()
        mention = self.Mention.search([("message_id", "=", question.id)])
        self.assertEqual(mention.state, "done")
        self.assertTrue(mention.conversation_id.sudo().agent_knowledge_only)

    def test_an_author_named_by_someone_else_is_not_answered(self):
        # 2.9: an author given in sudo (as a mail gateway would) is not the one who posts
        self.fake_answer("Hello.")
        before = self.Mention.search_count([("agent_id", "=", self.agent.id)])
        self.channel.with_user(self.user_b).sudo().message_post(
            body="@Tile Expert hello", author_id=self.user_a.partner_id.id, message_type="comment",
            subtype_xmlid="mail.mt_comment", partner_ids=self.partner.ids)
        self.channel.sudo().message_post(
            body="@Tile Expert hello", author_id=self.user_a.partner_id.id, message_type="comment",
            subtype_xmlid="mail.mt_comment", partner_ids=self.partner.ids)
        self.assertEqual(self.Mention.search_count([("agent_id", "=", self.agent.id)]), before)
        # Posted by the person, as their own partner: answered
        self.ask(self.user_a, "@Tile Expert hello")
        self.assertEqual(self.Mention.search_count([("agent_id", "=", self.agent.id)]), before + 1)

    def test_a_failed_answer_is_generic_and_never_retried(self):
        calls = self.fake_answer(error=AccessError("Record sale.order(42) of Acme"))
        self.ask(self.user_a, "@Tile Expert hello")
        mention = self.Mention.search([("agent_id", "=", self.agent.id)])
        self.Mention._cron_answer()
        self.assertEqual(mention.state, "failed")
        answer = self.posts()
        self.assertIn("This agent could not answer.", answer.body)
        self.assertNotIn("Acme", answer.body)
        self.assertNotIn("Acme", mention.error or "")
        self.Mention._cron_answer()
        self.assertEqual(len(calls), 1)
        # Any other exception ends the same way
        self.fake_answer(error=RuntimeError("boom 42"))
        self.ask(self.user_a, "@Tile Expert again")
        self.Mention._cron_answer()
        self.assertEqual(self.Mention.search([("agent_id", "=", self.agent.id)], limit=1).state, "failed")
        self.assertNotIn("boom", self.posts().mapped("body")[0])

    def test_an_agent_saying_nudge_does_not_wake_the_nudge_bot(self):
        before = self.Mention.search_count([])
        self.channel.sudo().message_post(
            body="ask @Nudge about it", author_id=self.partner.id, message_type="comment",
            subtype_xmlid="mail.mt_comment")
        self.assertEqual(self.Mention.search_count([]), before)

    def test_a_burst_of_refusals_is_one_refusal(self):
        self.use_adapter()
        for text in ("one", "two", "three"):
            self.ask(self.user_b, "@Tile Expert " + text)
        self.assertEqual(self.Mention.search_count([("agent_id", "=", self.agent.id), ("user_id", "=", self.user_b.id)]), 1)
        self.Mention._cron_answer()
        self.assertEqual(len(self.posts()), 1)
