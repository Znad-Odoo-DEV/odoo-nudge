import json
import logging

from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.nudge.llm import base, loop
from odoo.addons.nudge.models import nudge_run

from .test_agent import AgentCase, asking, call, saying, values

CREATE = call("prepare_create", {
    "model": "res.partner", "values": values(name="Nudge Made", city="Hebron")}, "call_c")
SEARCH = call("search_records", {"model": "res.partner", "conditions": [
    {"field": "name", "operator": "contains", "value": "Nudge Acme"}]}, "call_s")


class BackgroundCase(AgentCase):

    def setUp(self):
        super().setUp()
        self.env.cr.precommit.run()
        self.last_bus_id = max(self.env["bus.bus"].sudo().search([], order="id desc", limit=1).ids or [0])
        # Tests run in one transaction, which they undo: nothing is committed
        self.Run = self.env["nudge.run"].with_context(nudge_keep_transaction=True)
        # The crons commit after each piece of work; a test is one transaction it undoes
        self.patch(type(self.env["ir.cron"]), "_commit_progress", lambda cron, *args, **kwargs: 10 ** 6)

    def leave(self, user, text, conversation_id=None):
        """Ask for the work to be done in the background, the way the controller does.
        :return: (conversation, events)"""
        Conversation = self.env["nudge.conversation"].with_user(user)
        conversation = Conversation._chat_open(conversation_id)
        job = conversation._chat_prepare(text, mode="agent", background=True)
        return conversation, list(loop.run(job, conversation._chat_record))

    def work(self, run):
        self.Run.browse(run.id)._work()
        run.invalidate_recordset()

    def told(self):
        """:return: what the person was told through the bus since last asked"""
        self.env.cr.precommit.run()
        rows = self.env["bus.bus"].sudo().search([("id", ">", self.last_bus_id)], order="id")
        self.last_bus_id = max(rows.ids or [self.last_bus_id])
        payloads = []
        for row in rows:
            message = json.loads(row.message)
            if message["type"] == "nudge.run":
                payloads.append((json.loads(row.channel), message["payload"]))
        return payloads

    def made(self):
        return self.env["res.partner"].search([("name", "=", "Nudge Made")])


@tagged("post_install", "-at_install")
class TestBackground(BackgroundCase, TransactionCase):

    def test_left_to_the_background(self):
        adapter = self.use_adapter(script=[asking(CREATE)])
        conversation, events = self.leave(self.agent, "Create the contact Nudge Made")

        self.assertEqual([event["type"] for event in events], ["start", "queued"])
        run = conversation.sudo().run_id
        self.assertEqual(events[1]["run_id"], run.id)
        self.assertEqual((run.state, run.is_background, run.pieces), ("queued", True, 0))
        self.assertEqual(adapter.calls, [], "Nothing is done in the request")
        self.assertFalse(conversation.stream_started_at, "The person is free to ask elsewhere")
        cron = self.env.ref("nudge.cron_agent_runs")
        self.assertTrue(self.env["ir.cron.trigger"].search([("cron_id", "=", cron.id)]),
                        "The scheduled action is asked to run now")
        self.assertEqual(conversation.message_ids.mapped("role"), ["user"])
        self.assertEqual(self.told(), [])

        # The conversation is Nudge's while it works
        with self.assertRaises(UserError):
            conversation._chat_prepare("And also this", mode="agent")
        other, _event = self.ask(self.agent, "Something else, elsewhere")
        self.assertNotEqual(other, conversation)

    def test_worked_on_as_the_person(self):
        adapter = self.use_adapter(script=[
            asking(SEARCH, text="Let me look."),
            asking(CREATE, text="I am about to create it."),
            saying("Done: Nudge Made is created."),
        ])
        conversation, _events = self.leave(self.agent, "Create the contact Nudge Made")
        run = conversation.sudo().run_id
        self.work(run)

        self.assertEqual((run.state, run.pieces, run.rounds), ("waiting", 1, 2))
        self.assertEqual(run.step_ids.mapped("state"), ["done", "pending"])
        self.assertEqual(run.step_ids[0].summary, "Looked up: Contact")
        self.assertEqual(run.progress, "Looked up: Contact")
        self.assertFalse(self.made(), "A run in the background never decides for the person")
        self.assertIn("## Mode: Agent", adapter.calls[0]["system"][-1])
        self.assertEqual(conversation.message_ids.mapped("role"), ["user", "assistant", "tool", "assistant"])
        self.assertEqual(conversation.message_ids[-1].state, "waiting")

        step = conversation.sudo().waiting_step_id
        told = self.told()
        self.assertEqual([payload["kind"] for _channel, payload in told], ["progress", "waiting"])
        # On the channel of the person: the database, then their partner
        self.assertTrue(all(channel[-2:] == ["res.partner", self.agent.partner_id.id]
                            for channel, _payload in told), told)
        progress, waiting = (payload for _channel, payload in told)
        self.assertEqual(progress["progress"], "Looked up: Contact")
        self.assertEqual(waiting["state"], "waiting")
        self.assertEqual(waiting["conversation_id"], conversation.id)
        self.assertEqual(waiting["card"]["step_id"], step.id)
        self.assertEqual(waiting["card"]["title"], "Create: Contact")
        self.assertEqual(waiting["text"], "I am about to create it.")
        self.assertTrue(run.is_unseen)

        # The yes is done in the request; what follows is left to the background again
        events = self.decide(conversation)
        self.assertEqual([event["type"] for event in events], ["start", "decided", "changed", "queued"])
        partner = self.made()
        self.assertEqual(partner.create_uid, self.agent)
        self.assertEqual(run.state, "queued")
        self.assertEqual(len(adapter.calls), 2)

        self.work(run)
        self.assertEqual((run.state, run.pieces, run.rounds, run.change_count), ("done", 2, 3, 1))
        self.assertEqual(conversation.message_ids[-1].content, "Done: Nudge Made is created.")
        told = self.told()
        self.assertEqual([payload["kind"] for _channel, payload in told], ["done"])
        self.assertEqual(told[0][1]["text"], "Done: Nudge Made is created.")
        self.assertEqual(told[0][1]["state"], "done")
        # What was done is on the screen the person has in front
        self.assertEqual(told[0][1]["changed"], [{"model": "res.partner", "ids": partner.ids}])
        self.assertFalse(conversation.sudo().run_id.state == "running")

    def test_taken_up_in_pieces(self):
        searches = [asking(call("search_records", {"model": "res.partner"}, f"call_{index}"))
                    for index in range(10)]
        adapter = self.use_adapter(script=[*searches, saying("That is all.")])
        conversation, _events = self.leave(self.agent, "Look everywhere")
        run = conversation.sudo().run_id

        self.work(run)
        self.assertEqual((run.state, run.pieces, run.rounds), ("queued", 1, 8))
        self.assertEqual(conversation.message_ids[-1].role, "tool")
        self.assertNotIn("error", conversation.message_ids.mapped("state"),
                         "Time running out is not a failure in the background")
        self.work(run)
        self.assertEqual((run.state, run.pieces, run.rounds), ("done", 2, 11))
        self.assertEqual(run.step_count, 10)
        self.assertEqual(len(adapter.calls), 11)
        self.assertEqual(conversation.message_ids[-1].content, "That is all.")

    def test_what_bounds_a_run(self):
        endless = [asking(call("search_records", {"model": "res.partner"}, f"call_{index}"))
                   for index in range(100)]
        self.use_adapter(script=endless)
        conversation, _events = self.leave(self.agent, "Look forever")
        run = conversation.sudo().run_id
        for _piece in range(nudge_run.MAX_PIECES + 1):
            self.work(run)
            if run.state != "queued":
                break
        self.assertEqual(run.state, "failed")
        self.assertLessEqual(run.pieces, nudge_run.MAX_PIECES)
        self.assertLessEqual(run.step_count, 60)
        self.assertEqual(conversation.message_ids[-1].state, "error")
        self.assertIn("Ask it to go on", conversation.message_ids[-1].error_message)
        self.assertEqual(self.told()[-1][1]["kind"], "failed")

    def test_stopped_by_the_person(self):
        self.use_adapter(script=[asking(CREATE)])
        conversation, _events = self.leave(self.agent, "Create the contact Nudge Made")
        run = conversation.sudo().run_id
        self.work(run)
        self.assertEqual(run.state, "waiting")

        with self.assertRaises(AccessError):
            run.with_user(self.user_a).action_stop()
        run.with_user(self.agent).action_stop()
        self.assertEqual(run.state, "stopped")
        self.assertEqual(run.step_ids.mapped("state"), ["cancelled"])
        self.assertFalse(conversation.sudo().waiting_step_id)
        self.assertEqual(self.told()[-1][1]["kind"], "stopped")
        with self.assertRaises(UserError):
            self.decide(conversation, step=run.step_ids)
        self.assertFalse(self.made())
        # Stopped, the conversation is theirs again
        self.ask(self.agent, "What is a contact?", conversation_id=conversation.id)
        # A manager may stop what runs for somebody else
        self.use_adapter(script=[asking(CREATE)])
        conversation, _events = self.leave(self.agent, "Again")
        conversation.sudo().run_id.with_user(self.manager).action_stop()
        self.assertEqual(conversation.sudo().run_id.state, "stopped")

    def test_the_scheduled_action_takes_them_up(self):
        self.use_adapter(script=[saying("First done."), saying("Second done.")])
        first, _events = self.leave(self.agent, "First")
        second, _events = self.leave(self.agent, "Second")
        self.patch(type(self.env["ir.cron"]), "_commit_progress", lambda cron, *args, **kwargs: 10 ** 6)

        self.Run._cron_work()
        self.assertEqual(first.sudo().run_id.state, "done")
        self.assertEqual(second.sudo().run_id.state, "done")
        self.assertEqual(first.message_ids[-1].content, "First done.")
        self.assertEqual(second.message_ids[-1].content, "Second done.")

        # Short of time: one is taken up, the other waits for the next run
        self.use_adapter(script=[saying("Third done."), saying("Fourth done.")])
        third, _events = self.leave(self.agent, "Third")
        fourth, _events = self.leave(self.agent, "Fourth")
        self.patch(type(self.env["ir.cron"]), "_commit_progress", lambda cron, *args, **kwargs: 5)
        self.env["ir.cron.trigger"].search([]).unlink()
        self.Run._cron_work()
        self.assertEqual(third.sudo().run_id.state, "done")
        self.assertEqual(fourth.sudo().run_id.state, "queued")
        self.assertTrue(self.env["ir.cron.trigger"].search([]), "Asked to run again soon")

    def test_what_fails_in_the_background_is_told(self):
        def crash():
            yield base.text_event("Partial")
            raise RuntimeError("boom")

        self.use_adapter(events=crash())
        conversation, _events = self.leave(self.agent, "Create the contact Nudge Made")
        run = conversation.sudo().run_id
        with self.assertLogs("odoo.addons.nudge.llm.loop", logging.ERROR):
            self.work(run)
        self.assertEqual(run.state, "failed")
        self.assertEqual(conversation.message_ids[-1].state, "error")
        self.assertEqual(self.told()[-1][1]["kind"], "failed")

    def test_news(self):
        self.use_adapter(script=[asking(CREATE), saying("Done."), saying("Other done.")])
        conversation, _events = self.leave(self.agent, "Create the contact Nudge Made")
        run = conversation.sudo().run_id
        Run = self.env["nudge.run"].with_user(self.agent)
        self.assertEqual([news["state"] for news in Run.get_news()], ["queued"])

        self.work(run)
        news = Run.get_news()
        self.assertEqual((news[0]["state"], news[0]["is_unseen"]), ("waiting", True))
        self.assertEqual(news[0]["conversation_id"], conversation.id)
        Run.browse(run.id).action_seen()
        self.assertFalse(run.is_unseen)
        self.assertEqual([news["state"] for news in Run.get_news()], ["waiting"], "Not over: still news")

        self.decide(conversation)
        self.work(run)
        self.assertEqual((run.state, run.is_unseen), ("done", True))
        Run.browse(run.id).action_seen()
        self.assertEqual(Run.get_news(), [], "Over and seen: no news")
        # Only their own
        self.assertEqual(self.env["nudge.run"].with_user(self.manager).get_news(), [])
        self.user_a.group_ids += self.env.ref("nudge.group_nudge_agent")
        self.assertEqual(self.env["nudge.run"].with_user(self.user_a).get_news(), [])
        with self.assertRaises(AccessError):
            Run.browse(run.id).with_user(self.user_a).action_seen()

    def test_in_the_foreground_nothing_changes(self):
        self.use_adapter(script=[asking(CREATE), saying("Done.")])
        conversation, events = self.order(self.agent, "Create the contact Nudge Made")
        self.assertEqual(events[-1]["type"], "waiting")
        run = conversation.sudo().run_id
        self.assertFalse(run.is_background)
        self.assertEqual(self.told(), [], "Nobody is told what they are looking at")
        self.decide(conversation)
        self.assertEqual(run.state, "done")
        self.assertEqual(self.told(), [])
        self.assertFalse(run.is_unseen)

    def test_a_piece_lasts_less_than_a_scheduled_action_may(self):
        Run = self.env["nudge.run"]
        cases = [
            # limit of a scheduled action, limit of a request, seconds of a piece
            (-1, 120, 90),
            (-1, 100, 75),
            (60, 120, 35),
            (0, 120, 90),
            (30, 120, 20),
            (-1, 0, 90),
        ]
        for cron_limit, limit, seconds in cases:
            self.patch(nudge_run, "config", {"limit_time_real_cron": cron_limit, "limit_time_real": limit})
            self.assertEqual(Run._get_piece_seconds(), seconds, (cron_limit, limit))


@tagged("post_install", "-at_install")
class TestBackgroundOverHttp(BackgroundCase, HttpCase):

    def post(self, url, login, **data):
        self.authenticate(login, login)
        data["csrf_token"] = http.Request.csrf_token(self)
        return self.url_open(url, data=data, timeout=30)

    def events(self, response):
        self.assertEqual(response.status_code, 200, response.text[:300])
        return [json.loads(line) for line in response.text.splitlines() if line]

    def test_asked_and_answered_over_http(self):
        self.use_adapter(script=[asking(CREATE), saying("Done.")])
        events = self.events(self.post(
            "/nudge/chat/stream", "nudge_g", message="Create the contact Nudge Made",
            mode="agent", background="1"))
        self.assertEqual([event["type"] for event in events], ["start", "queued"])
        conversation = self.env["nudge.conversation"].browse(events[0]["conversation_id"])
        run = conversation.run_id
        self.assertEqual((run.state, run.is_background), ("queued", True))

        self.work(run)
        self.assertEqual(run.state, "waiting")
        events = self.events(self.post(
            "/nudge/agent/decide", "nudge_g", conversation_id=conversation.id,
            step_id=conversation.waiting_step_id.id, decision="confirm"))
        self.assertEqual([event["type"] for event in events], ["start", "decided", "changed", "queued"])
        self.assertEqual(self.made().create_uid, self.agent)
        self.assertEqual(run.state, "queued")

        # Only in Agent mode
        events = self.events(self.post(
            "/nudge/chat/stream", "nudge_g", message="Explain it", background="1"))
        self.assertEqual(events[-1]["type"], "done")
        self.assertFalse(self.env["nudge.run"].search([("user_id", "=", self.agent.id), ("name", "=", "Explain it")]))
