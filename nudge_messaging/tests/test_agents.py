"""Nudge Agents on WhatsApp and Telegram: a linked phone is served as its user through the agent, a phone nobody
linked only through ``_answer_public``, several agents through the front desk, and without a published binding the
channel works as it always did. The engine is Nudge's own (tested there): here it is mocked, and what the channel
passes to it is checked."""
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.sql_db import db_connect
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.models.nudge_slot import NudgeBusy
from odoo.addons.nudge.tools import pinned_http

from ..models import nudge_messaging, nudge_messaging_api

from .test_messaging import MessagingCase, OTHER_PHONE, RAGHAD_PHONE

STRANGER = "971500000099"


@tagged("post_install", "-at_install", "nudge_messaging")
class TestAgentsOnPhones(MessagingCase, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.service = new_test_user(
            cls.env, login="msg_service", groups="base.group_portal,nudge.group_agent_service", name="Phone service")

    def setUp(self):
        super().setUp()
        self.number = 0
        self.asked = []
        self.bindings = []
        agent_class = type(self.env["nudge.agent"])

        def answer(agent, text, lang=None, rehearsal=False, conversation=None, mode=None, binding=None,
                   visitor_key=None):
            self.asked.append(("person", agent.id, agent.env.user.id, text, bool(conversation)))
            self.bindings.append(binding)
            return {"text": "AGENT %s: %s" % (agent.name, text), "source": "ai", "tool_calls": [],
                    "conversation_id": False, "error": False}

        def public(agent, text, binding, lang=None, conversation=None, visitor_key=None, consent=False):
            self.asked.append(("public", agent.id, binding.id, text, visitor_key))
            return {"text": "PUBLIC %s" % agent.name, "source": "ai", "tool_calls": [],
                    "conversation_id": False, "error": False}

        self.patch(agent_class, "_answer", answer)
        # The engine of builder G adds these two; create=True keeps the test valid whichever lands first
        patcher = patch.object(agent_class, "_answer_public", public, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def agent(self, name, users=None, state="published"):
        return self.env["nudge.agent"].sudo().create({
            "name": name, "company_ids": [(6, 0, [self.company.id])],
            "user_ids": [(6, 0, [user.id for user in users or []])], "state": state,
            # A public channel is published only with its budgets (H1)
            "budget_month": 100.0, "budget_user_day": 100000})

    def bind(self, agent, channel="whatsapp", service=False):
        return self.env["nudge.agent.binding"].sudo().create({
            "agent_id": agent.id, "channel": channel, "published": True,
            "acts_as": "service" if service else "person",
            "service_user_id": self.service.id if service else False})

    def receive(self, text, sender=RAGHAD_PHONE, channel="whatsapp"):
        self.number += 1
        ext = "agent-%s" % self.number
        self.env["nudge.messaging"]._receive(
            channel, {"id": ext, "sender": sender, "kind": "text", "text": text, "media": {}})
        self.env["nudge.messaging"]._cron_answer()
        state = self.env["nudge.messaging.message"].sudo().search([("ext_id", "=", ext)]).state
        return state, [text for _channel, to, text in self.sent if to == sender][-1:]

    def test_a_linked_phone_is_answered_by_the_agent_as_its_user(self):
        self.link(self.raghad)
        agent = self.agent("Sales helper", [self.raghad])
        binding = self.bind(agent)
        state, said = self.receive("how are the sales")
        self.assertEqual(state, "answered")
        self.assertEqual(said, ["AGENT Sales helper: how are the sales"])
        self.assertEqual(self.asked, [("person", agent.id, self.raghad.id, "how are the sales", False)])
        # The channel goes with the turn: its office hours and its hand-over apply
        self.assertEqual(self.bindings, [binding])

    def test_a_linked_person_the_agent_is_not_open_to_is_refused_and_no_agent_runs(self):
        self.link(self.ebrahim, sender=OTHER_PHONE)
        self.bind(self.agent("Sales helper", [self.raghad]))
        state, said = self.receive("how are the sales", sender=OTHER_PHONE)
        self.assertEqual(self.asked, [])
        self.assertIn("No assistant on this channel is open to you.", said[0])

    def test_a_phone_nobody_linked_goes_only_through_answer_public_with_a_hashed_key(self):
        agent = self.agent("Front door")
        binding = self.bind(agent, service=True)
        state, said = self.receive("what do you sell", sender=STRANGER)
        self.assertEqual(state, "answered")
        self.assertEqual(said, ["PUBLIC Front door"])
        kind, agent_id, binding_id, text, key = self.asked[0]
        self.assertEqual((kind, agent_id, binding_id, text), ("public", agent.id, binding.id, "what do you sell"))
        self.assertTrue(key.startswith("whatsapp:"))
        self.assertNotIn(STRANGER, key, "The key holds the hash of the sender, never the number")
        self.assertEqual([entry[0] for entry in self.asked], ["public"], "No turn as a person")

    def test_a_phone_nobody_linked_respects_private_mode_and_never_reads_an_error(self):
        # L1, M4
        self.bind(self.agent("Front door"), service=True)
        self.env.company.nudge_private_mode = True
        state, said = self.receive("what do you sell", sender=STRANGER)
        self.assertEqual(self.asked, [], "No agent in private mode")
        self.assertIn("Private mode is on", said[0])
        self.env.company.nudge_private_mode = False
        agent_class = type(self.env["nudge.agent"])
        self.patch(agent_class, "_answer_public", lambda *args, **kwargs: (_ for _ in ()).throw(
            UserError("nudge.secret.model(42) is not yours")))
        state, said = self.receive("what do you sell again", sender=STRANGER)
        self.assertNotIn("secret", said[0])

    def test_numbers_nobody_linked_take_a_few_places_at_once(self):
        # H1: a place is a lock of the transaction; held elsewhere, the message waits in the queue
        self.bind(self.agent("Front door"), service=True)
        self.env["ir.config_parameter"].sudo().set_param(nudge_messaging.MAX_PUBLIC_PARAM, "1")
        with db_connect(self.env.cr.dbname).cursor() as other:
            other.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s), 0)", (nudge_messaging.PUBLIC_LOCK,))
            with self.assertRaises(NudgeBusy):
                self.env["nudge.messaging"]._public_reply("whatsapp", STRANGER, "what do you sell", "en_US")
        self.assertEqual(self.asked, [])

    def test_a_person_binding_never_answers_a_phone_nobody_linked(self):
        self.bind(self.agent("Staff only", [self.raghad]))
        state, said = self.receive("what do you sell", sender=STRANGER)
        self.assertEqual(state, "unknown")
        self.assertEqual(self.asked, [])
        self.assertIn("I do not know this phone yet", said[0])

    def test_a_code_from_a_stranger_still_links_the_phone_before_any_agent(self):
        self.bind(self.agent("Front door"), service=True)
        code = self.env["nudge.messaging.code"]._make(self.raghad)
        state, _said = self.receive(code, sender=STRANGER)
        self.assertEqual(state, "linked")
        self.assertEqual(self.asked, [])

    def test_several_agents_go_through_the_front_desk(self):
        self.link(self.raghad)
        sales = self.agent("Sales", [self.raghad])
        hr = self.agent("HR", [self.raghad])
        self.bind(sales)
        hr_binding = self.bind(hr)
        picked = []

        def front_desk(agent, text, bindings):
            picked.append((text, bindings.ids))
            return hr_binding

        patcher = patch.object(type(self.env["nudge.agent"]), "_front_desk", front_desk, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        _state, said = self.receive("my leave balance")
        self.assertEqual(said, ["AGENT HR: my leave balance"])
        self.assertEqual(len(picked), 1)
        self.assertEqual(len(picked[0][1]), 2)

    def test_the_front_desk_is_not_asked_for_one_agent(self):
        self.link(self.raghad)
        self.bind(self.agent("Sales", [self.raghad]))

        def front_desk(agent, text, bindings):
            raise AssertionError("one binding needs no front desk")

        patcher = patch.object(type(self.env["nudge.agent"]), "_front_desk", front_desk, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertEqual(self.receive("hello")[0], "answered")

    def test_a_binding_of_another_channel_or_unpublished_changes_nothing(self):
        self.link(self.raghad)
        agent = self.agent("Sales", [self.raghad])
        self.bind(agent, channel="telegram")
        unpublished = self.bind(self.agent("Other", [self.raghad]))
        unpublished.published = False
        draft = self.agent("Draft", [self.raghad], state="draft")
        self.bind(draft)
        # whatsapp has no published binding of a published agent: the channel answers as before (no agent run)
        self.receive("hello")
        self.assertEqual(self.asked, [])

    def test_without_any_binding_the_old_path_answers(self):
        self.link(self.raghad)
        # The database may hold overdue invoices of its own (demo data): the count is theirs and these three
        before = self.env["account.move"].with_user(self.raghad).search_count([
            ("move_type", "=", "out_invoice"), ("state", "=", "posted"),
            ("payment_state", "in", ("not_paid", "partial")), ("invoice_date_due", "<", fields.Date.today())])
        self.overdue_invoices(3)
        state, said = self.receive("how many overdue invoices")
        self.assertEqual(state, "answered")
        self.assertEqual(self.asked, [])
        self.assertIn(str(before + 3), said[0])

    def test_the_tools_messaging_calls_run_in_the_scope_of_the_agent(self):
        self.link(self.raghad)
        agent = self.agent("Sales", [self.raghad])
        self.bind(agent)
        conversation = self.env["nudge.conversation"].sudo().create({"user_id": self.raghad.id})
        seen = []
        conversation_class = type(conversation)

        def scope(record, mode=None):
            seen.append((record.id, mode))
            return record.with_context(nudge_agent_tools=("search_records",))

        self.patch(conversation_class, "_agent_scope", scope)
        run = self.env["nudge.run"].sudo().create({
            "name": "t", "conversation_id": conversation.id, "user_id": self.raghad.id,
            "company_id": self.company.id, "state": "running", "origin": "WhatsApp"})
        messaging = self.env["nudge.messaging"].with_user(self.raghad)
        plain = messaging._tool_env(run, "agent")
        self.assertEqual(seen, [])
        self.assertIsNone(plain.context.get("nudge_agent_tools"))
        scoped = messaging.with_context(nudge_messaging_agent_conversation=conversation.id)._tool_env(run, "agent")
        self.assertEqual(seen, [(conversation.id, "agent")])
        self.assertEqual(scoped.context["nudge_agent_tools"], ("search_records",))
        self.assertTrue(scoped.context["nudge_messaging"])
        self.assertEqual(scoped.uid, self.raghad.id, "Still the person, never another user")

    def plain_wire(self, answer, channel, linked):
        """:return: the texts of the HTTP calls made while ``answer`` came back from the agent"""
        calls = []

        class Done:
            status_code = 200

        def post(url, **kwargs):
            body = kwargs["json"]
            calls.append(body.get("text") if channel == "telegram" else body["text"]["body"])
            return Done()

        api_class = type(self.env["nudge.messaging.api"])
        self.patch(api_class, "_send_text", nudge_messaging_api.NudgeMessagingApi.__dict__["_send_text"])
        self.patch(pinned_http, "post", post)
        agent_class = type(self.env["nudge.agent"])
        result = {"text": answer, "source": "ai", "tool_calls": [], "conversation_id": False, "error": False}
        self.patch(agent_class, "_answer", lambda *args, **kwargs: dict(result))
        patcher = patch.object(agent_class, "_answer_public", lambda *args, **kwargs: dict(result), create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        wired = self.__dict__.setdefault("wired", set())
        if (channel, linked) not in wired:
            wired.add((channel, linked))
            agent = self.agent("Front door %s %s" % (channel, linked), [self.raghad])
            if linked:
                self.link(self.raghad, channel=channel, sender="555")
                self.bind(agent, channel=channel)
            else:
                self.bind(agent, channel=channel, service=True)
        self.number += 1
        self.env["nudge.messaging"]._receive(
            channel, {"id": "plain-%s" % self.number, "sender": "555" if linked else STRANGER, "kind": "text",
                      "text": "when are you open", "media": {}})
        self.env["nudge.messaging"]._cron_answer()
        return calls

    def test_markdown_never_reaches_the_phone_on_either_channel_for_linked_or_public(self):
        english = "We're open **Sunday to Thursday, 08:00 to 18:00**"
        arabic = "نحن نعمل **من الأحد إلى الخميس، من 08:00 إلى 18:00** و*مساء*"
        for channel in ("telegram", "whatsapp"):
            for linked in (True, False):
                for answer, plain in ((english, "We're open Sunday to Thursday, 08:00 to 18:00"),
                                      (arabic, "نحن نعمل من الأحد إلى الخميس، من 08:00 إلى 18:00 ومساء")):
                    with self.subTest(channel=channel, linked=linked, answer=answer[:12]):
                        self.sent.clear()
                        calls = self.plain_wire(answer, channel, linked)
                        self.assertEqual(calls[-1:], [plain])

    def test_plain_text_keeps_words_products_and_lone_asterisks(self):
        plain = nudge_messaging_api.plain_text
        for text in ("2*3 = 6", "2*3*4", "a * b", "price*", "snake_case_name", "**unclosed", "5 * 3 * 2",
                     "Total: 1,200 AED (VAT 5%)", "نص عادي بدون علامات", "10:30 - 12:00"):
            with self.subTest(text=text):
                self.assertEqual(plain(text), text)

    def test_plain_text_drops_the_marks_of_markdown(self):
        plain = nudge_messaging_api.plain_text
        for text, expected in (
                ("# Hours\n* Sunday\n- Monday\n+ Tuesday", "Hours\n- Sunday\n- Monday\n- Tuesday"),
                ("*italic* and __bold__ and _soft_", "italic and bold and soft"),
                ("See [the page](https://x.io/a) or [https://y.io](https://y.io)", "See the page (https://x.io/a) or https://y.io"),
                ("`code` and ```py\nx = 1\n```", "code and x = 1"),
                ("**أهلاً** بك في *الشركة*", "أهلاً بك في الشركة")):
            with self.subTest(text=text):
                self.assertEqual(plain(text), expected)
