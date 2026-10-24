import json
import threading
import time
from datetime import timedelta
from unittest import mock

import psycopg2.errors

from odoo import fields, http
from odoo.exceptions import AccessError, UserError
from odoo.sql_db import db_connect
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.nudge.llm import base, loop
from odoo.addons.nudge.models import nudge_conversation, nudge_slot
from odoo.addons.nudge.models.nudge_provider import DEFAULT_PROVIDER_PARAM
from odoo.addons.nudge.models.nudge_slot import NudgeInLine

from .common import NudgeCase


class LineCase(NudgeCase):
    """Questions past the cap wait in line (NP-04); the places are claimed atomically (PERF-04)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Parameter = cls.env["ir.config_parameter"].sudo()
        cls.Parameter.set_param(nudge_conversation.MAX_STREAMS_PARAM, "1")
        cls.Parameter.set_param(DEFAULT_PROVIDER_PARAM, str(cls.provider.id))
        cls.Slot = cls.env["nudge.slot"].sudo()

    def busy(self, user, provider=None):
        """:return: a conversation of ``user`` that is answering: it holds a place"""
        return self.env["nudge.conversation"].sudo().create({
            "user_id": user.id, "name": "Answering", "stream_started_at": fields.Datetime.now(),
            "provider_id": (provider or self.provider).id})

    def take(self, user, text="Hello", slot_id=None):
        """Ask as the controller does, up to the stream.
        :return: (conversation, the job, or where the question stands in line)"""
        conversation = self.env["nudge.conversation"].with_user(user)._chat_open()
        try:
            job = conversation._chat_prepare(text, slot_id=slot_id)
        except NudgeInLine as waiting:
            return conversation, self.env["nudge.slot"].with_user(user)._wait(waiting.line)
        return conversation, job

    def status(self, user, place):
        return self.env["nudge.slot"].with_user(user).browse(place["slot_id"]).wait_status()


@tagged("post_install", "-at_install")
class TestWaitingRoom(LineCase, TransactionCase):

    def test_past_the_cap_the_question_waits_and_nothing_of_it_is_kept(self):
        self.busy(self.user_a)
        _conversation, place = self.take(self.user_b, "Where are my quotations?")
        self.assertEqual(set(place), {"slot_id", "state", "position", "seconds"},
                         "Only where the question stands: nothing of the others")
        self.assertEqual((place["state"], place["position"]), ("waiting", 1))
        self.assertGreater(place["seconds"], 0)
        self.assertFalse(self.env["nudge.message"].sudo().search(
            [("conversation_id.user_id", "=", self.user_b.id)]), "The question is asked again with its turn")

    def test_the_line_is_served_in_order_and_a_newcomer_does_not_go_first(self):
        holder = self.busy(self.user_a)
        _c, first = self.take(self.user_b)
        _c, second = self.take(self.user_contacts)
        self.assertEqual((first["position"], second["position"]), (1, 2))
        self.assertEqual(self.status(self.user_contacts, second)["position"], 2)

        holder.stream_started_at = False
        # The free place goes to the first in line, not to who asks at this instant
        _c, newcomer = self.take(self.agent)
        self.assertEqual(newcomer["state"], "waiting")
        self.assertEqual(self.Slot.browse(first["slot_id"]).state, "ready")
        self.assertEqual(self.status(self.user_contacts, second)["position"], 1)

        # Its turn come, the first asks again with its place and is answered
        self.use_adapter(reply="Your quotations are under Sales.")
        self.Slot.browse(first["slot_id"]).waiting_since -= timedelta(seconds=5)
        conversation, job = self.take(self.user_b, "Where are my quotations?", slot_id=first["slot_id"])
        self.assertEqual(job["slot_id"], first["slot_id"])
        self.assertEqual(self.Slot.browse(first["slot_id"]).state, "streaming")
        job["received"] -= 1
        events = list(loop.run(job, conversation._chat_record))
        self.assertEqual(events[-1]["state"], "complete")
        # The latency panel reads how long it waited, and how long the first words took
        answer = self.env["nudge.message"].sudo().browse(events[-1]["message_id"])
        self.assertGreaterEqual(answer.queue_wait_ms, 5000)
        self.assertGreaterEqual(answer.first_line_ms, 1000)
        # The answer over, its place goes to the next
        self.Slot._finish(job["slot_id"])
        self.assertEqual(self.Slot.browse(first["slot_id"]).state, "done")
        self.assertEqual(self.Slot.browse(second["slot_id"]).state, "ready")
        self.assertEqual(self.Slot.browse(newcomer["slot_id"]).state, "waiting")

    def test_a_place_is_given_up_with_cancel(self):
        self.busy(self.user_a)
        _c, first = self.take(self.user_b)
        _c, second = self.take(self.user_contacts)
        self.env["nudge.slot"].with_user(self.user_b).browse(first["slot_id"]).cancel_wait()
        self.assertEqual(self.Slot.browse(first["slot_id"]).state, "cancelled")
        self.assertEqual(self.status(self.user_contacts, second)["position"], 1)

    def test_a_wait_too_long_ends_and_a_browser_gone_leaves_the_line(self):
        holder = self.busy(self.user_a)
        _c, first = self.take(self.user_b)
        _c, second = self.take(self.user_contacts)
        _c, third = self.take(self.agent)
        now = fields.Datetime.now()
        self.Slot.browse(first["slot_id"]).waiting_since = now - timedelta(seconds=nudge_slot.WAIT_SECONDS + 1)
        self.Slot.browse(second["slot_id"]).seen_at = now - timedelta(seconds=nudge_slot.HEARTBEAT_SECONDS + 1)
        self.assertEqual(self.status(self.user_b, first)["state"], "expired", "Offered to try again")
        self.assertEqual(self.Slot.browse(second["slot_id"]).state, "cancelled")
        self.assertEqual(self.status(self.agent, third)["position"], 1)
        # A turn that is not taken passes to the next
        holder.stream_started_at = False
        self.assertEqual(self.status(self.agent, third)["state"], "ready")
        self.Slot.browse(third["slot_id"]).ready_at = now - timedelta(seconds=nudge_slot.READY_SECONDS + 1)
        _c, job = self.take(self.manager)
        self.assertEqual(self.Slot.browse(third["slot_id"]).state, "expired")
        self.assertEqual(self.Slot.browse(job["slot_id"]).state, "streaming", "Nobody waits: the next is answered")

    def test_one_place_in_line_per_person_and_the_line_is_capped(self):
        self.busy(self.user_a)
        _c, first = self.take(self.user_b, "Hello")
        _c, again = self.take(self.user_b, "Hello again")
        self.assertEqual(again["slot_id"], first["slot_id"], "Asked again, the question keeps its place")
        self.assertEqual(self.Slot.search_count([("user_id", "=", self.user_b.id)]), 1)
        self.Parameter.set_param(nudge_slot.LINE_PARAM, "1")
        with self.assertRaisesRegex(UserError, "Try again in a moment") as refused:
            self.take(self.user_contacts)
        self.assertNotIsInstance(refused.exception, NudgeInLine)

    def test_a_person_sees_and_cancels_only_their_own_place(self):
        self.busy(self.user_a)
        _c, place = self.take(self.user_b)
        theirs = self.env["nudge.slot"].with_user(self.user_contacts).browse(place["slot_id"])
        with self.assertRaises(AccessError):
            theirs.wait_status()
        with self.assertRaises(AccessError):
            theirs.cancel_wait()
        self.assertEqual(self.Slot.browse(place["slot_id"]).state, "waiting")
        with self.assertRaises(AccessError):
            self.env["nudge.slot"].with_user(self.user_contacts).search([])

    def test_two_people_cannot_take_the_last_place(self):
        Slot = self.env["nudge.slot"]
        conversation_a = self.env["nudge.conversation"].with_user(self.user_a)._chat_open()
        conversation_b = self.env["nudge.conversation"].with_user(self.user_b)._chat_open()
        # The first claims the last place: committed at once, before its question is
        claimed = Slot.with_user(self.user_a)._claim(conversation_a, self.provider)
        self.assertEqual(claimed.state, "claimed")
        with self.assertRaises(NudgeInLine):
            Slot.with_user(self.user_b)._claim(conversation_b, self.provider)
        # Nor one person two answers, from two tabs
        with self.assertRaisesRegex(UserError, "still answering"):
            Slot.with_user(self.user_a)._claim(conversation_a, self.provider)

    def test_claims_are_made_one_at_a_time(self):
        # Another worker is claiming: this one waits for it, for a bounded time, rather than count
        # the places at the same instant
        self.patch(nudge_slot, "LOCK_TIMEOUT", "200ms")
        conversation = self.env["nudge.conversation"].with_user(self.user_a)._chat_open()
        with db_connect(self.env.cr.dbname).cursor() as other:
            other.execute("SELECT pg_advisory_lock(hashtext(%s))", [nudge_slot.LOCK_KEY])
            try:
                with self.assertRaises(psycopg2.errors.LockNotAvailable):
                    self.env["nudge.slot"].with_user(self.user_a)._claim(conversation, self.provider)
            finally:
                other.execute("SELECT pg_advisory_unlock_all()")
        self.assertEqual(self.env["nudge.slot"].with_user(self.user_a)._claim(conversation, self.provider).state,
                         "claimed")

    def test_what_is_counted_under_the_lock_is_what_its_last_holder_committed(self):
        # Review odoo-tech #2: the snapshot of a transaction in repeatable read is taken by its first
        # statement, the one that waits for the lock: the places the holder committed meanwhile
        # were not counted, and two questions took the last place. A place committed by another
        # worker after the lock is taken stands for one committed before the wait ended.
        dbname = self.env.cr.dbname
        admin = self.env.ref("base.user_admin")

        def forget(slot_id):
            with db_connect(dbname).cursor() as cr:
                cr.execute("DELETE FROM nudge_slot WHERE id = %s", [slot_id])

        with mock.patch.object(nudge_slot.odoo_module, "current_test", None), self.Slot._own() as Slot:
            Slot._lock()
            claimed = [("state", "=", "claimed")]
            before = Slot.search_count(claimed)
            with db_connect(dbname).cursor() as other:
                other.execute(
                    "INSERT INTO nudge_slot (user_id, state, started_at) VALUES (%s, 'claimed', %s) RETURNING id",
                    [admin.id, fields.Datetime.now()])
                self.addCleanup(forget, other.fetchone()[0])
            self.assertEqual(Slot.search_count(claimed), before + 1)
            Slot.env.cr.execute("SHOW transaction_isolation")
            self.assertEqual(Slot.env.cr.fetchone()[0], "read committed")

    def test_a_provider_takes_only_so_many_at_once(self):
        self.Parameter.set_param(nudge_conversation.MAX_STREAMS_PARAM, "8")
        ours = self.provider.copy({"name": "Our Ollama", "base_url": "http://localhost:11434/v1",
                                   "requires_api_key": False, "runs_locally": True})
        self.assertEqual(ours._get_max_parallel(), 1, "A model on one machine answers one at a time")
        self.assertEqual(self.provider._get_max_parallel(), 0)
        self.busy(self.user_a, provider=ours)
        Slot = self.env["nudge.slot"]
        conversation_b = self.env["nudge.conversation"].with_user(self.user_b)._chat_open()
        conversation_c = self.env["nudge.conversation"].with_user(self.user_contacts)._chat_open()
        with self.assertRaises(NudgeInLine) as waiting:
            Slot.with_user(self.user_b)._claim(conversation_b, ours)
        self.env["nudge.slot"].with_user(self.user_b)._wait(waiting.exception.line)
        # Behind it, a question for another provider goes first: there is room for it
        self.assertEqual(Slot.with_user(self.user_contacts)._claim(conversation_c, self.provider).state, "claimed")
        ours.max_parallel = 2
        self.assertEqual(ours._get_max_parallel(), 2)

    def test_a_failure_after_the_claim_gives_the_place_back(self):
        conversation = self.env["nudge.conversation"].with_user(self.user_a)._chat_open()
        slot = self.env["nudge.slot"].with_user(self.user_a)._claim(conversation, self.provider)
        self.Slot._release(slot.id)
        self.assertEqual(slot.state, "cancelled")
        self.assertFalse(self.Slot._is_answering(self.user_a))


@tagged("post_install", "-at_install")
class TestStop(LineCase, TransactionCase):
    """PERF-02: Stop lets the worker go and frees the place at once, even from a silent provider."""

    def silent(self):
        """:return: (events of a provider that says nothing until told, the event that tells it)"""
        release = threading.Event()
        self.addCleanup(release.set)

        def events():
            release.wait(30)
            yield base.stop_event(base.STOP_END, usage={"input_tokens": 1, "output_tokens": 0})
        return events(), release

    def test_stop_frees_the_person_and_the_next_in_line_at_once(self):
        self.use_adapter()
        conversation, job = self.take(self.user_a, "Summarise the open quotations")
        _c, place = self.take(self.user_b)
        self.assertEqual(place["state"], "waiting")
        conversation.action_stop_answer()
        self.assertFalse(conversation.sudo().stream_started_at)
        self.assertTrue(conversation._chat_stopped(job["serial"]))
        self.assertEqual(self.Slot.browse(job["slot_id"]).state, "stopped")
        self.assertEqual(self.Slot.browse(place["slot_id"]).state, "ready", "The next in line is told at once")

    def test_stop_during_a_silent_provider(self):
        self.Parameter.set_param(nudge_conversation.MAX_STREAMS_PARAM, "8")
        events, _release = self.silent()
        self.use_adapter(events=events)
        conversation, job = self.take(self.user_a, "Summarise the open quotations")
        stopped_at = []

        def cancelled():
            if not stopped_at:
                # Pressed while the provider says nothing, through another worker
                conversation.action_stop_answer()
                stopped_at.append(time.monotonic())
            return conversation._chat_stopped(job["serial"])
        job["cancelled"] = cancelled
        started = time.monotonic()
        answer = list(loop.run(job, conversation._chat_record))
        self.assertLess(time.monotonic() - started, 3, "The worker is let go within a second or two")
        self.assertEqual(answer[-1]["type"], "done")
        self.assertEqual(answer[-1]["state"], "aborted")
        message = self.env["nudge.message"].sudo().browse(answer[-1]["message_id"])
        self.assertEqual(message.state, "aborted")
        self.assertFalse(conversation.sudo().stream_started_at)
        self.assertEqual(self.Slot.browse(job["slot_id"]).state, "stopped")
        # The person asks again at once
        self.use_adapter(reply="Here they are.")
        _same, follow = self.take(self.user_a, "Then list them")
        self.assertTrue(follow["slot_id"])

    def test_stopped_while_the_model_wrote_its_calls_none_is_run(self):
        self.use_adapter(script=[[
            base.tool_use_event("call_1", "search_records", {"model": "res.partner"}),
            base.stop_event(base.STOP_TOOL_USE),
        ]])
        conversation, job = self.take(self.user_a, "Find the contacts")
        job["cancelled"] = lambda: True
        answer = list(loop.run(job, conversation._chat_record))
        self.assertEqual(answer[-1]["state"], "aborted")
        self.assertFalse(self.env["nudge.message"].sudo().search(
            [("conversation_id", "=", conversation.id), ("role", "=", "tool")]), "No tool ran")

    def test_a_stopped_stream_leaves_the_next_answer_alone(self):
        self.Parameter.set_param(nudge_conversation.MAX_STREAMS_PARAM, "8")
        self.use_adapter()
        conversation, first = self.take(self.user_a, "First")
        conversation.action_stop_answer()
        second = conversation._chat_prepare("Second")
        before = self.env["nudge.message"].sudo().search_count([("conversation_id", "=", conversation.id)])
        late = conversation._chat_record({"text": "Partial", "tool_calls": [], "aborted": True,
                                          "serial": first["serial"]})
        self.assertEqual(late["event"]["state"], "aborted")
        self.assertEqual(self.env["nudge.message"].sudo().search_count(
            [("conversation_id", "=", conversation.id)]), before, "Nothing written over the new answer")
        self.assertTrue(conversation.sudo().stream_started_at, "The new answer keeps its mark")
        self.assertGreater(second["serial"], first["serial"])

    def test_a_silent_provider_is_not_waited_for_past_the_deadline(self):
        self.patch(loop, "STOP_CHECK_SECONDS", 0.2)
        self.patch(loop, "DEADLINE_GRACE_SECONDS", 0)
        events, _release = self.silent()
        self.use_adapter(events=events)
        conversation, job = self.take(self.user_a, "Hello")
        job.update(seconds=0.4, cancelled=lambda: False)
        started = time.monotonic()
        answer = list(loop.run(job, conversation._chat_record))
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(answer[-1]["type"], "error")
        message = self.env["nudge.message"].sudo().browse(answer[-1]["message_id"])
        self.assertEqual(message.error_code, base.ERROR_TIMEOUT)

    def test_only_the_person_stops_their_answer(self):
        self.use_adapter()
        conversation, _job = self.take(self.user_a, "Hello")
        with self.assertRaises(AccessError):
            conversation.with_user(self.user_b).action_stop_answer()
        self.assertTrue(conversation.sudo().stream_started_at)


@tagged("post_install", "-at_install")
class TestWaitingRoomHttp(LineCase, HttpCase):

    def post(self, login, message="Hello", slot=None):
        self.env.flush_all()
        self.authenticate(login, login)
        data = {"message": message, "csrf_token": http.Request.csrf_token(self)}
        if slot:
            data["slot"] = slot
        response = self.url_open("/nudge/chat/stream", data=data, timeout=30)
        self.assertEqual(response.status_code, 200, response.text[:300])
        return [json.loads(line) for line in response.text.splitlines() if line]

    def test_the_question_waits_without_a_worker_then_starts_with_its_place(self):
        self.use_adapter(reply="Under Sales.")
        holder = self.busy(self.user_b)
        events = self.post("nudge_a", "Where are quotations?")
        self.assertEqual([event["type"] for event in events], ["in_line"], "Answered at once, nothing streams")
        self.assertEqual(events[0]["position"], 1)
        self.assertFalse(self.env["nudge.message"].sudo().search(
            [("conversation_id.user_id", "=", self.user_a.id)]))
        slot = self.env["nudge.slot"].sudo().browse(events[0]["slot_id"])
        self.assertEqual(slot.state, "waiting")

        holder.stream_started_at = False
        events = self.post("nudge_a", "Where are quotations?", slot=slot.id)
        self.assertEqual([event["type"] for event in events][0], "start")
        self.assertEqual(events[-1]["state"], "complete")
        slot.invalidate_recordset()
        self.assertEqual(slot.state, "done", "The answer over, its place is free for the next")
