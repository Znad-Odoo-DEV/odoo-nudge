from unittest.mock import patch

from odoo import fields, models
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.tools import base, records, revert
from odoo.addons.nudge.tools.records import Refused
from odoo.addons.nudge.tools.noai_commands import grammar

from .common import NudgeCase, require_modules

SCREEN = {"model": "product.template", "record_id": 5}

# (sentence, intent, field, name, current, period): the sentences of every kind of person, half of them Arabic
CASES = [
    ("مين غيّر سعر هالمنتج؟", "history", "price", "", True, None),
    ("رجّع عنوان راغد القديم", "revert", "street", "راغد", False, None),
    ("who changed the deadline of this task last week", "history", "deadline", "", True, ("rel", "last_week")),
    ("restore Raghad's old address", "revert", "street", "Raghad", False, None),
    ("منو غير الموعد النهائي للمهمة", "history", "deadline", "", True, None),
    ("شكون بدل الثمن ديال هاد المنتج", "history", "price", "", True, None),
    ("who غيّر سعر المنتج", "history", "price", "", True, None),
    ("رجّع the old phone of Ebrahim", "revert", "phone", "Ebrahim", False, None),
]


@tagged("post_install", "-at_install", "nudge")
class TestRevertParser(TransactionCase):
    """Who changed it, and put it back: sentences of any dialect and of English, no database."""

    def test_the_sentences(self):
        for text, intent, field, name, current, period in CASES:
            with self.subTest(text=text):
                parsed = grammar.parse(text, SCREEN)
                self.assertEqual(parsed.intent, intent, parsed)
                self.assertGreaterEqual(parsed.confidence, 0.7, parsed)
                self.assertEqual(parsed.slots["field"], field)
                self.assertEqual(parsed.slots["name"], name)
                self.assertEqual(parsed.slots["current"], current)
                self.assertEqual(parsed.period, period)

    def test_other_requests_are_not_taken_for_a_revert(self):
        for text in ("change the phone of Raghad to 0501112222", "غيّر جوال راغد ل 0509998888", "open invoices"):
            with self.subTest(text=text):
                self.assertNotIn(grammar.parse(text, SCREEN).intent, ("history", "revert"))

    def test_without_a_record_in_view_the_sentence_is_too_vague_to_act_on(self):
        parsed = grammar.parse("who changed the price", {})
        self.assertLess(parsed.confidence, 0.7)


@tagged("post_install", "-at_install", "nudge")
class TestRevert(NudgeCase, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        require_modules(cls, "project")
        env = cls.env
        # Raghad may let Nudge work and works on tasks; Ebrahim only reads Nudge
        cls.raghad = new_test_user(env, login="revert_raghad", groups="nudge.group_nudge_agent,project.group_project_user")
        cls.ebrahim = new_test_user(env, login="revert_ebrahim", groups="nudge.group_nudge_agent,base.group_user")
        cls.provider.active = False
        cls.project = env["project.project"].create({"name": "Raghad site"})
        cls.stage_a = env["project.task.type"].create({"name": "Todo", "project_ids": [(4, cls.project.id)]})
        cls.stage_b = env["project.task.type"].create({"name": "Doing", "project_ids": [(4, cls.project.id)]})
        cls.partner_a = env["res.partner"].create({"name": "Raghad Trading"})
        cls.partner_b = env["res.partner"].create({"name": "Ebrahim Cars"})
        cls.task = env["project.task"].with_user(cls.raghad).create({
            "name": "Fit the doors", "project_id": cls.project.id, "stage_id": cls.stage_a.id,
            "partner_id": cls.partner_a.id, "allocated_hours": 4.0, "priority": "0",
        })
        cls.flush_tracking(cls)

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("nudge.noai_first", "1")

    def flush_tracking(self):
        self.env.flush_all()
        self.env.cr.precommit.run()
        self.env.invalidate_all()

    def change(self, **values):
        self.task.with_user(self.raghad).write(values)
        self.flush_tracking()

    def rows(self, user=None, **kw):
        user = user or self.raghad
        task = self.task.with_user(user)
        return revert.decorate(task.env, task, revert.find_changes(task.env, task, **kw))

    def restore(self, user, **kw):
        """One restore, the way the pipeline does it: the card, then the yes"""
        tool = base.TOOLS["prepare_revert"]
        env = self.env(user=user)
        result = tool.run(env, dict({"model": "project.task", "id": self.task.id}, **kw), None)
        return tool, env, result

    # ------------------------------------------------------------ the list

    def test_changes_are_listed_newest_first_with_who_old_and_new(self):
        self.change(name="Fit the windows")
        self.change(allocated_hours=6.0)
        rows = self.rows()
        by_field = {row["field"]: row for row in rows}
        self.assertEqual([row["field"] for row in rows][:2], ["allocated_hours", "name"])
        self.assertEqual((by_field["name"]["old"], by_field["name"]["new"]), ("Fit the doors", "Fit the windows"))
        self.assertEqual(by_field["name"]["who"], self.raghad.name)
        self.assertEqual(by_field["allocated_hours"]["old"], "4.0")
        self.assertTrue(by_field["name"]["restorable"])
        self.assertTrue(by_field["name"]["date"] and by_field["name"]["message_id"])

    def test_a_field_the_person_may_not_read_is_not_listed(self):
        self.change(name="Fit the windows", allocated_hours=9.0)
        from odoo.addons.nudge.tools import revert as _revert_tool
        real = _revert_tool.has_field_access

        def deny_hours(model, field, operation):
            if field.name == "allocated_hours" and operation == "read":
                return False
            return real(model, field, operation)

        with patch.object(_revert_tool, "has_field_access", deny_hours):
            fields_listed = {row["field"] for row in self.rows()}
        self.assertIn("name", fields_listed)
        self.assertNotIn("allocated_hours", fields_listed)

    def test_a_field_that_holds_a_secret_under_another_name_is_not_listed(self):
        """Review (security S2): a field a view shows as a password holds a secret whatever it is called"""
        self.change(name="Fit the windows", allocated_hours=9.0)
        with patch.object(records, "password_fields",
                          lambda model: {"allocated_hours"} if model._name == "project.task" else set()):
            fields_listed = {row["field"] for row in self.rows()}
        self.assertIn("name", fields_listed)
        self.assertNotIn("allocated_hours", fields_listed)

    def test_the_name_of_a_record_the_person_may_not_open_is_not_told(self):
        self.change(partner_id=self.partner_b.id)
        self.env["ir.rule"].create({
            "name": "Nudge test: Raghad Trading is hidden", "model_id": self.env.ref("base.model_res_partner").id,
            "domain_force": f"[('id', '!=', {self.partner_a.id})]", "global": True})
        row = next(row for row in self.rows() if row["field"] == "partner_id")
        self.assertNotIn("Raghad Trading", row["old"])
        self.assertEqual(row["new"], "Ebrahim Cars")

    def test_the_tool_lists_and_says_when_a_field_is_not_tracked(self):
        self.change(name="Fit the windows")
        tool = base.TOOLS["record_changes"]
        result = tool.run(self.env(user=self.raghad), {"model": "project.task", "id": self.task.id}, None)
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.event["type"], "changes")
        self.assertTrue(result.event["can_write"])
        self.assertIn("Fit the doors -> Fit the windows", result.content)
        untracked = tool.run(
            self.env(user=self.raghad), {"model": "project.task", "id": self.task.id, "field": "description"}, None)
        self.assertIn("is not tracked", untracked.content)
        self.assertEqual(untracked.event["items"], [])

    def test_somebody_who_may_only_read_sees_the_list_without_a_button(self):
        self.change(name="Fit the windows")
        result = base.TOOLS["record_changes"].run(
            self.env(user=self.ebrahim), {"model": "project.task", "id": self.task.id}, None)
        self.assertFalse(result.is_error, result.content)
        self.assertFalse(result.event["can_write"])
        self.assertTrue(result.event["items"])
        self.assertFalse(any(item["restorable"] for item in result.event["items"]))
        self.assertIn("may not change this record", result.event["items"][0]["reason"])

    # --------------------------------------------------------- one field

    def test_restore_one_field_of_each_kind(self):
        self.change(name="Fit the windows", allocated_hours=6.0, partner_id=self.partner_b.id, priority="1")
        for field, expected in (("name", "Fit the doors"), ("allocated_hours", 4.0),
                                ("partner_id", self.partner_a), ("priority", "0")):
            with self.subTest(field=field):
                tool, env, result = self.restore(self.raghad, field=field)
                self.assertFalse(result.is_error, result.content)
                self.assertEqual(result.pending["values"]["restore"], {
                    field: expected.id if field == "partner_id" else expected})
                lines = result.pending["preview"]["lines"]
                self.assertEqual(lines[0]["label"], self.task._fields[field].string)
                # Nothing is written before the yes
                self.assertNotEqual(self.task[field], expected)
                step = self.env["nudge.step"].sudo().new({
                    "res_model": "project.task", "res_ids": self.task.ids, "values": result.pending["values"]})
                tool.execute(env, step)
                self.task.invalidate_recordset()
                self.assertEqual(self.task[field], expected)
                self.assertIn(field, step.values["undo"]["project.task"][str(self.task.id)]["before"])

    def test_restore_a_deadline_and_an_empty_value(self):
        self.change(date_deadline="2026-11-01 08:00:00")
        self.change(date_deadline="2026-12-05 08:00:00")
        tool, env, result = self.restore(self.raghad, field="date_deadline")
        self.assertEqual(result.pending["values"]["restore"], {"date_deadline": "2026-11-01 08:00:00"})
        self.change(date_deadline=False)
        # Back to what was there before the empty one
        self.assertFalse(self.task.date_deadline)
        tool, env, result = self.restore(self.raghad, field="date_deadline")
        self.assertEqual(result.pending["values"]["restore"], {"date_deadline": "2026-12-05 08:00:00"})

    # ------------------------------------------------------ a whole change

    def test_restore_the_record_as_it_was_before_a_change(self):
        self.change(name="Fit the windows", allocated_hours=6.0, priority="1")
        message_id = self.rows()[0]["message_id"]
        tool, env, result = self.restore(self.raghad, message_id=message_id)
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.pending["values"]["restore"], {"name": "Fit the doors", "allocated_hours": 4.0,
                                                               "priority": "0"})
        step = self.env["nudge.step"].sudo().new({
            "res_model": "project.task", "res_ids": self.task.ids, "values": result.pending["values"]})
        tool.execute(env, step)
        self.task.invalidate_recordset()
        self.assertEqual((self.task.name, self.task.allocated_hours, self.task.priority), ("Fit the doors", 4.0, "0"))

    # ----------------------------------------------------------- refusals

    def test_a_stage_is_never_put_back(self):
        self.change(stage_id=self.stage_b.id)
        row = next(row for row in self.rows() if row["field"] == "stage_id")
        self.assertFalse(row["restorable"])
        self.assertIn("never put back", row["reason"])
        _tool, _env, result = self.restore(self.raghad, field="stage_id")
        self.assertTrue(result.is_error)
        self.assertEqual(self.task.stage_id, self.stage_b)

    def test_the_rest_of_a_change_is_restored_and_the_state_says_why_it_stays(self):
        self.change(name="Fit the windows", stage_id=self.stage_b.id)
        message_id = self.rows()[0]["message_id"]
        _tool, _env, result = self.restore(self.raghad, message_id=message_id)
        self.assertEqual(result.pending["values"]["restore"], {"name": "Fit the doors"})
        stays = [line for line in result.pending["preview"]["lines"] if "Stays as it is" in str(line["value"])]
        self.assertEqual(len(stays), 1)

    def test_a_posted_entry_is_never_touched(self):
        move = self.env["account.move"].create({"move_type": "entry"})
        self.env.cr.execute("UPDATE account_move SET state = 'posted' WHERE id = %s", [move.id])
        move.invalidate_recordset()
        self.assertIn("posted", revert.locked_reason(self.env, move))
        self.env.cr.execute("UPDATE account_move SET state = 'draft' WHERE id = %s", [move.id])
        move.invalidate_recordset()
        self.assertEqual(revert.locked_reason(self.env, move), "")

    def test_the_lines_of_a_confirmed_order_and_a_done_move_are_never_touched(self):
        order = self.env["sale.order"].create({"partner_id": self.partner_a.id, "order_line": [(0, 0, {
            "name": "Door", "product_uom_qty": 1, "price_unit": 10.0})]})
        line = order.order_line
        self.assertEqual(revert.locked_reason(self.env, line), "")
        order.state = "sale"
        self.assertIn("confirmed order", revert.locked_reason(self.env, line))
        # A done stock move: the check only needs a record with a state
        fake = type("Move", (), {"_name": "stock.move", "state": "done", "_fields": {"state": 1}, "env": self.env})()
        self.assertIn("done stock move", revert.locked_reason(self.env, fake))

    def test_without_the_right_to_write_nothing_is_offered(self):
        self.change(name="Fit the windows")
        with patch.object(type(self.task), "has_access", lambda records, operation: operation != "write"):
            self.assertTrue(self.rows())
            self.assertFalse(any(row["restorable"] for row in self.rows()))
            _tool, _env, result = self.restore(self.raghad, field="name")
            self.assertTrue(result.is_error)
        self.assertEqual(self.task.name, "Fit the windows")

    def test_a_user_who_may_only_read_cannot_restore(self):
        self.change(name="Fit the windows")
        _tool, _env, result = self.restore(self.ebrahim, field="name")
        self.assertTrue(result.is_error)
        self.assertEqual(self.task.name, "Fit the windows")

    def test_an_old_value_that_no_longer_exists_is_not_restored(self):
        self.change(partner_id=self.partner_b.id)
        self.change(partner_id=self.partner_a.id)
        row = next(row for row in self.rows() if row["field"] == "partner_id")
        self.assertTrue(row["restorable"])
        self.partner_b.unlink()
        row = next(row for row in self.rows() if row["field"] == "partner_id")
        self.assertFalse(row["restorable"])
        self.assertIn("no longer exists", row["reason"])

    # ---------------------------------------- the card, the log, the undo

    def snapshot(self):
        return {"v": 1, "model": "project.task", "view_type": "form", "record": {"id": self.task.id}, "buttons": []}

    def test_a_restore_goes_through_the_card_the_log_and_the_undo(self):
        self.change(name="Fit the windows")
        change_id = self.rows()[0]["change_id"]
        conversation, events = self.order(self.raghad, f"/restore change {change_id}", snapshot=self.snapshot())
        cards = [event for event in events if event["type"] == "confirm"]
        self.assertEqual(len(cards), 1, events)
        self.assertEqual(cards[0]["lines"][0]["before"], "Fit the windows")
        self.assertEqual(cards[0]["lines"][0]["value"], "Fit the doors")
        self.assertEqual(self.task.name, "Fit the windows")
        done = self.decide(conversation, "confirm")
        self.task.invalidate_recordset()
        self.assertEqual(self.task.name, "Fit the doors")
        decided = [event for event in done if event["type"] == "decided"][0]
        self.assertTrue(decided["can_undo"])
        # The log keeps it
        step = self.env["nudge.step"].sudo().search([("res_model", "=", "project.task")], order="id desc", limit=1)
        self.assertEqual((step.kind, step.state), ("write", "done"))
        # Nudge's own undo takes the revert back
        result = self.env["nudge.step"].with_user(self.raghad).undo_changes(step.id)
        self.assertIn("back as they were", result["message"])
        self.task.invalidate_recordset()
        self.assertEqual(self.task.name, "Fit the windows")

    def test_a_value_changed_since_is_not_put_back_over(self):
        """Release-2 review S6: the card was read, somebody else edited, then yes"""
        self.change(name="Fit the windows")
        change_id = self.rows()[0]["change_id"]
        tool, env, result = self.restore(self.raghad, change_id=change_id)
        self.assertEqual(result.pending["values"]["restore"], {"name": "Fit the doors"})
        # somebody else edits the same field before the yes
        self.change(name="Fit the roof")
        step = self.env["nudge.step"].sudo().create({
            "run_id": self.env["nudge.run"].sudo().create({
                "name": "revert", "conversation_id": self.env["nudge.conversation"].sudo().create(
                    {"user_id": self.raghad.id}).id, "user_id": self.raghad.id, "model_name": "m"}).id,
            "call_id": "c1", "tool": "prepare_revert", "kind": "write", "state": "pending",
            "summary": "x", "res_model": "project.task", "res_ids": [self.task.id],
            "values": result.pending["values"], "preview": {}, "arguments": {}})
        with self.assertRaises(Refused) as caught:
            tool.execute(env, step)
        self.assertIn("changed since", str(caught.exception))
        self.assertEqual(self.task.name, "Fit the roof")

    def test_the_panel_button_lists_the_changes_without_any_ai(self):
        self.change(allocated_hours=6.0)
        _conversation, events = self.order(self.raghad, "/changes", snapshot=self.snapshot())
        listed = [event for event in events if event["type"] == "changes"]
        self.assertEqual(len(listed), 1, events)
        self.assertEqual(listed[0]["items"][0]["field"], "allocated_hours")

    def test_a_sentence_in_arabic_asks_for_the_card(self):
        self.change(name="Fit the windows")
        _conversation, events = self.order(self.raghad, "رجّع الاسم القديم", snapshot=self.snapshot())
        # The word for the name is a field the grammar knows: the latest change of it is offered back
        cards = [event for event in events if event["type"] == "confirm"]
        self.assertEqual(len(cards), 1, events)
        self.assertEqual(cards[0]["lines"][0]["value"], "Fit the doors")

    def test_who_changed_the_deadline_last_week_asks_the_tool(self):
        self.change(date_deadline="2026-11-01 08:00:00")
        _conversation, events = self.order(
            self.raghad, "who changed the deadline of this task", snapshot=self.snapshot())
        listed = [event for event in events if event["type"] == "changes"]
        self.assertEqual(len(listed), 1, events)
        self.assertEqual({item["field"] for item in listed[0]["items"]}, {"date_deadline"})

    def test_the_restore_command_of_a_change_that_is_gone_says_so(self):
        _conversation, events = self.order(self.raghad, "/restore change 999999", snapshot=self.snapshot())
        self.assertFalse([event for event in events if event["type"] == "confirm"])
        self.assertIn("not there any more", "".join(event.get("text", "") for event in events))
