import json
import logging

from odoo import http
from odoo.exceptions import MissingError
from odoo.tests import BaseCase, HttpCase, TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import base, loop
from odoo.addons.nudge.llm.openai_compat_adapter import OpenAICompatAdapter
from odoo.addons.nudge.tools import schema

from .common import NudgeCase
from .test_adapters import FakeResponse, sse
from .test_snapshot import SNAPSHOT


def call(name, arguments, call_id="call_1", **extra):
    return base.tool_use_event(call_id, name, arguments, **extra)


def guide(*steps):
    return call("show_on_screen", {"steps": [
        {"target_type": kind, "target_name": name, "text": text} for kind, name, text in steps
    ]})


def chunk(text=None, finish=None, tool_calls=None):
    """One piece of a streamed answer, as an OpenAI-compatible server sends it."""
    delta = {}
    if text is not None:
        delta["content"] = text
    if tool_calls:
        delta["tool_calls"] = tool_calls
    choice = {"index": 0, "delta": delta}
    if finish:
        choice["finish_reason"] = finish
    return {"model": "served-model", "choices": [choice]}


def answer_with(*events, text=""):
    """Events of one request to the model that ends with calls to tools."""
    written = [base.text_event(text)] if text else []
    return [*written, *events, base.stop_event(base.STOP_TOOL_USE)]


@tagged("post_install", "-at_install")
class TestSchema(BaseCase):

    SCHEMA = tools.guidance.ShowOnScreen.input_schema

    def check(self, value, fragment=None):
        if fragment is None:
            return schema.validate(value, self.SCHEMA)
        with self.assertRaises(schema.SchemaError) as capture:
            schema.validate(value, self.SCHEMA)
        self.assertIn(fragment, str(capture.exception))

    def step(self, **changes):
        values = {"target_type": "button", "target_name": "action_confirm", "text": "Press it."}
        values.update(changes)
        return {key: value for key, value in values.items() if value is not None}

    def test_accepted(self):
        self.check({"steps": [self.step(), self.step(advance="manual", target_type="field")]})

    def test_refused(self):
        self.check({}, "'steps' is missing")
        self.check({"steps": []}, "at least 1")
        self.check({"steps": [self.step()] * 9}, "at most 8")
        self.check({"steps": "confirm"}, "expected array")
        self.check({"steps": [self.step(text=None)]}, "steps[0]: 'text' is missing")
        self.check({"steps": [self.step(text="")]}, "must not be empty")
        self.check({"steps": [self.step(text="x" * 401)]}, "longer than 400")
        self.check({"steps": [self.step(text=7)]}, "expected string")
        self.check({"steps": [self.step(target_type="selector")]}, "must be one of")
        self.check({"steps": [self.step(advance="later")]}, "must be one of")
        self.check({"steps": [self.step(selector=".o_form")]}, "'selector' is not an accepted argument")
        self.check({"steps": [self.step()], "css": "body"}, "'css' is not an accepted argument")

    def test_a_wrong_shape_is_shown_the_right_one(self):
        # Gemini wrote a condition as text, then gave up on conditions
        searching = tools.records.SearchRecords.input_schema
        with self.assertRaises(schema.SchemaError) as capture:
            schema.validate({"model": "sale.order", "conditions": ["state = 'sale'"]}, searching)
        told = str(capture.exception)
        self.assertIn("conditions[0]: expected object, got str, such as ", told)
        self.assertIn('{"field": "...", "operator": "=", "value": "..."}', told)
        # An argument of another tool: the accepted ones are named
        self.check({"steps": [self.step()], "css": "body"}, "accepted: steps")

    def test_a_name_is_never_a_selector(self):
        for name in ('x"] , body *', "a > b", "[name]", "name{}", "a\nb", "a'b", "x" * 121, "#id", "a:b"):
            self.check({"steps": [self.step(target_name=name)]}, "expected form")

    def test_types_are_those_of_json(self):
        number = {"type": "object", "properties": {"n": {"type": "integer", "minimum": 1}}}
        schema.validate({"n": 3}, number)
        for value in (True, 3.5, "3", None):
            with self.assertRaises(schema.SchemaError):
                schema.validate({"n": value}, number)
        with self.assertRaises(schema.SchemaError):
            schema.validate({"n": 0}, number)

    def test_a_number_written_for_text_is_forgiven(self):
        # qwen3.5:4b searched the orders of partner 9 with "value": 9, twice refused
        searching = tools.records.SearchRecords.input_schema
        arguments = {"model": "sale.order", "conditions": [{"field": "partner_id", "operator": "=", "value": 9},
                                                           {"field": "active", "operator": "=", "value": True}]}
        forgiven = schema.forgive(arguments, searching)
        self.assertEqual([condition["value"] for condition in forgiven["conditions"]], ["9", "true"])
        schema.validate(forgiven, searching)
        self.assertEqual(arguments["conditions"][0]["value"], 9, "What the model wrote is left as it was")
        # Where a number is meant, or one of a list, nothing is changed
        number = {"type": "object", "properties": {"n": {"type": "integer"}, "kind": {"type": "string", "enum": ["a"]}}}
        self.assertEqual(schema.forgive({"n": 3, "kind": 1}, number), {"n": 3, "kind": 1})
        # Text still refused where it is not text: nothing is guessed
        listed = {"steps": [self.step(text=["Press it."])]}
        with self.assertRaisesRegex(schema.SchemaError, "expected string"):
            schema.validate(schema.forgive(listed, self.SCHEMA), self.SCHEMA)
        # The values written as a mapping: six calls were refused for it
        planning = tools.plans.PreparePlan.input_schema
        plan = {"title": "Raghad on the Acme orders", "steps": [
            {"model": "sale.order", "action": "write", "ids": [5, 1], "values": {"user_id": 8}}]}
        forgiven = schema.forgive(plan, planning)
        self.assertEqual(forgiven["steps"][0]["values"], [{"field": "user_id", "value": "8"}])
        schema.validate(forgiven, planning)
        # One pair written alone stays as it was: refused, and shown the list
        one = {"model": "sale.order", "action": "write", "ids": [5], "values": {"field": "user_id", "value": "8"}}
        self.assertEqual(schema.forgive({"title": "x", "steps": [one]}, planning)["steps"][0]["values"], one["values"])
        # A field path written as a placeholder, or wrapped: the details of an email
        path = {"type": "string", "pattern": r"[a-z_][a-z0-9_.]*"}
        self.assertEqual(schema.forgive("{{ object.partner_id.email }}", path), "partner_id.email")
        self.assertEqual(schema.forgive("#object.id", path), "id")
        self.assertEqual(schema.forgive({"field": "user_id"}, path), "user_id")
        self.assertEqual(schema.forgive("Dear {{ object.name }}", {"type": "string"}), "Dear {{ object.name }}",
                         "Text with placeholders is left as written")
        self.assertEqual(schema.forgive("partner id!", path), "partner id!", "Not a path after cleaning: refused")

    def test_what_a_small_model_writes_around_its_arguments_is_forgiven(self):
        # Refused four times: "view_id": "" and an argument no tool has, written empty
        button = tools.base.TOOLS["prepare_button"].input_schema
        arguments = {"model": "res.partner", "label": "Archive", "action_id": 5, "view_id": "",
                     "read_automations_on_model_first": ""}
        forgiven = schema.forgive(arguments, button)
        self.assertEqual(forgiven, {"model": "res.partner", "label": "Archive", "action_id": 5})
        schema.validate(forgiven, button)
        with self.assertRaisesRegex(schema.SchemaError, "not an accepted argument"):
            schema.validate(schema.forgive(dict(arguments, colour="red"), button), button)
        with self.assertRaisesRegex(schema.SchemaError, "must not be empty"):
            schema.validate(schema.forgive(dict(arguments, label=""), button), button)
        # actions written as the text of a list: expected array, got str
        rule = tools.base.TOOLS["prepare_automation"].input_schema
        written = json.dumps([{"kind": "update", "field": "lang", "value": "ar"}])
        self.assertEqual(schema.forgive({"actions": written}, rule)["actions"][0]["kind"], "update")
        self.assertEqual(schema.forgive({"actions": "not a list"}, rule)["actions"], "not a list")
        # Code written on one line with "\n" between its lines: 7 of 8 tries refused as a SyntaxError
        checking = tools.base.TOOLS["check_python_code"].input_schema
        one_line = "for rec in records:\\n    rec.write({'comment': 'x'})"
        self.assertEqual(schema.forgive({"code": one_line}, checking)["code"],
                         "for rec in records:\n    rec.write({'comment': 'x'})")
        kept = "rec.write({'comment': 'one\\ntwo'})"
        self.assertEqual(schema.forgive({"code": kept}, checking)["code"], kept, "Code that compiles is left")

    def test_quotes_and_a_last_comma_are_forgiven(self):
        # The language '"en"' was refused, the quotes read as part of it
        searching = tools.records.SearchRecords.input_schema
        arguments = {"model": "res.partner", "conditions": [
            {"field": "lang", "operator": "=", "value": '"en_US"'}, {"field": "name", "operator": "=", "value": "'ar'"},
            {"field": "name", "operator": "=", "value": "'Bob', 'Alice'"}]}
        forgiven = schema.forgive(arguments, searching)
        self.assertEqual([condition["value"] for condition in forgiven["conditions"]], ["en_US", "ar", "'Bob', 'Alice'"])
        # A comma after the last item, as JavaScript allows it
        rule = tools.base.TOOLS["prepare_automation"].input_schema
        written = '[{"kind": "update", "field": "lang", "value": "ar"},]'
        self.assertEqual(schema.forgive({"actions": written}, rule)["actions"], [{"kind": "update", "field": "lang", "value": "ar"}])


@tagged("post_install", "-at_install")
class TestOpenAIToolCalls(BaseCase):

    TOOLS = [tools.guidance.ShowOnScreen().definition()]

    def setUp(self):
        super().setUp()
        self.adapter = OpenAICompatAdapter(base.ProviderConfig(
            kind="openai_compatible", model="test-model", api_key="sk-test",
            base_url="https://llm.invalid/v1"))
        self.posted = []

    def respond(self, *chunks):
        def post(payload, stream, read_timeout):
            self.posted.append(json.loads(json.dumps(payload)))
            return FakeResponse(lines=sse(*chunks))

        self.patch(self.adapter, "_post", post)

    def run_stream(self, turns=None):
        turns = turns or [{"role": "user", "text": "Show me", "context": ""}]
        return list(self.adapter.stream_chat(["System"], turns, tools=self.TOOLS))

    def test_tools_are_offered(self):
        self.respond(chunk("ok", finish="stop"))
        self.run_stream()
        offered = self.posted[0]["tools"]
        self.assertEqual(offered[0]["type"], "function")
        self.assertEqual(offered[0]["function"]["name"], "show_on_screen")
        self.assertEqual(offered[0]["function"]["parameters"], self.TOOLS[0]["input_schema"])

    def test_call_sent_whole(self):
        """Gemini: one chunk, signed, and the reason given is "stop"."""
        signature = {"google": {"thought_signature": "c2lnbmVk"}}
        arguments = {"steps": [{"target_type": "button", "target_name": "action_confirm", "text": "Go"}]}
        self.respond(
            chunk(tool_calls=[{
                "id": "call_9", "type": "function", "extra_content": signature,
                "function": {"name": "show_on_screen", "arguments": json.dumps(arguments)},
            }]),
            chunk(finish="stop"),
        )
        used, stop = self.run_stream()

        self.assertEqual(used, base.tool_use_event("call_9", "show_on_screen", arguments,
                                                   extra={"extra_content": signature}))
        self.assertEqual(stop["reason"], base.STOP_TOOL_USE)

    def test_call_sent_in_pieces(self):
        """OpenAI: pieces to assemble by index, several calls at once."""
        self.respond(
            chunk("Let me show you. "),
            chunk(tool_calls=[{"index": 0, "id": "call_a", "type": "function",
                               "function": {"name": "find_screen", "arguments": '{"wor'}}]),
            chunk(tool_calls=[{"index": 1, "id": "call_b", "type": "function",
                               "function": {"name": "find_screen", "arguments": ""}}]),
            chunk(tool_calls=[{"index": 0, "function": {"arguments": 'ds":"quotations"}'}}]),
            chunk(tool_calls=[{"index": 1, "function": {"arguments": '{"words":"orders"}'}}]),
            chunk(finish="tool_calls"),
        )
        text, first, second, stop = self.run_stream()

        self.assertEqual(text["text"], "Let me show you. ")
        self.assertEqual((first["id"], first["input"]), ("call_a", {"words": "quotations"}))
        self.assertEqual((second["id"], second["input"]), ("call_b", {"words": "orders"}))
        self.assertEqual(stop["reason"], base.STOP_TOOL_USE)

    def test_arguments_that_cannot_be_read(self):
        self.respond(
            chunk(tool_calls=[{"index": 0, "id": "call_a", "type": "function",
                               "function": {"name": "find_screen", "arguments": '{"words": "quo'}}]),
            chunk(finish="tool_calls"),
        )
        used, _stop = self.run_stream()
        self.assertEqual(used["input"], {})
        self.assertTrue(used["invalid"])

    def test_a_call_cut_short_is_not_run(self):
        self.respond(
            chunk(tool_calls=[{"index": 0, "id": "call_a", "type": "function",
                               "function": {"name": "find_screen", "arguments": "{}"}}]),
            chunk(finish="length"),
        )
        events = self.run_stream()
        self.assertEqual([event["type"] for event in events], ["stop"])
        self.assertEqual(events[0]["reason"], base.STOP_MAX_TOKENS)

    def test_calls_and_results_are_replayed(self):
        self.respond(chunk("ok", finish="stop"))
        signature = {"extra_content": {"google": {"thought_signature": "c2lnbmVk"}}}
        self.run_stream([
            {"role": "user", "text": "Show me", "context": ""},
            {"role": "assistant", "text": "", "tool_calls": [
                {"id": "call_9", "name": "find_screen", "input": {"words": "b", "a": 1},
                 "extra": signature},
            ]},
            {"role": "tool", "results": [
                {"id": "call_9", "name": "find_screen", "content": "[]", "is_error": False}]},
            {"role": "user", "text": "Thanks", "context": ""},
        ])
        self.assertEqual(self.posted[0]["messages"][1:], [
            {"role": "user", "content": "Show me"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_9", "type": "function",
                "function": {"name": "find_screen", "arguments": '{"a": 1, "words": "b"}'},
                "extra_content": {"google": {"thought_signature": "c2lnbmVk"}},
            }]},
            {"role": "tool", "tool_call_id": "call_9", "content": "[]"},
            {"role": "user", "content": "Thanks"},
        ])


@tagged("post_install", "-at_install")
class TestTools(NudgeCase, TransactionCase):

    def run_tool(self, name, arguments, user=None, screen=SNAPSHOT):
        env = self.env(user=user or self.user_a)
        return tools.base.call(env, name, arguments, screen)

    def test_offered_in_the_same_order(self):
        env = self.env(user=self.user_a)
        names = [tool.name for tool in tools.base.available(env)]
        self.assertEqual(names, sorted(names), "The list is part of the prompt")
        self.assertIn("show_on_screen", names)
        self.assertFalse(tools.base.available(self.env(user=self.outsider)))

    def test_a_reference_to_a_record_deleted_since_reads_as_gone(self):
        # A rule of Studio pointed at an action deleted since: the name of the action could not
        # be read, and the whole list of rules failed. Odoo clears the home action of a user
        # when its action is deleted, by hand; Studio leaves its rules pointing at nothing
        from odoo.addons.nudge.tools.records import reference_names  # noqa: PLC0415

        action = self.env["ir.actions.act_window"].create({"name": "Nudge Gone", "res_model": "res.partner"})
        user = self.user_a.sudo()
        user.write({"action_id": action.id})
        self.assertEqual(reference_names(user.action_id, "gone"), ["Nudge Gone"])
        gone = action.id
        action.unlink()
        # Nothing in the database ties a reference to an action to it
        self.env.cr.execute("UPDATE res_users SET action_id = %s WHERE id = %s", (gone, user.id))
        self.env.invalidate_all()
        self.assertTrue(user.action_id)
        with self.assertRaises(MissingError):
            user.action_id.name  # noqa: B018
        self.assertEqual(reference_names(user.action_id, "gone"), ["gone"])
        partners = self.user_a.partner_id | self.env["res.partner"].browse(999999999)
        self.assertEqual(reference_names(partners, "gone"), [self.user_a.partner_id.display_name, "gone"])
        self.assertEqual(reference_names(self.env["res.users"], "gone"), [])

    def test_unknown_tool(self):
        result = self.run_tool("drop_database", {})
        self.assertTrue(result.is_error)
        self.assertIn("no tool named", result.content)
        self.assertTrue(self.run_tool("show_on_screen", {"steps": []}, user=self.outsider).is_error)

    def test_pointing(self):
        result = self.run_tool("show_on_screen", {"steps": [
            {"target_type": "field", "target_name": "email", "text": " Type the address. "},
            {"target_type": "control", "target_name": "save", "text": "Save.", "advance": "manual"},
        ]})
        self.assertFalse(result.is_error)
        self.assertEqual(result.event, {"type": "guidance", "steps": [
            {"target": {"type": "field", "name": "email"}, "text": "Type the address.", "advance": "auto"},
            {"target": {"type": "control", "name": "save"}, "text": "Save.", "advance": "manual"},
        ]})

    def test_pointing_at_what_is_not_there(self):
        result = self.run_tool("show_on_screen", {"steps": [
            {"target_type": "button", "target_name": "action_confirm", "text": "Confirm."},
        ]})
        self.assertTrue(result.is_error)
        self.assertIsNone(result.event)
        self.assertIn("no button named 'action_confirm'", result.content)
        self.assertIn("action_archive", result.content, "The model is told what it can point at")

        result = self.run_tool("show_on_screen", {"steps": [
            {"target_type": "control", "target_name": "delete_everything", "text": "Go."},
        ]})
        self.assertTrue(result.is_error)

    def test_pointing_without_a_screen(self):
        result = self.run_tool("show_on_screen", {"steps": [
            {"target_type": "field", "target_name": "email", "text": "Here."},
        ]}, screen=None)
        self.assertTrue(result.is_error)
        self.assertIn("not sharing", result.content)

    def test_invalid_arguments_are_explained(self):
        result = self.run_tool("show_on_screen", {"steps": [{"target_name": "email", "text": "x"}]})
        self.assertTrue(result.is_error)
        self.assertIn("'target_type' is missing", result.content)
        self.assertTrue(self.run_tool("show_on_screen", "not a dict").is_error)
        # A number where text is meant is read as that text
        result = self.run_tool("find_screen", {"words": 2026}, user=self.user_contacts)
        self.assertNotIn("Invalid arguments", result.content)

    def test_a_failing_tool_does_not_break_the_conversation(self):
        def explode(env, arguments, screen):
            env["res.partner"].create({"name": "Left behind"})
            raise RuntimeError("boom")

        self.patch(tools.base.TOOLS["find_screen"], "run", explode)
        with self.assertLogs("odoo.addons.nudge.tools.base", logging.INFO):
            result = self.run_tool("find_screen", {"words": "x"}, user=self.user_contacts)
        self.assertTrue(result.is_error)
        self.assertIn("boom", result.content)
        self.assertFalse(self.env["res.partner"].search([("name", "=", "Left behind")]),
                         "What a failed tool did is undone")

    def test_a_tool_has_the_rights_of_the_person(self):
        def create(env, arguments, screen):
            self.assertFalse(env.su)
            partner = env["res.partner"].create({"name": "Made by a tool"})
            return tools.base.ToolResult(f"created {partner.id}")

        self.patch(tools.base.TOOLS["find_screen"], "run", create)
        # Allowed to create contacts
        result = self.run_tool("find_screen", {"words": "x"}, user=self.user_contacts)
        self.assertFalse(result.is_error)
        partner = self.env["res.partner"].search([("name", "=", "Made by a tool")])
        self.assertEqual(partner.create_uid, self.user_contacts)
        # Not allowed: refused by Odoo itself, and told to the model
        with self.assertLogs("odoo.addons.nudge.tools.base", logging.INFO):
            result = self.run_tool("find_screen", {"words": "x"}, user=self.user_a)
        self.assertTrue(result.is_error)
        self.assertIn("AccessError", result.content)
        self.assertEqual(self.env["res.partner"].search_count([("name", "=", "Made by a tool")]), 1)

    def test_menus_are_those_the_person_can_open(self):
        menu = self.env["ir.ui.menu"].create({
            "name": "Nudge Secret Screen",
            "action": f"ir.actions.act_window,{self.env.ref('base.action_partner_form').id}",
            "group_ids": [(4, self.env.ref("base.group_system").id)],
        })
        found = self.run_tool("find_screen", {"words": "nudge secret"})
        self.assertFalse(found.is_error)
        self.assertNotIn("Nudge Secret Screen", found.content)
        opened = self.run_tool("open_screen", {"menu_id": menu.id, "text": "Here"})
        self.assertTrue(opened.is_error)
        self.assertIsNone(opened.event)

        admin = self.env.ref("base.user_admin")
        found = self.run_tool("find_screen", {"words": "SECRET nudge"}, user=admin)
        self.assertEqual(json.loads(found.content), [
            {"menu_id": menu.id, "path": "Nudge Secret Screen"}])
        opened = self.run_tool("open_screen", {"menu_id": menu.id, "text": " On our way. "}, user=admin)
        self.assertEqual(opened.event, {
            "type": "navigate", "menu_id": menu.id, "path": "Nudge Secret Screen",
            "text": "On our way.",
        })


@tagged("post_install", "-at_install")
class TestLoop(NudgeCase, TransactionCase):

    def converse(self, adapter, text="Show me", snapshot=SNAPSHOT, conversation_id=None, user=None):
        conversation = self.env["nudge.conversation"].with_user(
            user or self.user_a)._chat_open(conversation_id)
        job = conversation._chat_prepare(text, snapshot=snapshot)
        return conversation, list(loop.run(job, conversation._chat_record))

    def test_a_round_needs_the_time_for_it(self):
        now = 1000.0
        self.assertTrue(loop.time_for_a_round(None, 500.0, now), "No deadline, no limit")
        self.assertTrue(loop.time_for_a_round(now + 100, 40.0, now), "32 seconds needed, 100 left")
        self.assertFalse(loop.time_for_a_round(now + 30, 40.0, now), "32 seconds needed, 30 left")
        self.assertTrue(loop.time_for_a_round(now + 20, 0.0, now), "15 seconds at least, 20 left")
        self.assertFalse(loop.time_for_a_round(now + 10, 0.0, now), "15 seconds at least, 10 left")

    def test_out_of_time_is_said_and_the_work_goes_on(self):
        # The first round reads something; the second never starts: the
        # time left would not see it through
        self.patch(loop, "MIN_ROUND_SECONDS", 10 ** 6)
        adapter = self.use_adapter(script=[
            answer_with(call("search_odoo_documentation", {"query": "discounts"}), text="Let me read."),
            answer_with(text="Discounts are set on the pricelist."),
        ])
        conversation, events = self.converse(adapter, text="How do discounts work?")
        self.assertEqual(len(adapter.calls), 1, "No second call with no time for it")
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["state"], "truncated")
        self.assertIn("ran out of time", events[-1]["message"])
        self.assertEqual(conversation.message_ids[-1].state, "truncated")
        self.assertFalse(conversation.stream_started_at)

        # Asked to go on, the model is given what was read
        self.patch(loop, "MIN_ROUND_SECONDS", 15)
        conversation, events = self.converse(adapter, text="Go on", conversation_id=conversation.id)
        self.assertEqual(events[-1]["state"], "complete")
        turns = adapter.calls[1]["turns"]
        self.assertTrue(any(turn["role"] == "tool" for turn in turns), "The result read before is there")
        self.assertEqual(turns[-1]["text"], "Go on")

    def test_pointing_ends_the_answer(self):
        adapter = self.use_adapter(script=[
            answer_with(guide(("field", "email", "Type the address here.")), text="Look: "),
        ])
        conversation, events = self.converse(adapter)

        self.assertEqual([event["type"] for event in events], ["start", "delta", "guidance", "done"])
        self.assertEqual(events[2]["steps"][0]["target"], {"type": "field", "name": "email"})
        self.assertEqual(events[3]["state"], "complete")
        self.assertEqual(len(adapter.calls), 1, "What is shown needs no comment from the model")

        question, answer, result = conversation.message_ids
        self.assertEqual(answer.content, "Look: ")
        self.assertEqual(answer.sudo().tool_calls[0]["name"], "show_on_screen")
        self.assertEqual(answer.screen_events, [events[2]])
        self.assertEqual(result.role, "tool")
        self.assertFalse(result.sudo().tool_results[0]["is_error"])
        self.assertFalse(conversation.stream_started_at)
        offered = [tool["name"] for tool in adapter.calls[0]["tools"]]
        self.assertEqual(offered, sorted(offered))
        self.assertIn("show_on_screen", offered)

    def test_pointing_without_a_word(self):
        adapter = self.use_adapter(script=[answer_with(guide(("field", "email", "Here.")))])
        conversation, events = self.converse(adapter)
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(conversation.message_ids[1].state, "complete")

    def test_the_model_reads_a_mistake_and_tries_again(self):
        adapter = self.use_adapter(script=[
            answer_with(guide(("button", "action_confirm", "Confirm."))),
            answer_with(guide(("button", "action_archive", "Archive it here."))),
        ])
        conversation, events = self.converse(adapter)

        self.assertEqual([event["type"] for event in events], ["start", "guidance", "done"])
        self.assertEqual(events[1]["steps"][0]["target"]["name"], "action_archive")
        self.assertEqual(len(adapter.calls), 2)
        turns = adapter.calls[1]["turns"]
        self.assertEqual([turn["role"] for turn in turns], ["user", "assistant", "tool"])
        self.assertEqual(turns[1]["tool_calls"][0]["input"]["steps"][0]["target_name"], "action_confirm")
        self.assertTrue(turns[2]["results"][0]["is_error"])
        self.assertIn("action_archive", turns[2]["results"][0]["content"])
        self.assertEqual(conversation.message_ids.mapped("role"),
                         ["user", "assistant", "tool", "assistant", "tool"])

    def test_a_result_is_read_before_answering(self):
        adapter = self.use_adapter(script=[
            answer_with(call("find_screen", {"words": "nudge conversations"})),
            [base.text_event("It is under Nudge."), base.stop_event(base.STOP_END)],
        ])
        conversation, events = self.converse(adapter, user=self.manager)

        self.assertEqual([event["type"] for event in events], ["start", "delta", "done"])
        result = adapter.calls[1]["turns"][-1]["results"][0]
        self.assertIn("Nudge/Conversations", result["content"])
        self.assertEqual(conversation.message_ids[-1].content, "It is under Nudge.")

    def test_text_after_a_tool_is_a_new_paragraph(self):
        adapter = self.use_adapter(script=[
            answer_with(call("find_screen", {"words": "zzz"}), text="Let me look."),
            [base.text_event("Nothing found."), base.stop_event(base.STOP_END)],
        ])
        _conversation, events = self.converse(adapter)
        text = "".join(event["text"] for event in events if event["type"] == "delta")
        self.assertEqual(text, "Let me look.\n\nNothing found.")

    def test_rounds_are_limited(self):
        endless = [answer_with(call("find_screen", {"words": "zzz"}, call_id=f"call_{index}"))
                   for index in range(10)]
        adapter = self.use_adapter(script=endless)
        conversation, events = self.converse(adapter)

        self.assertEqual(len(adapter.calls), 4)
        self.assertEqual(events[-1]["type"], "error")
        self.assertTrue(events[-1]["message"])
        self.assertFalse(conversation.stream_started_at)

    def test_unreadable_arguments_are_told_to_the_model(self):
        adapter = self.use_adapter(script=[
            answer_with(call("find_screen", None, invalid="The arguments are not valid JSON.")),
            [base.text_event("Sorry."), base.stop_event(base.STOP_END)],
        ])
        self.converse(adapter)
        result = adapter.calls[1]["turns"][-1]["results"][0]
        self.assertTrue(result["is_error"])
        self.assertIn("not valid JSON", result["content"])

    def test_calls_of_an_answer_cut_short_are_not_run(self):
        ran = []
        self.patch(tools.base.TOOLS["find_screen"], "run",
                   lambda env, arguments, screen: ran.append(arguments))
        adapter = self.use_adapter(script=[[
            base.text_event("Partial"),
            call("find_screen", {"words": "x"}),
            base.stop_event(base.STOP_MAX_TOKENS),
        ]])
        conversation, events = self.converse(adapter)

        self.assertEqual(ran, [])
        self.assertEqual(events[-1]["state"], "truncated")
        self.assertFalse(conversation.message_ids[-1].sudo().tool_calls)

    def test_next_question_replays_the_tools(self):
        adapter = self.use_adapter(script=[
            answer_with(guide(("field", "email", "Here."))),
            [base.text_event("You are welcome."), base.stop_event(base.STOP_END)],
        ])
        conversation, _events = self.converse(adapter)
        self.converse(adapter, text="Thanks", conversation_id=conversation.id)

        turns = adapter.calls[1]["turns"]
        self.assertEqual([turn["role"] for turn in turns], ["user", "assistant", "tool", "user"])
        self.assertEqual(turns[1]["tool_calls"][0]["name"], "show_on_screen")
        self.assertIn("unchanged", turns[3]["context"])

    def test_a_call_left_without_result_is_not_replayed(self):
        adapter = self.use_adapter(script=[answer_with(guide(("field", "email", "Here.")))])
        conversation, _events = self.converse(adapter)
        conversation.message_ids.filtered(lambda message: message.role == "tool").sudo().unlink()
        turns = conversation.sudo()._chat_build_turns()
        self.assertEqual([turn["role"] for turn in turns], ["user"])

    def test_the_browser_goes_away(self):
        adapter = self.use_adapter(reply="One two three")
        conversation = self.env["nudge.conversation"].with_user(self.user_a)._chat_open()
        job = conversation._chat_prepare("Hello")
        events = loop.run(job, conversation._chat_record)
        self.assertEqual(next(events)["type"], "start")
        self.assertEqual(next(events)["text"], "One ")
        events.close()

        answer = conversation.message_ids[-1]
        self.assertEqual(answer.state, "aborted")
        self.assertEqual(answer.content, "One ")
        self.assertFalse(conversation.stream_started_at)
        self.assertEqual(len(adapter.calls), 1)


@tagged("post_install", "-at_install")
class TestToolsOverHttp(NudgeCase, HttpCase):

    def test_guidance_reaches_the_browser(self):
        self.use_adapter(script=[answer_with(guide(("field", "email", "اكتب البريد هنا.")))])
        self.authenticate("nudge_a", "nudge_a")
        response = self.url_open("/nudge/chat/stream", timeout=30, data={
            "csrf_token": http.Request.csrf_token(self),
            "message": "وين أكتب الإيميل؟",
            "snapshot": json.dumps(SNAPSHOT),
        })
        events = [json.loads(line) for line in response.text.splitlines() if line]

        self.assertEqual([event["type"] for event in events], ["start", "guidance", "done"])
        self.assertEqual(events[1]["steps"], [{
            "target": {"type": "field", "name": "email"},
            "text": "اكتب البريد هنا.",
            "advance": "auto",
        }])
