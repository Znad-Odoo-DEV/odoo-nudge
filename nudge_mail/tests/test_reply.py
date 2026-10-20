import base64
import re

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.mail.tests.common import MockEmail
from odoo.addons.nudge.llm import prompt
from odoo.addons.nudge.llm.fake_adapter import FakeAdapter
from odoo.addons.nudge.tests.common import NudgeCase

POLICY = b"""Delivery policy
Every delivery is made within 5 working days of the confirmed order, free of charge in Dubai.
"""


class ToneAdapter(FakeAdapter):
    """A provider that is a plain function of what it is sent: the reply names the tone asked."""

    def stream_chat(self, system, turns, deadline=None, tools=None, tool_choice=None):
        tone = re.search(r"Tone: (\w+)", turns[-1]["text"])
        self.reply = "Dear Raghad,\n\nWe deliver within 5 working days (%s).\n\nBest regards" % (tone and tone.group(1))
        return super().stream_chat(system, turns, deadline=deadline, tools=tools, tool_choice=tool_choice)


@tagged("post_install", "-at_install")
class TestReply(NudgeCase, MockEmail, TransactionCase):
    """A reply to a customer drafted from the thread: read in the window of the emails, sent by nobody but the person."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clerk = new_test_user(
            cls.env, login="nudge_reply_clerk", name="Ebrahim", email="ebrahim@acme.example",
            groups="nudge.group_nudge_user,base.group_partner_manager")
        cls.clerk.signature = "<p>Ebrahim, Sales</p>"
        cls.customer = cls.env["res.partner"].create({"name": "Raghad", "email": "raghad@customer.example"})
        cls.record = cls.env["res.partner"].create({"name": "Raghad Trading", "email": "info@trading.example"})
        cls.incoming = cls.record.message_post(
            body="<p>Hello, how long does a delivery take?</p>", subject="Delivery question",
            message_type="email", subtype_xmlid="mail.mt_comment", author_id=cls.customer.id,
            email_from='"Raghad" <raghad@customer.example>')
        cls.record.message_post(
            body="<p>Secret margin on tiles is 30 percent</p>", message_type="comment", subtype_xmlid="mail.mt_note")
        cls.env["nudge.source"].with_user(cls.manager).create({
            "name": "Delivery Policy.txt", "file": base64.b64encode(POLICY), "file_name": "Delivery Policy.txt"})

    def window(self, user=None, tone="friendly", record=None, refresh=False):
        record = record or self.record
        action = self.env["nudge.email.review"].with_user(user or self.clerk).action_draft_reply(
            record._name, record.id, tone, refresh)
        self.assertEqual(action["res_model"], "nudge.email.review")
        self.assertEqual(action["target"], "new")
        return self.env["nudge.email.review"].with_user(user or self.clerk).browse(action["res_id"])

    def context(self, adapter):
        return adapter.calls[-1]["turns"][-1]["context"]

    def test_the_draft_opens_in_the_review_window_and_nothing_is_sent(self):
        adapter = self.use_adapter()
        adapter.__class__ = ToneAdapter
        mails = self.env["mail.mail"].search_count([])
        with self.mock_mail_gateway():
            window = self.window()
        self.assertEqual(self._mails, [], "Drafting sends nothing")
        self.assertEqual(self.env["mail.mail"].search_count([]), mails)
        self.assertIn("raghad@customer.example", window.email_to)
        self.assertEqual(window.subject, "Re: Delivery question")
        self.assertFalse(window.flow_id, "No button behind it, no template")
        self.assertIn("Dear Raghad", window.body)
        self.assertIn("Ebrahim, Sales", window.body, "The signature of the person")
        self.assertIn(self.env.company.name, window.body, "The company under it")
        self.assertEqual(window.email_from, self.clerk.email_formatted)

    def test_send_is_the_only_thing_that_sends(self):
        adapter = self.use_adapter()
        adapter.__class__ = ToneAdapter
        window = self.window()
        window.email_cc = "boss@acme.example"
        window.body = window.body + "<p>Edited by Ebrahim</p>"
        with self.mock_mail_gateway():
            window.action_send()
        self.assertEqual(len(self._mails), 1)
        self.assertEqual(self._mails[0]["email_cc"], ["boss@acme.example"])
        self.assertEqual(self._mails[0]["email_to"], ["raghad@customer.example"])
        self.assertIn("Edited by Ebrahim", self._mails[0]["body"])
        self.assertIn("Email sent", self.record.message_ids[0].body)
        self.assertTrue(self.record.message_ids[0].subtype_id.internal, "What was sent is kept as a note")

    def test_the_notes_between_employees_are_never_quoted(self):
        adapter = self.use_adapter()
        self.window()
        sent = self.context(adapter)
        self.assertNotIn("Secret margin", sent)
        self.assertIn("how long does a delivery take", sent)

    def test_the_thread_the_record_and_the_company_files_ground_it(self):
        adapter = self.use_adapter()
        self.window()
        call = adapter.calls[0]
        self.assertEqual(call["system"], [prompt.FOCUS_PERSONA, prompt.FOCUS_BRIEFS["reply"]])
        self.assertFalse(call["tools"])
        sent = self.context(adapter)
        self.assertIn('<data source="reply_thread">', sent)
        self.assertIn("Raghad Trading", sent)
        self.assertIn('<data source="company_files">', sent)
        self.assertIn("5 working days", sent)
        self.assertEqual(sent.count("</data>"), 2, "Nothing written in the thread can close a frame")

    def test_the_tones(self):
        adapter = self.use_adapter()
        adapter.__class__ = ToneAdapter
        for tone in ("formal", "friendly", "short"):
            self.assertIn(f"({tone})", self.window(tone=tone).body)
        self.assertIn("(friendly)", self.window(tone="cheeky").body, "An unknown tone is the default one")

    def test_the_draft_is_kept_for_fifteen_minutes(self):
        adapter = self.use_adapter()
        self.window(tone="short")
        self.window(tone="short")
        self.assertEqual(len(adapter.calls), 1)
        self.window(tone="formal")
        self.assertEqual(len(adapter.calls), 2, "Another tone is another draft")
        self.window(tone="short", refresh=True)
        self.assertEqual(len(adapter.calls), 3)

    def test_it_is_in_usage_like_any_call(self):
        self.use_adapter()
        before = self.env["nudge.usage"].search_count([("user_id", "=", self.clerk.id)])
        self.window()
        self.assertEqual(self.env["nudge.usage"].search_count([("user_id", "=", self.clerk.id)]), before + 1)

    def test_the_language_of_the_customer(self):
        adapter = self.use_adapter()
        record = self.env["res.partner"].create({"name": "Raghad Arabic"})
        record.message_post(body="<p>مرحبا، متى يصل الطلب؟</p>", message_type="email", subtype_xmlid="mail.mt_comment",
                            author_id=self.customer.id)
        self.window(record=record)
        self.assertIn("Arabic", self.context(adapter))
        self.window()
        self.assertIn("the language of the customer's last message", self.context(adapter))

    def test_the_panel_offers_it_when_a_message_of_a_customer_is_there(self):
        Reply = self.env["nudge.reply"].with_user(self.clerk)
        self.assertEqual(Reply.state("res.partner", self.record.id), {"can": True})
        quiet = self.env["res.partner"].create({"name": "Nobody wrote here"})
        quiet.message_post(body="<p>Only a note</p>", message_type="comment", subtype_xmlid="mail.mt_note")
        self.assertEqual(Reply.state("res.partner", quiet.id), {"can": False})
        own = self.env["res.partner"].create({"name": "We wrote here"})
        own.with_user(self.clerk).message_post(
            body="<p>We wrote first</p>", message_type="comment", subtype_xmlid="mail.mt_comment",
            author_id=self.clerk.partner_id.id)
        self.assertEqual(Reply.state("res.partner", own.id), {"can": False}, "Our own message is not to be answered")
        self.assertEqual(Reply.state("res.partner", 0), {"can": False})
        self.assertEqual(Reply.state("not.a.model", 1), {"can": False})

    def test_nothing_to_answer_is_said(self):
        self.use_adapter()
        quiet = self.env["res.partner"].create({"name": "Nobody wrote here"})
        with self.assertRaises(UserError):
            self.window(record=quiet)

    def test_a_window_made_by_hand_is_checked_at_send(self):
        outsider = self.outsider
        window = self.env["nudge.email.review"].with_user(outsider).create({
            "res_model": "res.partner", "res_id": self.record.id, "email_to": "someone@else.example",
            "subject": "Hello", "body": "<p>Hi</p>"})
        with self.mock_mail_gateway(), self.assertRaises(UserError):
            window.action_send()
        self.assertEqual(self._mails, [], "Without Nudge, no reply goes from a window made by hand")
        wrong = self.env["nudge.email.review"].with_user(self.clerk).create({
            "res_model": "res.partner", "res_id": self.record.id, "email_to": "someone@else.example",
            "subject": "Hello", "body": "<p>Hi</p>"})
        wrong.res_model = "res.users"
        with self.mock_mail_gateway(), self.assertRaises(UserError):
            wrong.action_send()

    def test_an_attachment_of_another_record_is_copied_not_taken(self):
        adapter = self.use_adapter()
        adapter.__class__ = ToneAdapter
        other = self.env["res.partner"].create({"name": "Another customer"})
        Attachment = self.env["ir.attachment"]
        theirs = Attachment.sudo().create({
            "name": "price-list.txt", "raw": b"prices", "res_model": "res.partner", "res_id": other.id})
        mine = Attachment.with_user(self.clerk).create({
            "name": "photo.txt", "raw": b"photo", "res_model": "nudge.email.review", "res_id": 0})
        window = self.window()
        window.attachment_ids = [(6, 0, (theirs | mine).ids)]
        with self.mock_mail_gateway():
            window.action_send()
        self.assertEqual((theirs.res_model, theirs.res_id), ("res.partner", other.id), "It stays where it was")
        self.assertEqual((mine.res_model, mine.res_id), ("res.partner", self.record.id), "Their own file moves")
        copy = Attachment.sudo().search([
            ("name", "=", "price-list.txt"), ("res_model", "=", "res.partner"), ("res_id", "=", self.record.id)])
        self.assertEqual(len(copy), 1, "The record holds a copy of what was sent")

    def test_without_the_right_to_write_the_record_no_attachment_is_pointed_at_it(self):
        adapter = self.use_adapter()
        adapter.__class__ = ToneAdapter
        reader = new_test_user(self.env, login="nudge_reply_reader", name="Reader", email="reader@acme.example",
                               groups="nudge.group_nudge_user")
        mine = self.env["ir.attachment"].with_user(reader).create({
            "name": "note.txt", "raw": b"x", "res_model": "nudge.email.review", "res_id": 0})
        window = self.env["nudge.email.review"].with_user(reader).create({
            "res_model": "res.partner", "res_id": self.record.id, "email_to": "someone@else.example",
            "subject": "Hello", "body": "<p>Hi</p>", "attachment_ids": [(6, 0, mine.ids)]})
        self.assertFalse(self.record.with_user(reader).has_access("write"))
        try:
            with self.mock_mail_gateway():
                window.action_send()
        except UserError:
            return  # the person may not even send from it: nothing was pointed at the record
        self.assertNotEqual(mine.res_id, self.record.id)
