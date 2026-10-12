from datetime import datetime, timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.models import nudge_task

from .test_agent import asking, call, saying, values
from .test_background import BackgroundCase

NAMED = [{"field": "name", "operator": "contains", "value": "Nudge Watched"}]


@tagged("post_install", "-at_install")
class TestTasks(BackgroundCase, TransactionCase):
    """What Nudge does by itself: as its owner, counted first, reading only."""

    def make(self, user=None, **given):
        values = {"name": "Overdue", "instruction": "List the contacts named Nudge Watched.", **given}
        return self.env["nudge.task"].with_user(user or self.agent).create(values)

    def due(self, task):
        task.sudo().next_run = fields.Datetime.now() - timedelta(minutes=1)

    def cron(self):
        self.env["nudge.task"]._cron_run()

    def test_asked_on_a_card_first(self):
        result = self.run_tool("prepare_task", {
            "name": "Overdue every morning", "kind": "schedule",
            "instruction": "List the overdue customer invoices with their amount.",
            "every": 1, "unit": "weeks", "hour": 8, "weekday": "monday",
            "model": "res.partner", "conditions": NAMED,
        })
        self.assertFalse(result.is_error, result.content)
        lines = {line["label"]: line["value"] for line in result.pending["preview"]["lines"]}
        self.assertIn("Every week, at 8:00, Monday", lines["When"])
        self.assertIn("Read, as you", lines["It may"])
        self.assertFalse(self.env["nudge.task"].search([("name", "=", "Overdue every morning")]),
                         "Nothing is made by asking")
        self.assertTrue(self.run_tool("prepare_task", {
            "name": "Watch", "kind": "watch", "instruction": "Tell me about the new contacts."}).is_error,
            "A watch needs its model")

    def test_developer_mode_builds_a_scheduled_action_instead(self):
        # "Every Monday remind the salespeople", asked in Developer mode, became a personal task
        task = tools.base.TOOLS["prepare_task"]
        env = self.env(user=self.agent)
        self.assertTrue(task.is_available(env, tools.base.MODE_AGENT))
        self.assertFalse(task.is_available(env, tools.base.MODE_DEV))

    def test_said_yes_to(self):
        self.use_adapter(script=[
            asking(call("prepare_task", {
                "name": "Morning list", "kind": "schedule", "every": 1, "unit": "days", "hour": 8,
                "instruction": "List the contacts named Nudge Watched."})),
            saying("It is set."),
        ])
        conversation, _events = self.order(self.agent, "Every morning list the watched contacts")
        self.decide(conversation)
        task = self.env["nudge.task"].search([("name", "=", "Morning list")])
        self.assertEqual(task.user_id, self.agent)
        self.assertGreater(task.next_run, fields.Datetime.now())

    def test_the_next_run_follows_the_clock_of_the_owner(self):
        self.agent.tz = "Asia/Dubai"
        task = self.make(interval_type="days", hour=8)
        # 2026-10-08 06:00 UTC is 10:00 in Dubai: 8:00 has passed, so tomorrow, 04:00 UTC
        self.assertEqual(task._next_after(datetime(2026, 10, 8, 6, 0)), datetime(2026, 10, 9, 4, 0))
        self.assertEqual(task._next_after(datetime(2026, 10, 8, 3, 0)), datetime(2026, 10, 8, 4, 0))
        weekly = self.make(name="Weekly", interval_type="weeks", hour=9, weekday="0")
        self.assertEqual(weekly._next_after(datetime(2026, 10, 8, 6, 0)).weekday(), 0)
        hourly = self.make(name="Hourly", interval_type="hours", interval_number=2)
        self.assertEqual(hourly._next_after(datetime(2026, 10, 8, 6, 0)), datetime(2026, 10, 8, 8, 0))

    def test_runs_as_its_owner_in_the_background(self):
        self.use_adapter(script=[saying("Two contacts.")])
        self.env["res.partner"].create({"name": "Nudge Watched One"})
        task = self.make(model_name="res.partner", conditions=NAMED)
        self.due(task)
        self.cron()
        conversation = task.conversation_id
        run = conversation.sudo().run_id
        self.assertEqual((conversation.user_id, conversation.task_id), (self.agent, task))
        self.assertEqual((run.user_id, run.is_background, run.state), (self.agent, True, "queued"))
        self.assertEqual(conversation.message_ids[0].content, task.instruction)
        self.assertGreater(task.next_run, fields.Datetime.now())
        self.work(run)
        self.assertEqual(run.state, "done")
        self.assertEqual(task.last_result, "Two contacts.")
        self.assertEqual(task.run_count, 1)

    def test_nothing_matches_nothing_is_spent(self):
        adapter = self.use_adapter()
        task = self.make(model_name="res.partner", conditions=[
            {"field": "name", "operator": "contains", "value": "Nobody Is Called So"}])
        self.due(task)
        self.cron()
        self.assertFalse(task.conversation_id)
        self.assertEqual(task.skip_count, 1)
        self.assertEqual(adapter.calls, [])

    def test_a_watch_sees_only_what_is_new(self):
        self.use_adapter(script=[saying("One new contact."), saying("Again.")])
        task = self.make(kind="watch", model_name="res.partner", conditions=NAMED)
        task.sudo().last_check = fields.Datetime.now() - timedelta(minutes=5)
        new = self.env["res.partner"].create({"name": "Nudge Watched New"})
        new.flush_recordset()
        self.env.cr.execute("UPDATE res_partner SET create_date = %s WHERE id = %s",
                            (fields.Datetime.now() - timedelta(minutes=1), new.id))
        self.env["res.partner"].invalidate_model(["create_date"])
        self.cron()
        message = task.conversation_id.message_ids[0].content
        self.assertIn(f"ids {new.id}", message)
        # Looked at again: nothing new, nothing asked
        self.work(task.conversation_id.sudo().run_id)
        count = task.run_count
        self.cron()
        self.assertEqual(task.run_count, count)
        self.assertEqual(task.skip_count, 1)

    def test_a_change_waits_for_its_owner(self):
        self.use_adapter(script=[
            asking(call("prepare_write", {
                "model": "res.partner", "ids": self.bob.ids, "values": values(function="Owner")})),
            saying("Done."),
        ])
        task = self.make(instruction="Make Bob the owner.")
        self.due(task)
        self.cron()
        run = task.conversation_id.sudo().run_id
        self.work(run)
        self.assertEqual(run.state, "waiting")
        self.assertEqual(self.bob.function, "Buyer", "A task never decides for its owner")

    def test_only_theirs_and_within_the_limit(self):
        task = self.make()
        with self.assertRaises(AccessError):
            self.make(user=self.user_a)
        self.assertIn(task, self.env["nudge.task"].with_user(self.manager).search([]), "Managers read them all")
        with self.assertRaises(AccessError):
            task.with_user(self.agent).write({"user_id": self.manager.id})
        self.env["ir.config_parameter"].sudo().set_param(nudge_task.TASKS_PARAM, "1")
        with self.assertRaises(UserError):
            self.make(name="Second")
        self.env["ir.config_parameter"].sudo().set_param(nudge_task.TASKS_PARAM, "0")
        self.assertNotIn("prepare_task", [tool.name for tool in tools.base.available(self.env(user=self.agent), "agent")])

    def test_an_owner_without_agent_mode_stops_their_tasks(self):
        task = self.make()
        # Taken out of Agent mode, as an administrator would
        self.patch(type(self.env["nudge.conversation"]), "_can_use_agent", lambda conversation: False)
        self.due(task)
        self.cron()
        self.assertFalse(task.sudo().active)
        self.assertIn("Stopped", task.sudo().last_result)
