from unittest import mock

from odoo import Command
from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge.llm import prompt
from odoo.addons.nudge.tests.common import NudgeCase
from odoo.addons.nudge_discuss.models.nudge_mention import to_html


@tagged("post_install", "-at_install")
class TestMentions(NudgeCase, TransactionCase):
    """@Nudge in the chatter and in Discuss: answered as an internal note, from
    what every reader of the record sees."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.nudge = cls.env.ref("nudge_discuss.partner_nudge")
        cls.acme = cls.env["res.partner"].create({
            "name": "Nudge Mention Acme", "city": "Nablus", "function": "Supplier of tiles",
        })
        cls.Mention = cls.env["nudge.mention"].with_context(nudge_keep_transaction=True)

    def mention(self, user, text="@Nudge what does this company sell?", record=None, partners=None, **kwargs):
        record = record or self.acme
        return record.with_user(user).message_post(
            body=text, message_type="comment", subtype_xmlid=kwargs.pop("subtype", "mail.mt_note"),
            partner_ids=(partners if partners is not None else self.nudge).ids, **kwargs)

    def answers(self, record=None):
        record = record or self.acme
        return self.env["mail.message"].search([
            ("model", "=", record._name), ("res_id", "=", record.id), ("author_id", "=", self.nudge.id)])

    def test_answered_as_an_internal_note(self):
        adapter = self.use_adapter(reply="Acme is a **supplier** of tiles in Nablus.")
        question = self.mention(self.user_contacts)
        mention = self.Mention.search([("message_id", "=", question.id)])
        self.assertEqual((mention.state, mention.user_id), ("queued", self.user_contacts))
        self.Mention._cron_answer()
        self.assertEqual(mention.state, "done")
        answer = self.answers()
        self.assertEqual(len(answer), 1)
        self.assertTrue(answer.subtype_id.internal, "A note: read by employees, never sent out")
        self.assertIn("<b>supplier</b>", answer.body)
        self.assertEqual(answer.parent_id, question)
        self.assertIn(self.user_contacts.partner_id, answer.partner_ids, "The one who asked is told")
        call = adapter.calls[0]
        self.assertEqual(call["system"], [prompt.FOCUS_PERSONA, prompt.FOCUS_BRIEFS["mention"]])
        self.assertEqual([tool["name"] for tool in call["tools"]], ["search_odoo_documentation"])
        sent = call["turns"][-1]["context"]
        self.assertIn("<nudge_record>", sent)
        self.assertIn("Supplier of tiles", sent)
        self.assertIn("Nudge Mention Acme", sent)
        self.assertEqual(call["turns"][-1]["text"], "what does this company sell?")
        self.assertEqual(len(call["turns"]), 1, "Nothing else of any conversation")

    def test_summarise_is_the_history_of_the_record_as_an_internal_note(self):
        from odoo.addons.nudge.tests.test_history import EchoAdapter
        adapter = self.use_adapter()
        adapter.__class__ = EchoAdapter
        child = self.env["res.partner"].create({"name": "Ebrahim", "parent_id": self.acme.id})
        self.acme.message_post(body="<p>Raghad wants 40 tiles</p>", message_type="comment",
                               subtype_xmlid="mail.mt_comment")
        question = self.mention(self.user_contacts, text="@Nudge summarize")
        self.Mention._cron_answer()
        mention = self.Mention.search([("message_id", "=", question.id)])
        self.assertEqual(mention.state, "done")
        answer = self.answers()
        self.assertEqual(len(answer), 1)
        self.assertTrue(answer.subtype_id.internal, "A note: read by employees, never sent out")
        self.assertEqual(adapter.calls[0]["system"], [prompt.FOCUS_PERSONA, prompt.FOCUS_BRIEFS["history"]])
        self.assertIn("40 tiles", adapter.calls[0]["turns"][-1]["context"])
        self.assertIn(f'<a href="/web#model=res.partner&amp;id={child.id}&amp;view_type=form">Show me</a>', answer.body)
        # The same words in Arabic, and in a longer sentence that is a question like any other
        self.mention(self.user_contacts, text="@Nudge لخص لي")
        self.Mention._cron_answer()
        self.assertEqual(len(adapter.calls), 1, "Kept for 15 minutes: the chatter did not change")
        self.mention(self.user_contacts, text="@Nudge summarize the quality of this supplier")
        self.Mention._cron_answer()
        self.assertEqual(adapter.calls[-1]["system"], [prompt.FOCUS_PERSONA, prompt.FOCUS_BRIEFS["mention"]])

    def test_draft_a_reply_is_posted_as_a_note_and_never_sent(self):
        adapter = self.use_adapter(reply="Dear Raghad,\n\nOur tiles come in 5 days.\n\nBest regards")
        customer = self.env["res.partner"].create({"name": "Raghad", "email": "raghad@customer.example"})
        self.acme.message_post(
            body="<p>When do my tiles come?</p>", subject="Tiles", message_type="email",
            subtype_xmlid="mail.mt_comment", author_id=customer.id)
        self.mention(self.user_contacts, text="@Nudge draft a formal reply")
        self.Mention._cron_answer()
        self.assertFalse(self.env["mail.mail"].search([("recipient_ids", "in", customer.id)]), "The customer gets nothing")
        self.assertFalse(self.env["mail.mail"].search([("email_to", "ilike", "customer.example")]))
        answer = self.answers()
        self.assertEqual(len(answer), 1)
        self.assertTrue(answer.subtype_id.internal)
        self.assertIn("Our tiles come in 5 days.", answer.body)
        self.assertIn("raghad@customer.example", answer.body)
        self.assertIn("Re: Tiles", answer.body)
        self.assertEqual(adapter.calls[0]["system"], [prompt.FOCUS_PERSONA, prompt.FOCUS_BRIEFS["reply"]])
        self.assertIn("Tone: formal", adapter.calls[0]["turns"][-1]["text"])
        # A note of Nudge is internal: the next draft does not quote it
        self.mention(self.user_contacts, text="@Nudge draft a reply")
        self.Mention._cron_answer()
        self.assertNotIn("Our tiles come in 5 days.", adapter.calls[-1]["turns"][-1]["context"])

    def test_nothing_to_answer_is_said_in_the_chatter(self):
        self.use_adapter()
        self.mention(self.user_contacts, text="@Nudge draft a reply")
        self.Mention._cron_answer()
        self.assertIn("could not answer", self.answers().body)

    def test_how_a_request_in_a_few_words_is_told_from_a_question(self):
        from odoo.addons.nudge_discuss.models.nudge_mention import asked_for
        self.assertEqual(asked_for("@Nudge Summarise"), ("history", None))
        self.assertEqual(asked_for("@Nudge please summarize this."), ("history", None))
        self.assertEqual(asked_for("@Nudge لخص"), ("history", None))
        self.assertEqual(asked_for("@Nudge draft a reply"), ("reply", "friendly"))
        self.assertEqual(asked_for("@Nudge draft a short reply to this"), ("reply", "short"))
        self.assertEqual(asked_for("@Nudge write me a formal response"), ("reply", "formal"))
        self.assertEqual(asked_for("@Nudge reply"), ("reply", "friendly"))
        self.assertEqual(asked_for("@Nudge اكتب لي رد رسمي"), ("reply", "formal"))
        for question in ("@Nudge summarise the contract terms of Acme", "@Nudge who should reply to this?",
                         "@Nudge draft a reply that offers a 10 percent discount", "@Nudge", "@Nudge hello"):
            self.assertEqual(asked_for(question), (None, None), question)

    def test_what_some_readers_cannot_see_is_not_sent(self):
        adapter = self.use_adapter()
        kept = [field for name, field in self.acme._fields.items()
                if field.groups and field.type in ("char", "text") and self.acme.sudo()[name]]
        self.mention(self.user_contacts)
        self.Mention._cron_answer()
        sent = adapter.calls[0]["turns"][-1]["context"]
        for field in kept:
            self.assertNotIn(field.string, sent)

    def test_not_for_those_without_nudge(self):
        adapter = self.use_adapter()
        # Someone without Nudge names it all the same
        self.acme.sudo().message_post(
            body="@Nudge hello", message_type="comment", subtype_xmlid="mail.mt_note",
            author_id=self.outsider.partner_id.id, partner_ids=self.nudge.ids)
        self.Mention._cron_answer()
        self.assertEqual(adapter.calls, [])
        self.assertIn("not open", self.answers().body)
        # Said in the language of the one who asked, not in that of the background job
        refused = self.Mention.search([("state", "=", "refused")], order="id desc", limit=1)
        self.env["res.lang"]._activate_lang("fr_FR")
        self.outsider.sudo().lang = "fr_FR"
        self.assertEqual(refused._lang(), "fr_FR")

    def test_only_when_named(self):
        self.use_adapter()
        self.mention(self.user_contacts, text="Nudge, are you there?", partners=self.env["res.partner"])
        self.assertFalse(self.Mention.search([("res_id", "=", self.acme.id)]))

    def test_typed_without_choosing_it(self):
        self.use_adapter()
        self.mention(self.user_contacts, text="Write to sales@nudge.example", partners=self.env["res.partner"])
        self.assertFalse(self.Mention.search([("res_id", "=", self.acme.id)]), "An address is not a mention")
        self.mention(self.user_contacts, text="@nudge who is the buyer?", partners=self.env["res.partner"])
        self.assertEqual(len(self.Mention.search([("res_id", "=", self.acme.id)])), 1)

    def test_a_failure_is_said(self):
        self.use_adapter()
        self.provider.active = False
        self.env["nudge.provider"].search([]).active = False
        self.mention(self.user_contacts)
        self.Mention._cron_answer()
        mention = self.Mention.search([("res_id", "=", self.acme.id)])
        self.assertEqual(mention.state, "failed")
        self.assertIn("could not answer", self.answers().body)

    def test_in_a_channel(self):
        self.use_adapter(reply="Hello, team.")
        channel = self.env["discuss.channel"].create({
            "name": "Nudge Mentions",
            "channel_type": "channel",
            "channel_member_ids": [Command.create({"partner_id": self.user_contacts.partner_id.id})],
        })
        self.mention(self.user_contacts, record=channel, text="@Nudge hello", subtype="mail.mt_comment")
        self.Mention._cron_answer()
        answer = self.answers(channel)
        self.assertEqual(len(answer), 1)
        self.assertIn("Hello, team.", answer.body)

    def test_markdown_becomes_a_message(self):
        html = to_html("Two things:\n- **first** `code`\n- [docs](https://www.odoo.com)\n\n<script>x</script>")
        self.assertIn("<ul><li><b>first</b> <code>code</code></li>", html)
        self.assertIn('<a href="https://www.odoo.com" target="_blank">docs</a>', html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_italic_becomes_a_message(self):
        # *Note* kept its asterisks in the channel
        html = to_html("A *note* and _another_, but file_name_here and 2*3*4 stay")
        self.assertIn("A <i>note</i> and <i>another</i>, but file_name_here and 2*3*4 stay", html)
        self.assertIn("<b>bold</b>", to_html("**bold** first"))

    def test_nudge_is_offered_after_an_at_in_a_channel(self):
        # "@" in #general did not offer Nudge: a channel suggests its members
        channel = self.env["discuss.channel"].create({"name": "Nudge Offered", "channel_type": "channel"})
        self.assertIn(self.nudge, channel.channel_member_ids.partner_id)
        self.assertIn(self.nudge, self.env["res.partner"].search([("channel_ids", "in", channel.ids)]))
        chat = self.env["discuss.channel"].create({"name": "Nudge Chat", "channel_type": "group"})
        self.assertNotIn(self.nudge, chat.channel_member_ids.partner_id, "Never added to a private talk")
        # The channels made before it: on install and on every update
        self.env["discuss.channel.member"].search([("partner_id", "=", self.nudge.id)]).unlink()
        self.env["discuss.channel"]._nudge_join_all()
        self.assertIn(self.nudge, channel.channel_member_ids.partner_id)

    def test_steps_keep_their_numbers(self):
        # The answer in #general read "1." for every step: each had lines under it
        html = to_html("Steps:\n\n1. **Enable**:  \n   Go to: Settings\n\n2. **Add**:\n   Go to: Bills\n\n3. **Verify**")
        self.assertEqual(html.count("<ol"), 1)
        self.assertIn("<ol><li><b>Enable</b>:<br>Go to: Settings</li><li><b>Add</b>:<br>Go to: Bills</li>"
                      "<li><b>Verify</b></li></ol>", html)
        # A paragraph that is not indented ends the list: the next one starts where it was
        html = to_html("1. one\nA note between.\n2. two\n3. three")
        self.assertIn('<p>A note between.</p><ol start="2"><li>two</li><li>three</li></ol>', html)
        # And the number is kept in the message posted
        message = self.acme.message_post(body=html, message_type="comment", subtype_xmlid="mail.mt_note")
        self.assertIn('<ol start="2">', message.body)

    def test_an_answer_cut_short_goes_back_to_the_queue_and_does_not_block_it(self):
        self.use_adapter(reply="Acme sells tiles.")
        self.mention(self.user_contacts)
        mention = self.Mention.search([("res_id", "=", self.acme.id)])
        # A worker killed in the middle of the answer left it "answering"
        mention.state = "answering"
        self.Mention._cron_answer()
        self.assertEqual(mention.state, "answering", "Recent: another worker may be on it")
        self.env.cr.execute(
            "UPDATE nudge_mention SET write_date = now() at time zone 'utc' - interval '1 hour' WHERE id = %s",
            (mention.id,))
        mention.invalidate_recordset()
        self.Mention._cron_answer()
        self.assertEqual(mention.state, "done")

    def test_a_pass_that_runs_long_leaves_the_rest_for_the_next(self):
        from odoo.addons.nudge_discuss.models import nudge_mention  # noqa: PLC0415
        self.use_adapter(reply="Acme sells tiles.")
        self.mention(self.user_contacts)
        self.mention(self.user_contacts, text="@Nudge and what else?")
        mentions = self.Mention.search([("res_id", "=", self.acme.id)], order="id")
        with mock.patch.object(nudge_mention, "CRON_SECONDS", -1), \
                mock.patch.object(type(self.env["ir.cron"]), "_trigger") as trigger:
            self.Mention._cron_answer()
            self.assertEqual(mentions.mapped("state"), ["done", "queued"])
            trigger.assert_called()
        self.Mention._cron_answer()
        self.assertEqual(mentions.mapped("state"), ["done", "done"])
