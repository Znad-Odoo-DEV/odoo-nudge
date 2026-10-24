from odoo import fields
from odoo.tests import HttpCase, tagged

from odoo.addons.nudge.llm import base as llm
from odoo.addons.nudge.models.nudge_slot import NudgeBusy

from .test_messaging import RAGHAD_PHONE, MessagingCase

AI_QUESTION = "who is Raghad Contracting, tell me everything about them"
PREPARE_QUESTION = "add a new contact called Ebrahim New Contact with a long description of it"


@tagged("post_install", "-at_install", "nudge_messaging")
class TestMessagingLine(MessagingCase, HttpCase):
    """The answers of the messaging apps take their places in the line of the panel (NP-04)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("nudge.max_streams", "1")
        cls.env["ir.config_parameter"].sudo().set_param("nudge.default_provider_id", str(cls.provider.id))

    def setUp(self):
        super().setUp()
        self.link(self.raghad)
        self.Slot = self.env["nudge.slot"].sudo()

    def receive(self, text=AI_QUESTION, message_id=None):
        message_id = message_id or f"line-{len(self.sent)}-{fields.Datetime.now()}"
        self.env["nudge.messaging"]._receive("whatsapp", {
            "id": message_id, "sender": RAGHAD_PHONE, "kind": "text", "text": text, "media": {}})
        self.answer_the_queue()
        return self.env["nudge.messaging.message"].sudo().search([("ext_id", "=", message_id)])

    def places(self, *states):
        return self.Slot.search([("state", "in", states or ("claimed", "streaming"))])

    def hold(self, user):
        return self.env["nudge.conversation"].sudo().create({
            "user_id": user.id, "name": "Busy", "stream_started_at": fields.Datetime.now(),
            "provider_id": self.provider.id})

    def marked(self):
        return self.env["nudge.conversation"].sudo().with_context(active_test=False).search(
            [("user_id", "=", self.raghad.id), ("stream_started_at", "!=", False)])

    def test_an_answer_claims_a_place_and_gives_it_back(self):
        seen = []
        adapter = self.use_adapter(reply="Done.")
        original = adapter.stream_chat

        def stream_chat(*args, **kwargs):
            seen.append(self.places().mapped("state"))
            return original(*args, **kwargs)
        adapter.stream_chat = stream_chat
        line = self.receive()
        self.assertEqual(line.state, "answered")
        self.assertEqual(seen, [["streaming"]], "One place while the provider answers")
        self.assertFalse(self.places())
        self.assertEqual(self.Slot._taken(), 0)
        slot = self.places("done")
        self.assertEqual((slot.user_id, slot.provider_id), (self.raghad, self.provider))
        self.assertFalse(self.marked())

    def test_at_the_limit_the_message_stays_queued_and_is_answered_when_a_place_frees(self):
        holder = self.hold(self.ebrahim)
        adapter = self.use_adapter(reply="Done.")
        line = self.receive()
        self.assertEqual((line.state, bool(line.payload), line.tries), ("received", True, 0),
                         "Not answered, not failed, not a try used")
        self.assertFalse(self.sent, "The person is told nothing: the answer comes")
        self.assertFalse(adapter.calls, "The provider was not asked")
        self.assertFalse(self.places())
        self.assertTrue(self.env["ir.cron.trigger"].sudo().search([
            ("cron_id", "=", self.env.ref("nudge_messaging.cron_answer").id)]), "The cron comes back")
        # Still full: the message waits on, however often the cron runs
        self.answer_the_queue()
        self.assertEqual(line.state, "received")
        holder.stream_started_at = False
        line.started_at = fields.Datetime.subtract(fields.Datetime.now(), minutes=3)
        self.env.flush_all()
        self.answer_the_queue()
        self.assertEqual((line.state, line.payload), ("answered", False))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(adapter.calls), 1)

    def test_the_place_is_given_back_when_the_answer_fails(self):
        def crash(*args, **kwargs):
            raise RuntimeError("The provider fell over")
            yield
        adapter = self.use_adapter()
        adapter.stream_chat = crash
        line = self.receive()
        self.assertEqual(line.state, "failed")
        self.assertFalse(self.places())
        self.assertEqual(self.Slot._taken(), 0)
        self.assertFalse(self.marked())
        # The place is free for the next message
        self.use_adapter(reply="Fine.")
        self.assertEqual(self.receive().state, "answered")

    def test_stop_frees_the_place_at_once(self):
        stopped = []
        adapter = self.use_adapter(reply="Done.")
        original = adapter.stream_chat

        def stop_then_answer(*args, **kwargs):
            slot = self.places()
            self.env["nudge.slot"].with_user(self.raghad)._stop(slot.mapped("conversation_ref"))
            stopped.append((slot.state, self.Slot._taken()))
            return original(*args, **kwargs)
        adapter.stream_chat = stop_then_answer
        self.assertEqual(self.receive().state, "answered")
        self.assertEqual(stopped, [("stopped", 0)])
        self.assertEqual(len(self.places("stopped")), 1)

    def test_no_place_is_held_for_a_change_waiting_for_the_yes(self):
        self.use_adapter(script=[
            [llm.tool_use_event("t1", "prepare_create", {"model": "res.partner", "values": [
                {"field": "name", "value": "Ebrahim New Contact"}]}), llm.stop_event(llm.STOP_TOOL_USE)],
            [llm.text_event("I prepared the contact."), llm.stop_event(llm.STOP_END)]])
        self.receive(PREPARE_QUESTION)
        step = self.env["nudge.step"].sudo().search([("origin", "=", "WhatsApp"), ("tool", "=", "prepare_create")])
        self.assertEqual(step.state, "pending")
        self.assertFalse(self.places(), "The change waits for the yes of the person with no place")
        self.assertEqual(self.Slot._taken(), 0)

    def test_a_message_does_not_go_before_the_questions_waiting_in_line(self):
        now = fields.Datetime.now()
        waiting = self.Slot.create({
            "user_id": self.ebrahim.id, "state": "waiting", "waiting_since": now, "seen_at": now,
            "provider_id": self.provider.id})
        conversation = self.env["nudge.messaging"].with_user(self.raghad)._conversation("whatsapp")
        try:
            # Not assertRaises: it rolls the claim back, and the turn given with it
            self.env["nudge.slot"].with_user(self.raghad)._claim_channel(conversation, self.provider)
        except NudgeBusy:
            pass
        else:
            self.fail("The free place is the first in line's: the message is told to come back")
        self.assertEqual(waiting.state, "ready", "The free place went to the first in line")
        self.assertFalse(self.places())
        # And the message itself stays queued, with the provider not asked
        waiting.state = "waiting"
        adapter = self.use_adapter(reply="Done.")
        self.assertEqual(self.receive().state, "received")
        self.assertFalse(adapter.calls)

    def test_the_limit_of_the_provider_applies(self):
        self.env["ir.config_parameter"].sudo().set_param("nudge.max_streams", "5")
        self.provider.max_parallel = 1
        self.hold(self.ebrahim)
        adapter = self.use_adapter(reply="Done.")
        line = self.receive()
        self.assertEqual(line.state, "received")
        self.assertFalse(adapter.calls)

    def test_a_message_answered_by_rules_needs_no_place(self):
        self.hold(self.ebrahim)
        self.overdue_invoices(2)
        line = self.receive("كم فاتورة متأخرة")
        self.assertEqual(line.state, "answered")
        self.assertTrue(self.sent)
