import json
import logging

from odoo import http
from odoo.tests import HttpCase, tagged

from odoo.addons.nudge.llm import base

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestChatStream(NudgeCase, HttpCase):

    def post(self, login, message="Hello", conversation_id=None, csrf=True):
        self.authenticate(login, login)
        data = {"message": message}
        if csrf:
            data["csrf_token"] = http.Request.csrf_token(self)
        if conversation_id:
            data["conversation_id"] = conversation_id
        return self.url_open("/nudge/chat/stream", data=data, timeout=30)

    def events(self, response):
        self.assertEqual(response.status_code, 200, response.text[:300])
        self.assertTrue(response.headers["Content-Type"].startswith("application/x-ndjson"))
        return [json.loads(line) for line in response.text.splitlines() if line]

    def test_stream(self):
        self.use_adapter(reply="Go to **Sales** then مرحبا")
        response = self.post("nudge_a", "Where are quotations?")
        events = self.events(response)

        self.assertEqual(response.headers["X-Accel-Buffering"], "no")
        self.assertNotIn("Content-Length", response.headers, "The answer is sent as it comes")
        self.assertEqual(
            [event["type"] for event in events],
            ["start", "delta", "delta", "delta", "delta", "delta", "done"],
        )
        self.assertEqual("".join(e["text"] for e in events[1:-1]), "Go to **Sales** then مرحبا")
        self.assertEqual(events[0]["title"], "Where are quotations?")
        self.assertEqual(events[-1]["state"], "complete")

        conversation = self.env["nudge.conversation"].browse(events[0]["conversation_id"])
        self.assertEqual(conversation.user_id, self.user_a)
        self.assertEqual(conversation.message_ids.mapped("role"), ["user", "assistant"])
        self.assertEqual(conversation.message_ids[1].id, events[-1]["message_id"])
        self.assertEqual(conversation.message_ids[1].content, "Go to **Sales** then مرحبا")
        self.assertFalse(conversation.stream_started_at)

    def test_conversation_continues(self):
        adapter = self.use_adapter(reply="Answer")
        first = self.events(self.post("nudge_a", "First"))
        conversation_id = first[0]["conversation_id"]
        second = self.events(self.post("nudge_a", "Second", conversation_id=conversation_id))

        self.assertEqual(second[0]["conversation_id"], conversation_id)
        self.assertEqual(
            [turn["text"] for turn in adapter.calls[1]["turns"]], ["First", "Answer", "Second"])

    def test_provider_error(self):
        self.use_adapter(events=[
            base.text_event("Partial"),
            base.error_event(base.ERROR_UNAVAILABLE, "upstream overloaded"),
        ])
        events = self.events(self.post("nudge_a"))

        self.assertEqual([event["type"] for event in events], ["start", "delta", "error"])
        self.assertTrue(events[-1]["message"])
        self.assertNotIn("detail", events[-1])
        message = self.env["nudge.message"].browse(events[-1]["message_id"])
        self.assertEqual(message.state, "error")
        self.assertEqual(message.content, "Partial")

    def test_adapter_crash(self):
        def crash():
            yield base.text_event("Partial")
            raise RuntimeError("boom")

        self.use_adapter(events=crash())
        with self.assertLogs("odoo.addons.nudge.llm.loop", logging.ERROR):
            events = self.events(self.post("nudge_a"))
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(self.env["nudge.message"].browse(events[-1]["message_id"]).state, "error")

    def test_rejected_message(self):
        self.use_adapter()
        events = self.events(self.post("nudge_a", "   "))
        self.assertEqual([event["type"] for event in events], ["error"])
        self.assertEqual(events[0]["state"], "rejected")
        self.assertFalse(
            self.env["nudge.conversation"].search([("user_id", "=", self.user_a.id)]),
            "A rejected message leaves no empty conversation behind",
        )

    def test_not_configured(self):
        self.provider.active = False
        events = self.events(self.post("nudge_a"))
        self.assertEqual(events[0]["state"], "rejected")
        self.assertIn("administrator", events[0]["message"])

    def test_conversation_of_someone_else(self):
        self.use_adapter()
        conversation_id = self.events(self.post("nudge_a"))[0]["conversation_id"]
        events = self.events(self.post("nudge_b", conversation_id=conversation_id))
        self.assertEqual(events[0]["state"], "rejected")
        conversation = self.env["nudge.conversation"].browse(conversation_id)
        self.assertEqual(len(conversation.message_ids), 2)

    def test_access(self):
        self.use_adapter()
        self.assertEqual(self.post("nudge_out").status_code, 403)
        self.assertEqual(self.post("nudge_a", csrf=False).status_code, 400)
        self.logout()
        response = self.url_open("/nudge/chat/stream", data={"message": "Hello"}, allow_redirects=False)
        self.assertNotEqual(response.status_code, 200)
        users = self.user_a | self.outsider
        self.assertFalse(self.env["nudge.message"].search([("user_id", "in", users.ids)]))
        self.assertFalse(self.env["nudge.conversation"].search([("user_id", "in", users.ids)]))
