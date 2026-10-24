import hashlib
import hmac
import json
import logging
from datetime import timedelta

import requests
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from odoo.addons.nudge.llm import base as llm
from odoo.addons.nudge.llm.fake_adapter import FakeAdapter
from odoo.addons.nudge_messaging.models import nudge_messaging_api

APP_SECRET = "wa-app-secret-XYZ"
ACCESS_TOKEN = "wa-access-token-XYZ"
BOT_TOKEN = "123456:TELEGRAM-BOT-TOKEN-XYZ"
HOOK_SECRET = "tg-hook-secret-XYZ"
VERIFY = "verify-me-please"
SECRETS = (APP_SECRET, ACCESS_TOKEN, BOT_TOKEN, HOOK_SECRET)
RAGHAD_PHONE = "971500000001"
OTHER_PHONE = "971500000002"
WHATSAPP = "/nudge_messaging/whatsapp"
TELEGRAM = "/nudge_messaging/telegram"


class MessagingCase:
    """Mixin: the channels on with their keys, a person who may prepare changes (Raghad), a person who
    may only look (Ebrahim), a scripted AI provider, and the messages that would have left, kept."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Parameter = cls.env["ir.config_parameter"].sudo()
        for key, value in {
                "enabled": "True", "whatsapp.app_secret": APP_SECRET, "whatsapp.access_token": ACCESS_TOKEN,
                "whatsapp.verify_token": VERIFY, "whatsapp.phone_id": "1234",
                "telegram.bot_token": BOT_TOKEN, "telegram.webhook_secret": HOOK_SECRET}.items():
            Parameter.set_param("nudge_messaging." + key, value)
        Parameter.set_param("nudge_messaging.rate_messages", "20")
        Parameter.set_param("nudge.noai_first", "1")
        cls.env["res.lang"]._activate_lang("ar_001")
        groups = ("nudge.group_nudge_agent,base.group_partner_manager,account.group_account_invoice,"
                  "sales_team.group_sale_salesman")
        cls.raghad = new_test_user(cls.env, login="msg_raghad", groups=groups, name="Raghad Messaging")
        cls.ebrahim = new_test_user(
            cls.env, login="msg_ebrahim", groups="nudge.group_nudge_user,base.group_partner_manager",
            name="Ebrahim Messaging")
        Provider = cls.env["nudge.provider"]
        Provider.search([]).active = False
        cls.provider = Provider.create({
            "name": "Test provider", "kind": "openai_compatible", "base_url": "https://llm.invalid/v1",
            "api_key": "sk-test-secret", "model_name": "test-model"})

    def setUp(self):
        super().setUp()
        self.sent = []
        api = type(self.env["nudge.messaging.api"])
        self.patch(api, "_send_text", lambda model, channel, to, text: self.sent.append((channel, str(to), text)) or True)

    def use_adapter(self, **kwargs):
        adapter = FakeAdapter(**kwargs)
        self.patch(type(self.env["nudge.provider"]), "_get_adapter", lambda provider: adapter)
        return adapter

    def link(self, user, channel="whatsapp", sender=RAGHAD_PHONE):
        code = self.env["nudge.messaging.code"]._make(user)
        self.env["nudge.messaging.code"]._redeem(code, channel, sender)

    def whatsapp_body(self, text, message_id="wamid.1", sender=RAGHAD_PHONE, kind="text", **extra):
        message = {"id": message_id, "from": sender, "type": kind}
        if kind == "text":
            message["text"] = {"body": text}
        message.update(extra)
        return json.dumps({"entry": [{"changes": [{"value": {"messages": [message]}}]}]}).encode()

    def sign(self, body, secret=APP_SECRET):
        return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def say_whatsapp(self, text, **kwargs):
        body = self.whatsapp_body(text, **kwargs)
        response = self.url_open(WHATSAPP, data=body, headers={
            "X-Hub-Signature-256": self.sign(body), "Content-Type": "application/json"})
        self.answer_the_queue()
        return response

    def answer_the_queue(self):
        """The webhook only keeps the message; the scheduled action answers it"""
        self.env["nudge.messaging"]._cron_answer()

    def say_telegram(self, text, update_id=1, message_id=1, chat=555, secret=HOOK_SECRET, **extra):
        body = json.dumps({"update_id": update_id, "message": dict(
            {"message_id": message_id, "chat": {"id": chat, "type": "private"}, "text": text}, **extra)}).encode()
        response = self.url_open(TELEGRAM, data=body, headers={
            "X-Telegram-Bot-Api-Secret-Token": secret, "Content-Type": "application/json"})
        self.answer_the_queue()
        return response

    def overdue_invoices(self, count=3):
        customer = self.env["res.partner"].create({"name": "Raghad Trading"})
        moves = self.env["account.move"]
        for index in range(count):
            moves |= self.env["account.move"].create({
                "move_type": "out_invoice", "partner_id": customer.id,
                "invoice_date": "2020-01-01", "invoice_date_due": "2020-02-01",
                "invoice_line_ids": [(0, 0, {"name": f"Item {index}", "quantity": 1, "price_unit": 100.0})]})
        moves.action_post()
        return moves


@tagged("post_install", "-at_install", "nudge_messaging")
class TestDoors(MessagingCase, HttpCase):
    """The webhooks: proof first, then everything else"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.country_id = cls.env.ref("base.ae")

    def test_a_forged_signature_is_refused_and_nothing_is_read(self):
        self.link(self.raghad)
        body = self.whatsapp_body("how many overdue invoices")
        for header in ("sha256=" + "0" * 64, self.sign(body, "another-secret"), "", "garbage"):
            reply = self.url_open(WHATSAPP, data=body, headers={"X-Hub-Signature-256": header})
            self.assertEqual(reply.status_code, 403)
            self.assertNotIn("Raghad", reply.text)
        self.assertFalse(self.sent)
        self.assertFalse(self.env["nudge.messaging.message"].search([]))

    def test_a_wrong_telegram_secret_is_refused(self):
        self.link(self.raghad, "telegram", "555")
        self.assertEqual(self.say_telegram("hello", secret="wrong").status_code, 403)
        self.assertEqual(self.say_telegram("hello", secret="").status_code, 403)
        self.assertFalse(self.sent)

    def test_without_the_secret_set_nothing_is_accepted(self):
        self.env["ir.config_parameter"].sudo().set_param("nudge_messaging.whatsapp.app_secret", False)
        body = self.whatsapp_body("hi")
        self.assertEqual(self.url_open(WHATSAPP, data=body, headers={
            "X-Hub-Signature-256": self.sign(body, "")}).status_code, 403)

    def test_switched_off_the_doors_are_shut(self):
        self.env["ir.config_parameter"].sudo().set_param("nudge_messaging.enabled", "False")
        self.assertEqual(self.say_whatsapp("hi").status_code, 403)
        self.assertEqual(self.say_telegram("hi").status_code, 403)

    def test_the_verify_handshake_of_meta(self):
        good = self.url_open(f"{WHATSAPP}?hub.mode=subscribe&hub.verify_token={VERIFY}&hub.challenge=4242")
        self.assertEqual((good.status_code, good.text), (200, "4242"))
        bad = self.url_open(f"{WHATSAPP}?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=4242")
        self.assertEqual(bad.status_code, 403)

    def test_a_body_over_one_megabyte_is_refused(self):
        body = b"x" * (1024 * 1024 + 10)
        try:
            status = self.url_open(WHATSAPP, data=body, headers={"X-Hub-Signature-256": self.sign(body)}).status_code
        except requests.ConnectionError:
            # The server may close the connection before the client has finished sending: also a refusal
            status = 413
        self.assertEqual(status, 413)

    def test_an_unknown_sender_gets_one_polite_line_and_nothing_else(self):
        self.assertEqual(self.say_whatsapp("كم فاتورة متأخرة", sender=OTHER_PHONE).status_code, 200)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][:2], ("whatsapp", OTHER_PHONE))
        self.assertNotIn("Odoo", self.env["nudge.run"].sudo().search([]).mapped("origin"))
        # no Request, no step, no usage, no conversation: the question was not even kept
        self.assertFalse(self.env["nudge.run"].sudo().search([("origin", "=", "WhatsApp")]))
        self.assertFalse(self.env["nudge.messaging.link"].sudo().search([("sender_id", "=", OTHER_PHONE)]))

    def test_a_linked_user_asking_for_overdue_invoices_gets_a_count(self):
        # Demo data may bring overdue invoices of its own: the count is theirs and these three
        before = self.env["account.move"].with_user(self.raghad).search_count([
            ("move_type", "=", "out_invoice"), ("state", "=", "posted"),
            ("payment_state", "in", ("not_paid", "partial")), ("invoice_date_due", "<", fields.Date.today())])
        self.overdue_invoices(3)
        self.link(self.raghad)
        self.assertEqual(self.say_whatsapp("كم فاتورة متأخرة").status_code, 200)
        channel, to, text = self.sent[-1]
        self.assertEqual((channel, to), ("whatsapp", RAGHAD_PHONE))
        self.assertIn(str(before + 3), text)
        self.assertLessEqual(len(text), 1500)
        run = self.env["nudge.run"].sudo().search([("origin", "=", "WhatsApp")])
        self.assertEqual(run.user_id, self.raghad)

    def test_a_quotation_is_prepared_never_made(self):
        self.env["res.partner"].create({"name": "راغد"})
        self.link(self.raghad)
        orders = self.env["sale.order"].search_count([])
        self.say_whatsapp("اعمل عرض سعر لراغد")
        text = self.sent[-1][2]
        step = self.env["nudge.step"].sudo().search([("origin", "=", "WhatsApp")])
        self.assertEqual(len(step), 1)
        self.assertEqual((step.state, step.tool), ("pending", "prepare_create"))
        self.assertIn(f"id={step.id}&view_type=form", text)
        self.assertEqual(self.env["sale.order"].search_count([]), orders)
        # The yes is given in Odoo, as the person
        step.with_user(self.raghad).action_messaging_apply()
        self.assertEqual(step.state, "done")
        self.assertEqual(self.env["sale.order"].search_count([]), orders + 1)
        self.assertEqual(self.env["sale.order"].search([], limit=1).create_uid, self.raghad)

    def test_a_yes_given_after_leaving_the_company_is_refused(self):
        self.env["res.partner"].create({"name": "راغد"})
        self.link(self.raghad)
        orders = self.env["sale.order"].search_count([])
        self.say_whatsapp("اعمل عرض سعر لراغد")
        step = self.env["nudge.step"].sudo().search([("origin", "=", "WhatsApp")])
        self.assertEqual(step.company_id, self.env.company)
        step.company_id = self.env["res.company"].create({"name": "Nudge Messaging Other Company"})
        with self.assertRaises(UserError):
            step.with_user(self.raghad).action_messaging_apply()
        self.assertEqual((step.state, self.env["sale.order"].search_count([])), ("pending", orders))

    def test_another_person_cannot_say_yes_for_them(self):
        self.env["res.partner"].create({"name": "راغد"})
        self.link(self.raghad)
        self.say_whatsapp("اعمل عرض سعر لراغد")
        step = self.env["nudge.step"].sudo().search([("origin", "=", "WhatsApp")])
        with self.assertRaises(AccessError):
            step.with_user(self.ebrahim).action_messaging_apply()
        with self.assertRaises(AccessError):
            step.with_user(self.ebrahim).action_messaging_refuse()
        self.assertEqual(step.state, "pending")
        step.with_user(self.raghad).action_messaging_refuse()
        self.assertEqual(step.state, "declined")

    def test_the_webhook_only_keeps_the_message_and_the_scheduled_action_answers_it(self):
        """Release-2 review B2: nothing is paid for, and nobody waits, inside the request"""
        self.overdue_invoices(2)
        self.link(self.raghad)
        body = self.whatsapp_body("كم فاتورة متأخرة", message_id="wamid.queued")
        response = self.url_open(WHATSAPP, data=body, headers={
            "X-Hub-Signature-256": self.sign(body), "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.sent)
        line = self.env["nudge.messaging.message"].sudo().search([("ext_id", "=", "wamid.queued")])
        self.assertEqual((line.state, bool(line.payload)), ("received", True))
        self.assertFalse(self.env["nudge.run"].sudo().search([("origin", "=", "WhatsApp")]))
        self.answer_the_queue()
        self.assertEqual((line.state, line.payload), ("answered", False))
        self.assertIn("2", self.sent[-1][2])

    def test_a_message_begun_and_never_ended_is_begun_again_not_lost(self):
        self.overdue_invoices(2)
        self.link(self.raghad)
        Message = self.env["nudge.messaging.message"].sudo()
        self.env["nudge.messaging"]._receive("whatsapp", {
            "id": "wamid.died", "sender": RAGHAD_PHONE, "kind": "text", "text": "كم فاتورة متأخرة", "media": {}})
        line = Message.search([("ext_id", "=", "wamid.died")])
        # a worker took it three minutes ago and died
        line.write({"tries": 1, "started_at": fields.Datetime.subtract(fields.Datetime.now(), minutes=3)})
        self.answer_the_queue()
        self.assertEqual((line.state, line.tries), ("answered", 2))
        self.assertEqual(len(self.sent), 1)
        # begun three times without an end: it fails, it is not tried for ever
        self.env["nudge.messaging"]._receive("whatsapp", {
            "id": "wamid.poison", "sender": RAGHAD_PHONE, "kind": "text", "text": "x", "media": {}})
        poison = Message.search([("ext_id", "=", "wamid.poison")])
        poison.write({"tries": 3, "started_at": fields.Datetime.subtract(fields.Datetime.now(), minutes=3)})
        self.answer_the_queue()
        self.assertEqual((poison.state, poison.payload), ("failed", False))

    def test_the_log_of_old_messages_goes_with_the_autovacuum(self):
        Message = self.env["nudge.messaging.message"].sudo()
        old = Message.create({"channel": "whatsapp", "ext_id": "old-1", "sender_hash": "x"})
        self.env.cr.execute(
            "UPDATE nudge_messaging_message SET create_date = now() - interval '100 days' WHERE id = %s", [old.id])
        recent = Message.create({"channel": "whatsapp", "ext_id": "new-1", "sender_hash": "x"})
        Message._gc_old()
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_two_phones_with_the_same_code_do_not_both_link(self):
        Code = self.env["nudge.messaging.code"]
        code = Code._make(self.raghad)
        row = Code.sudo().search([("user_id", "=", self.raghad.id)])
        self.assertTrue(Code._redeem(code, "whatsapp", RAGHAD_PHONE))
        # the other phone read the code as unused a moment before the first one took it
        row.invalidate_recordset()
        with patch.object(type(Code), "search", lambda model, *a, **k: row):
            self.assertFalse(Code._redeem(code, "whatsapp", OTHER_PHONE))
        self.assertFalse(self.env["nudge.messaging.link"].sudo()._link_for("whatsapp", OTHER_PHONE))

    def test_too_many_wrong_codes_close_the_door_for_every_phone(self):
        Message = self.env["nudge.messaging.message"].sudo()
        for index in range(60):
            Message.create({"channel": "whatsapp", "ext_id": f"wrong-{index}", "sender_hash": f"s{index}",
                            "state": "wrong_code"})
        code = self.env["nudge.messaging.code"]._make(self.raghad)
        self.env["nudge.messaging"]._receive("whatsapp", {
            "id": "wamid.guess", "sender": OTHER_PHONE, "kind": "text", "text": code, "media": {}})
        self.answer_the_queue()
        self.assertFalse(self.env["nudge.messaging.link"].sudo()._link_for("whatsapp", OTHER_PHONE))

    def test_a_repeated_message_is_answered_once(self):
        self.overdue_invoices(2)
        self.link(self.raghad)
        for _ in range(3):
            self.say_whatsapp("كم فاتورة متأخرة", message_id="wamid.same")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.env["nudge.run"].sudo().search_count([("origin", "=", "WhatsApp")]), 1)

    def test_the_limit_per_sender(self):
        self.env["ir.config_parameter"].sudo().set_param("nudge_messaging.rate_messages", "3")
        for index in range(6):
            self.say_whatsapp("hello", message_id=f"wamid.{index}", sender=OTHER_PHONE)
        # one polite line an hour, one "wait" line, then silence
        self.assertEqual(len(self.sent), 2)
        self.assertIn("wait", self.sent[-1][2].lower())

    def test_the_other_channel_counts_alone(self):
        self.env["ir.config_parameter"].sudo().set_param("nudge_messaging.rate_messages", "1")
        self.say_whatsapp("hello", sender="555")
        self.say_telegram("hello", chat=555)
        self.assertEqual({sent[0] for sent in self.sent}, {"whatsapp", "telegram"})

    def test_telegram_links_and_answers(self):
        self.overdue_invoices(2)
        code = self.env["nudge.messaging.code"]._make(self.raghad)
        self.say_telegram(code, update_id=10, message_id=10)
        self.assertEqual(self.env["nudge.messaging.link"].sudo().search([("channel", "=", "telegram")]).user_id,
                         self.raghad)
        self.say_telegram("how many overdue invoices", update_id=11, message_id=11)
        self.assertIn("2", self.sent[-1][2])
        run = self.env["nudge.run"].sudo().search([("origin", "=", "Telegram")])
        self.assertEqual(len(run), 1)

    def test_a_group_chat_is_ignored(self):
        self.link(self.raghad, "telegram", "555")
        body = json.dumps({"update_id": 1, "message": {
            "message_id": 1, "chat": {"id": 555, "type": "group"}, "text": "hi"}}).encode()
        self.url_open(TELEGRAM, data=body, headers={"X-Telegram-Bot-Api-Secret-Token": HOOK_SECRET})
        self.assertFalse(self.sent)

    def test_the_keys_are_never_in_a_log_or_an_answer(self):
        self.link(self.raghad)
        with self.assertLogs(level=logging.DEBUG) as logs:
            logging.getLogger("odoo.addons.nudge_messaging").info("start")
            replies = [
                self.say_whatsapp("hello"),
                self.url_open(WHATSAPP, data=b"{}", headers={"X-Hub-Signature-256": "sha256=bad"}),
                self.say_telegram("hello", secret="bad"),
                self.url_open(f"{WHATSAPP}?hub.mode=subscribe&hub.verify_token=x&hub.challenge=1")]
        everything = "\n".join(logs.output) + "".join(reply.text for reply in replies) + "".join(
            text for _c, _t, text in self.sent) + "".join(str(reply.headers) for reply in replies)
        for secret in SECRETS:
            self.assertNotIn(secret, everything)

    def test_a_failing_channel_logs_the_kind_of_failure_only(self):
        """The address of a Telegram call holds the token: a failed call must not write it down"""
        def boom(url, **kwargs):
            raise requests.ConnectionError(f"cannot reach {url}")

        self.patch(requests, "post", boom)
        api = type(self.env["nudge.messaging.api"])
        original = nudge_messaging_api.NudgeMessagingApi.__dict__["_send_text"]
        self.patch(api, "_send_text", original)
        lines = []

        class Keep(logging.Handler):
            def emit(self, record):
                lines.append(record.getMessage())

        logger = logging.getLogger(nudge_messaging_api.__name__)
        handler, level, disabled = Keep(), logger.level, logger.disabled
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.disabled = False
        try:
            sent = self.env["nudge.messaging.api"]._send_text("telegram", "555", "hello")
        finally:
            logger.removeHandler(handler)
            logger.setLevel(level)
            logger.disabled = disabled
        self.assertFalse(sent)
        self.assertNotIn(BOT_TOKEN, "; ".join(lines))
        self.assertIn("ConnectionError", "; ".join(lines))

    def test_the_headers_carry_the_access_token_and_nothing_else_does(self):
        calls = []

        class Done:
            status_code = 200

        self.patch(requests, "post", lambda url, **kwargs: calls.append((url, kwargs)) or Done())
        self.patch(type(self.env["nudge.messaging.api"]), "_send_text",
                   nudge_messaging_api.NudgeMessagingApi.__dict__["_send_text"])
        self.assertTrue(self.env["nudge.messaging.api"]._send_text("whatsapp", RAGHAD_PHONE, "hello"))
        url, kwargs = calls[0]
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {ACCESS_TOKEN}")
        self.assertNotIn(ACCESS_TOKEN, url + json.dumps(kwargs["json"]))

    def test_private_mode_answers_with_rules_only(self):
        """Nothing goes to a provider: a count is answered, a free question gets the rules' help"""
        self.overdue_invoices(2)
        self.link(self.raghad)
        adapter = self.use_adapter(reply="This must never be said.")
        self.env.company.nudge_private_mode = True
        self.env["ir.config_parameter"].sudo().set_param("nudge_messaging.allow_private", "True")
        self.say_whatsapp("how many overdue invoices", message_id="a")
        self.assertIn("2", self.sent[-1][2])
        self.say_whatsapp("write me a poem about tiles", message_id="b")
        self.assertNotIn("never be said", self.sent[-1][2])
        self.assertFalse(adapter.calls)
        # A voice note is not listened to, a picture is not read
        self.say_whatsapp("", message_id="c", kind="audio", audio={"id": "m1", "mime_type": "audio/ogg"})
        self.assertIn("text", self.sent[-1][2].lower())
        self.assertFalse(adapter.calls)

    def test_unlinking_from_the_phone(self):
        self.link(self.raghad)
        self.say_whatsapp("unlink", message_id="u1")
        self.assertFalse(self.env["nudge.messaging.link"]._link_for("whatsapp", RAGHAD_PHONE))
        self.say_whatsapp("how many overdue invoices", message_id="u2")
        self.assertFalse(self.env["nudge.run"].sudo().search([("origin", "=", "WhatsApp")]))


@tagged("post_install", "-at_install", "nudge_messaging")
class TestEngine(MessagingCase, HttpCase):
    """The AI path: scripted provider, read tools and prepare tools only"""

    def receive(self, text, channel="whatsapp", sender=RAGHAD_PHONE, message_id=None, **event):
        message_id = message_id or f"m{len(self.sent)}-{fields.Datetime.now()}-{text[:5]}"
        self.env["nudge.messaging"]._receive(channel, dict(
            {"id": message_id, "sender": sender, "kind": "text", "text": text, "media": {}}, **event))
        self.answer_the_queue()
        return self.sent[-1][2] if self.sent else ""

    def test_the_provider_looks_up_with_a_read_tool_and_answers_in_words(self):
        partner = self.env["res.partner"].create({"name": "Raghad Contracting", "email": "r@example.com"})
        self.link(self.raghad)
        adapter = self.use_adapter(script=[
            [llm.tool_use_event("t1", "search_records", {"model": "res.partner", "conditions": [
                {"field": "name", "operator": "contains", "value": "Raghad Contracting"}]}),
             llm.stop_event(llm.STOP_TOOL_USE)],
            [llm.text_event("Raghad Contracting is a contact."), llm.stop_event(llm.STOP_END)]])
        text = self.receive("who is Raghad Contracting, tell me everything about them")
        self.assertIn("Raghad Contracting is a contact.", text)
        self.assertIn(f"id={partner.id}&view_type=form", text)
        names = {tool["name"] for tool in adapter.calls[0]["tools"]}
        self.assertIn("search_records", names)
        self.assertIn("prepare_create", names)
        # nothing that applies a change, and nothing for developers
        self.assertFalse(names & {"bulk_apply", "prepare_plan", "prepare_delete", "execute_sql", "run_python"})
        self.assertLessEqual(len(text), 1500)

    def test_a_change_asked_to_the_provider_is_prepared_and_waits(self):
        self.link(self.raghad)
        contacts = self.env["res.partner"].search_count([])
        self.use_adapter(script=[
            [llm.tool_use_event("t1", "prepare_create", {"model": "res.partner", "values": [
                {"field": "name", "value": "Ebrahim New Contact"}]}), llm.stop_event(llm.STOP_TOOL_USE)],
            [llm.text_event("I prepared the contact Ebrahim New Contact."), llm.stop_event(llm.STOP_END)]])
        text = self.receive("add a new contact called Ebrahim New Contact with a long description of it")
        step = self.env["nudge.step"].sudo().search([("origin", "=", "WhatsApp"), ("tool", "=", "prepare_create")])
        self.assertEqual(step.state, "pending")
        self.assertIn(f"id={step.id}&view_type=form", text)
        self.assertEqual(self.env["res.partner"].search_count([]), contacts)
        self.assertEqual(step.run_id.state, "waiting")

    def test_a_person_who_may_only_look_is_not_offered_the_prepare_tools(self):
        self.link(self.ebrahim, sender=OTHER_PHONE)
        adapter = self.use_adapter(reply="Nothing to prepare.")
        self.receive("tell me something about the weather in the database", sender=OTHER_PHONE)
        names = {tool["name"] for tool in adapter.calls[0]["tools"]}
        self.assertIn("search_odoo_documentation", names)
        self.assertFalse(names & {"prepare_create", "prepare_write", "call_method", "document_to_record"})

    def test_a_tool_outside_the_list_is_refused_to_the_model(self):
        self.link(self.raghad)
        adapter = self.use_adapter(script=[
            [llm.tool_use_event("t1", "execute_sql", {"query": "delete from res_partner"}),
             llm.stop_event(llm.STOP_TOOL_USE)],
            [llm.text_event("I cannot."), llm.stop_event(llm.STOP_END)]])
        self.receive("please delete every partner in the database right now")
        result = adapter.calls[1]["turns"][-1]["results"][0]
        self.assertTrue(result["is_error"])

    def test_the_budget_stops_the_provider(self):
        self.link(self.raghad)
        adapter = self.use_adapter(reply="Should not be asked.")
        self.env["ir.config_parameter"].sudo().set_param("nudge.budget_user_tokens", "1")
        self.env["nudge.usage"].sudo().create({
            "user_id": self.raghad.id, "company_id": self.raghad.company_id.id,
            "date": fields.Date.context_today(self.raghad), "kind": "chat", "input_tokens": 50})
        text = self.receive("explain how the weather affects quotations in general terms")
        self.assertFalse(adapter.calls)
        self.assertIn("tokens", text)

    def test_every_message_is_a_request_with_its_origin(self):
        self.link(self.raghad)
        self.use_adapter(reply="Fine.")
        self.receive("hello there, how are you doing today my friend")
        run = self.env["nudge.run"].sudo().search([("origin", "=", "WhatsApp")])
        self.assertEqual((run.user_id, run.state), (self.raghad, "done"))
        self.assertTrue(run.name.startswith("WhatsApp:"))

    def test_a_voice_note_is_written_out_when_it_can_be(self):
        self.link(self.raghad)
        api = type(self.env["nudge.messaging.api"])
        self.patch(api, "_download", lambda model, channel, media: (b"RIFFxxxxWAVE", "audio/ogg"))
        voice = type(self.env["nudge.voice"])
        self.patch(voice, "_transcribe", lambda model, audio, language="", hints=None: "كم فاتورة متأخرة")
        self.overdue_invoices(4)
        text = self.receive("", kind="audio", media={"id": "m1", "mime": "audio/ogg"})
        self.assertIn("4", text)

    def test_a_voice_note_or_a_file_counts_in_the_limit_of_simultaneous_answers(self):
        self.link(self.raghad)
        marks = []
        messaging = type(self.env["nudge.messaging"])
        original = messaging._mark
        self.patch(messaging, "_mark", lambda model, channel, active, provider=None: (marks.append(active), original(model, channel, active, provider))[1])
        self.patch(type(self.env["nudge.messaging.api"]), "_download",
                   lambda model, channel, media: (b"RIFFxxxxWAVE", "audio/ogg"))
        self.patch(type(self.env["nudge.voice"]), "_transcribe", lambda model, audio, language="", hints=None: "hello")
        self.receive("", kind="audio", media={"id": "m1", "mime": "audio/ogg"})
        self.assertEqual(marks[:2], [True, False])

    def test_an_error_in_the_database_inside_an_answer_is_the_messages_and_the_queue_goes_on(self):
        self.link(self.raghad)
        messaging = type(self.env["nudge.messaging"])
        original = messaging._converse

        def broken(model, channel, event):
            if event["id"] == "wamid.bad":
                model.env.cr.execute("SELECT 1/0")
            return original(model, channel, event)

        self.patch(messaging, "_converse", broken)
        for name in ("wamid.bad", "wamid.good"):
            self.env["nudge.messaging"]._receive("whatsapp", {
                "id": name, "sender": RAGHAD_PHONE, "kind": "text", "text": "hello there my friend", "media": {}})
        self.use_adapter(reply="Fine.")
        self.answer_the_queue()
        states = {line.ext_id: line.state for line in self.env["nudge.messaging.message"].sudo().search([])}
        self.assertEqual((states["wamid.bad"], states["wamid.good"]), ("failed", "answered"))
        self.assertIn("went wrong", self.sent[0][2])

    def test_a_voice_note_that_cannot_be_heard_asks_for_text(self):
        self.link(self.raghad)
        self.patch(type(self.env["nudge.messaging.api"]), "_download",
                   lambda model, channel, media: (b"OggS-no-converter", "audio/ogg"))
        self.patch(type(self.env["nudge.messaging"]), "_to_wav",
                   lambda model, data: (_ for _ in ()).throw(UserError("no converter")))
        text = self.receive("", kind="audio", media={"id": "m1", "mime": "audio/ogg"})
        self.assertIn("text", text.lower())

    def test_a_picture_becomes_a_draft_waiting_for_the_yes(self):
        self.link(self.raghad)
        self.patch(type(self.env["nudge.messaging.api"]), "_download",
                   lambda model, channel, media: (b"\x89PNG\r\n\x1a\n" + b"0" * 40, "image/png"))
        calls = []
        from odoo.addons.nudge.tools import base as tool_base
        real = tool_base.call

        def spy(env, name, arguments, screen=None, mode="talk"):
            calls.append((name, arguments))
            if name == "document_to_record":
                return tool_base.ToolResult("x", pending={
                    "summary": "Create: Lead", "res_model": "crm.lead", "res_ids": [], "values": {},
                    "preview": {"records": [], "lines": [{"label": "Name", "value": "Raghad lead"}]}})
            return real(env, name, arguments, screen, mode)

        self.patch(tool_base, "call", spy)
        self.use_adapter()
        text = self.receive("", kind="image", media={"id": "m1", "mime": "image/png"})
        self.assertEqual(calls[0][0], "document_to_record")
        step = self.env["nudge.step"].sudo().search([("origin", "=", "WhatsApp"), ("tool", "=", "document_to_record")])
        self.assertEqual(step.state, "pending")
        self.assertIn(f"id={step.id}&view_type=form", text)

    def test_the_answer_never_goes_over_fifteen_hundred_characters(self):
        self.link(self.raghad)
        self.use_adapter(reply="word " * 1000)
        text = self.receive("give me a very long answer about everything in the system please")
        self.assertLessEqual(len(text), 1500)

    def test_a_phone_belongs_to_one_person_and_a_person_has_one_phone(self):
        self.link(self.raghad, sender=RAGHAD_PHONE)
        self.link(self.ebrahim, sender=RAGHAD_PHONE)
        Link = self.env["nudge.messaging.link"].sudo()
        self.assertEqual(Link._link_for("whatsapp", RAGHAD_PHONE).user_id, self.ebrahim)
        self.link(self.ebrahim, sender=OTHER_PHONE)
        self.assertFalse(Link._link_for("whatsapp", RAGHAD_PHONE))
        self.assertEqual(Link._link_for("whatsapp", OTHER_PHONE).user_id, self.ebrahim)

    def test_a_code_works_once_and_expires(self):
        code = self.env["nudge.messaging.code"]._make(self.raghad)
        self.assertEqual(len(code), 6)
        Code = self.env["nudge.messaging.code"]
        self.assertTrue(Code._redeem(code, "whatsapp", RAGHAD_PHONE))
        self.assertFalse(Code._redeem(code, "whatsapp", OTHER_PHONE))
        old = Code._make(self.raghad)
        Code.sudo().search([("used", "=", False)]).expires_at = fields.Datetime.subtract(fields.Datetime.now(), minutes=1)
        self.assertFalse(Code._redeem(old, "whatsapp", OTHER_PHONE))

    def test_a_person_revokes_their_own_phone_only(self):
        self.link(self.raghad)
        link = self.env["nudge.messaging.link"].sudo()._link_for("whatsapp", RAGHAD_PHONE)
        with self.assertRaises(AccessError):
            link.with_user(self.ebrahim).action_revoke()
        link.with_user(self.raghad).action_revoke()
        self.assertFalse(link.active)

    def test_the_settings_never_give_a_key_back(self):
        settings = self.env["res.config.settings"].with_user(self.env.ref("base.user_admin")).create({})
        self.assertTrue(settings.nudge_msg_whatsapp_keys_set)
        for field in ("nudge_msg_whatsapp_app_secret", "nudge_msg_whatsapp_access_token",
                      "nudge_msg_telegram_bot_token", "nudge_msg_telegram_webhook_secret"):
            self.assertFalse(settings[field])
        # blank keeps; a value replaces
        settings.execute()
        self.assertEqual(self.env["nudge.messaging.api"]._param("whatsapp.app_secret"), APP_SECRET)
        settings = self.env["res.config.settings"].with_user(self.env.ref("base.user_admin")).create(
            {"nudge_msg_whatsapp_app_secret": "new-secret"})
        settings.execute()
        self.assertEqual(self.env["nudge.messaging.api"]._param("whatsapp.app_secret"), "new-secret")


@tagged("post_install", "-at_install", "nudge_messaging")
class TestMessagingRetention(TransactionCase):
    """The log of messages and the linking codes do not grow for ever"""

    def test_old_messages_and_codes_are_collected(self):
        Message = self.env["nudge.messaging.message"]
        fresh = Message._reserve("telegram", "fresh-1", "raghad")
        old = Message._reserve("telegram", "old-1", "raghad")
        self.env.cr.execute("UPDATE nudge_messaging_message SET create_date = (now() AT TIME ZONE 'UTC') - interval '31 days' WHERE id = %s",
                            [old.id])
        self.env["ir.config_parameter"].sudo().set_param("nudge_messaging.retention_days", "30")
        Message.invalidate_model()
        Message._gc_old()
        self.assertTrue(fresh.exists())
        self.assertFalse(old.exists())
        self.env["ir.config_parameter"].sudo().set_param("nudge_messaging.retention_days", "7")
        self.env.cr.execute("UPDATE nudge_messaging_message SET create_date = (now() AT TIME ZONE 'UTC') - interval '8 days' WHERE id = %s",
                            [fresh.id])
        Message.invalidate_model()
        Message._gc_old()
        self.assertFalse(fresh.exists(), "The retention is a setting")

        Code = self.env["nudge.messaging.code"]
        user = new_test_user(self.env, login="raghad_code", groups="base.group_user")
        Code._make(user)
        stale = Code.sudo().create({"user_id": user.id, "code_hash": "x" * 32, "used": True,
                                    "expires_at": fields.Datetime.now() - timedelta(days=2)})
        live = Code.sudo().search([("user_id", "=", user.id), ("used", "=", False)])
        Code._gc_old()
        self.assertFalse(stale.exists())
        self.assertTrue(live.exists())

    def test_the_preferences_page_is_added_to_the_notebook_that_exists_on_every_series(self):
        view = self.env.ref("nudge_messaging.view_users_form_simple_modif_nudge_messaging")
        self.assertIn("//notebook", view.arch_db)
        self.assertNotIn("page_security", view.arch_db, "That page is Odoo 19 only: the install fails on 17 and 18")
        form = self.env["res.users"].get_view(self.env.ref("base.view_users_form_simple_modif").id)
        self.assertIn('name="nudge_messaging"', form["arch"])
