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

    def mention(self, user, text="@Nudge summarise this", record=None, partners=None, **kwargs):
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
        self.assertEqual(call["turns"][-1]["text"], "summarise this")
        self.assertEqual(len(call["turns"]), 1, "Nothing else of any conversation")

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
