import json

from odoo.tests import HttpCase, tagged

from odoo.addons.nudge.llm import base

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestUi(NudgeCase, HttpCase):

    def test_chat_tour(self):
        self.use_adapter(reply="Open **Sales**, then press `New`.")
        self.start_tour("/web", "nudge_chat", login="nudge_a")

        conversation = self.env["nudge.conversation"].search([("user_id", "=", self.user_a.id)])
        self.assertEqual(len(conversation), 1)
        self.assertEqual(conversation.message_ids.mapped("content"), [
            "How do I create a quotation?",
            "Open **Sales**, then press `New`.",
            "Thanks!",
            # A thanks alone is answered without AI (nudge.noai._chat_thanks)
            "You are welcome.",
        ])

    def test_hidden_without_the_group(self):
        self.start_tour("/web", "nudge_hidden", login="nudge_out")

    def test_screen_tour(self):
        adapter = self.use_adapter(reply="This is the contact **Nudge Test Partner**.")
        partner = self.env["res.partner"].create({
            "name": "Nudge Test Partner",
            "email": "partner@nudge.example",
            "comment": "Pays on time",
        })
        action = self.env.ref("base.action_partner_form")
        self.start_tour(f"/web#action={action.id}&id={partner.id}&model=res.partner&view_type=form", "nudge_screen", login="nudge_c")

        self.assertEqual(len(adapter.calls), 3)
        seen = [json.loads(self._snapshot_of(call)) for call in adapter.calls[:2]]
        first = seen[0]
        self.assertEqual(first["model"], "res.partner")
        self.assertEqual(first["view_type"], "form")
        self.assertEqual(first["record"]["id"], partner.id)
        self.assertFalse(first["record"]["unsaved_changes"])
        fields = {field["name"]: field for field in first["fields"]}
        self.assertEqual(fields["name"]["value"], "Nudge Test Partner")
        self.assertEqual(fields["email"]["value"], "partner@nudge.example")
        self.assertTrue(fields["name"]["on_screen"])
        self.assertTrue(first["tabs"], "The tabs of the form are listed")

        # The second question was asked after typing in the form, before saving
        second = seen[1]
        self.assertTrue(second["record"]["unsaved_changes"])
        fields = {field["name"]: field for field in second["fields"]}
        self.assertEqual(fields["email"]["value"], "changed@nudge.example")
        self.assertEqual(partner.email, "partner@nudge.example", "Nothing was saved")

        # The third one was asked with the eye closed
        self.assertNotIn("screen_snapshot", adapter.calls[2]["turns"][-1]["context"])
        messages = self.env["nudge.message"].search(
            [("user_id", "=", self.user_contacts.id), ("role", "=", "user")], order="id")
        self.assertEqual([bool(message.sudo().snapshot) for message in messages], [True, True, False])

    def test_guidance_tour(self):
        steps = {"steps": [
            {"target_type": "field", "target_name": "email", "text": "Type the new address here."},
            {"target_type": "control", "target_name": "save", "text": "Then save."},
        ]}
        self.use_adapter(script=[[
            base.tool_use_event("call_1", "show_on_screen", steps),
            base.stop_event(base.STOP_TOOL_USE),
        ]])
        partner = self.env["res.partner"].create(
            {"name": "Nudge Guided Partner", "email": "before@nudge.example"})
        action = self.env.ref("base.action_partner_form")
        self.start_tour(f"/web#action={action.id}&id={partner.id}&model=res.partner&view_type=form", "nudge_guidance", login="nudge_c")

        self.assertEqual(partner.email, "guided@nudge.example", "The person did it, guided")
        self.assertEqual(partner.write_uid, self.user_contacts)

    def open_partner(self, tour, **values):
        partner = self.env["res.partner"].create({"name": "Nudge Voice Partner", **values})
        action = self.env.ref("base.action_partner_form")
        self.start_tour(f"/web#action={action.id}&id={partner.id}&model=res.partner&view_type=form", tour, login="nudge_c")
        return partner

    def test_voice_tour(self):
        adapter = self.use_adapter(reply="Type it, then press **Save**.")
        self.open_partner("nudge_voice", email="partner@nudge.example")

        question = self.env["nudge.message"].search(
            [("user_id", "=", self.user_contacts.id), ("role", "=", "user")])
        self.assertEqual(question.content, "How do I change the email?")
        self.assertTrue(question.is_spoken)
        self.assertEqual(question.sudo().snapshot["model"], "res.partner")
        context = adapter.calls[0]["turns"][-1]["context"]
        self.assertIn('"input":"voice"', context)
        self.assertNotIn("read aloud", context)
        self.assertEqual(adapter.recordings, [], "The browser listened: no sound reached the server")

    def test_dictation_tour(self):
        adapter = self.use_adapter()
        partner = self.open_partner("nudge_dictation")

        self.assertEqual(partner.email, "voice@nudge.example")
        self.assertEqual(partner.write_uid, self.user_contacts, "Saved by the person, as themselves")
        self.assertEqual(adapter.calls, [], "A dictation asks nothing of the model")
        self.assertFalse(self.env["nudge.message"].search([("user_id", "=", self.user_contacts.id)]))

    def test_write_tour(self):
        written = base.tool_use_event("call_1", "write_in_field", {
            "field": "street", "text": "12 Main Street", "mode": "replace"})
        adapter = self.use_adapter(script=[
            [written, base.stop_event(base.STOP_TOOL_USE)],
            [dict(written, id="call_2"), base.stop_event(base.STOP_TOOL_USE)],
        ])
        partner = self.open_partner("nudge_write", street="Old Road")

        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(partner.street, "12 Main Street")
        self.assertEqual(partner.write_uid, self.user_contacts, "Saved by the person, as themselves")
        answers = self.env["nudge.message"].search(
            [("user_id", "=", self.user_contacts.id), ("role", "=", "assistant")], order="id")
        self.assertEqual(answers.mapped("state"), ["complete", "complete"])
        self.assertEqual(answers[0].screen_events[0]["type"], "write")

    def test_agent_tour(self):
        partner = self.env["res.partner"].create({"name": "Nudge Agent Partner", "function": "Buyer"})

        def change(position, call_id):
            return [
                base.text_event(f"I am about to make them a {position}."),
                base.tool_use_event(call_id, "prepare_write", {
                    "model": "res.partner", "ids": [partner.id],
                    "values": [{"field": "function", "value": position}],
                }),
                base.stop_event(base.STOP_TOOL_USE),
            ]

        def say(text):
            return [base.text_event(text), base.stop_event(base.STOP_END)]

        adapter = self.use_adapter(script=[
            change("Director", "call_1"),
            say("Nudge Agent Partner is now a Director."),
            change("President", "call_2"),
            say("All right, I leave it."),
        ])
        action = self.env.ref("base.action_partner_form")
        self.start_tour(f"/web#action={action.id}&id={partner.id}&model=res.partner&view_type=form", "nudge_agent", login="nudge_g")

        self.assertEqual(partner.function, "Director")
        self.assertEqual(partner.write_uid, self.agent, "Changed by the person, not by Nudge")
        self.assertEqual(len(adapter.calls), 4)
        runs = self.env["nudge.run"].search([("user_id", "=", self.agent.id)], order="id")
        self.assertEqual(runs.mapped("state"), ["done", "done"])
        self.assertEqual(runs.step_ids.mapped("state"), ["done", "declined"])
        self.assertEqual(runs.mapped("change_count"), [1, 0])
        self.assertEqual(runs.step_ids.mapped("res_ids"), [[partner.id], [partner.id]])
        self.assertIn("said no", adapter.calls[3]["turns"][-1]["results"][0]["content"])
        settings = self.env["res.users.settings"]._find_or_create_for_user(self.agent)
        self.assertEqual(settings.nudge_mode, "agent", "The choice of the person is kept")

    def _snapshot_of(self, call):
        context = call["turns"][-1]["context"]
        start = context.index("<screen_snapshot>") + len("<screen_snapshot>")
        return context[start:context.index("</screen_snapshot>")].replace("<\\/", "</")
