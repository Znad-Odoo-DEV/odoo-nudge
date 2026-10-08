from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.tools import plans

from .test_agent import asking, call, saying, values
from .test_background import BackgroundCase


def writing(model, conditions=None, ids=None, **given):
    step = {"action": "write", "model": model, "values": values(**given)}
    if ids:
        step["ids"] = ids
    if conditions:
        step["conditions"] = conditions
    return step


NAMED = [{"field": "name", "operator": "contains", "value": "Nudge Plan"}]


@tagged("post_install", "-at_install")
class TestPlans(BackgroundCase, TransactionCase):
    """Many records, or a few steps on them, with one yes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env["res.partner"]
        cls.head = Partner.create({"name": "Nudge Plan Head", "is_company": True})
        cls.partners = Partner.create([{"name": f"Nudge Plan {index:02d}", "function": "Clerk"}
                                       for index in range(30)])

    def plan(self, steps, title="Make them buyers", user=None):
        return self.run_tool("prepare_plan", {"title": title, "steps": steps}, user=user)

    def asked(self, steps, title="Make them buyers"):
        """The model asks for the plan; the conversation waits for the person"""
        self.use_adapter(script=[
            asking(call("prepare_plan", {"title": title, "steps": steps})),
            saying("Done."),
        ])
        conversation, _events = self.order(self.agent, "Make them all buyers")
        return conversation

    def test_tried_first_and_undone(self):
        few = self.partners[:5]
        result = self.plan([writing("res.partner", ids=few.ids, function="Buyer")])
        self.assertFalse(result.is_error, result.content)
        preview = result.pending["preview"]["plan"]
        self.assertEqual((preview["total"], preview["passing"], preview["failing"]), (5, 5, []))
        self.assertTrue(preview["undoable"])
        self.assertFalse(preview["background"])
        self.assertEqual(set(few.mapped("function")), {"Clerk"}, "Nothing is done by asking")

    def test_the_rehearsal_says_which_would_not_go_through(self):
        # A contact cannot be its own company: Odoo refuses it for the head only
        group = self.partners[:3] | self.head
        result = self.plan([writing("res.partner", ids=group.ids, parent_id=str(self.head.id))])
        preview = result.pending["preview"]["plan"]
        self.assertEqual((preview["total"], preview["passing"]), (4, 3))
        self.assertEqual(preview["failing"][0]["name"], self.head.display_name)
        self.assertIn("would not", result.content)
        self.assertFalse(self.partners[:3].parent_id, "Undone after the rehearsal")

    def test_records_by_conditions_and_the_limit(self):
        result = self.plan([writing("res.partner", conditions=NAMED, function="Buyer")])
        self.assertEqual(result.pending["preview"]["plan"]["total"], 31)
        self.assertTrue(result.pending["preview"]["plan"]["background"])
        self.env["ir.config_parameter"].sudo().set_param(plans.PLAN_PARAM, "10")
        result = self.plan([writing("res.partner", conditions=NAMED, function="Buyer")])
        self.assertTrue(result.is_error)
        self.assertIn("more than the 10", result.content)
        self.env["ir.config_parameter"].sudo().set_param(plans.PLAN_PARAM, "5000")
        self.assertEqual(plans.max_records(self.env), plans.HARD_MAX_RECORDS)

    def test_a_button_must_exist(self):
        result = self.plan([{"action": "press", "model": "res.partner", "ids": self.partners[:2].ids,
                             "method": "action_nowhere"}])
        self.assertTrue(result.is_error)
        self.assertIn("no button", result.content)

    def test_one_yes_for_all_then_undo(self):
        few = self.partners[:4]
        conversation = self.asked([writing("res.partner", ids=few.ids, function="Buyer")])
        step = conversation.sudo().waiting_step_id
        card = step._get_card()
        self.assertEqual(card["plan"]["total"], 4)
        self.decide(conversation)
        self.assertEqual(set(few.mapped("function")), {"Buyer"})
        step.invalidate_recordset()
        card = step._get_card()
        self.assertEqual((card["state"], card["done_count"], card["can_undo"]), ("done", 4, True))
        # Someone changes one of them since: that one is left as it is
        few[0].function = "Manager"
        Step = self.env["nudge.step"].with_user(self.agent)
        with self.assertRaises(AccessError):
            self.env["nudge.step"].with_user(self.user_a).undo_changes(step.id)
        undone = Step.undo_changes(step.id)
        self.assertEqual(few[1:].mapped("function"), ["Clerk"] * 3)
        self.assertEqual(few[0].function, "Manager")
        self.assertIn("3", undone["message"])
        self.assertFalse(undone["card"]["can_undo"])
        with self.assertRaises(UserError):
            Step.undo_changes(step.id)

    def test_only_those_that_go_through(self):
        group = self.partners[:3] | self.head
        conversation = self.asked([writing("res.partner", ids=group.ids, parent_id=str(self.head.id))])
        self.decide(conversation, "passing")
        self.assertEqual(self.partners[:3].parent_id, self.head)
        self.assertFalse(self.head.parent_id)
        step = conversation.sudo().run_id.step_ids.filtered(lambda entry: entry.tool == "prepare_plan")
        self.assertIn("left out", step.result)

    def test_passing_is_for_plans_only(self):
        self.use_adapter(script=[asking(call("prepare_write", {
            "model": "res.partner", "ids": self.partners[:1].ids, "values": values(function="Buyer")}))])
        conversation, _events = self.order(self.agent, "Make him a buyer")
        with self.assertRaises(UserError):
            self.decide(conversation, "passing")

    def test_many_records_in_the_background_by_pieces(self):
        conversation = self.asked([writing("res.partner", conditions=NAMED, function="Buyer")])
        step = conversation.sudo().waiting_step_id
        self.decide(conversation)
        run = step.run_id
        self.assertEqual((step.state, run.state, run.is_background), ("working", "queued", True))
        self.assertEqual(self.partners[0].function, "Clerk", "Nothing yet: it waits for its turn")
        # A piece whose time is up after 11 records leaves the rest to the next one
        clock = iter([0] * 12 + [10 ** 9] * 100)
        self.patch(plans, "now", lambda: next(clock))
        self.work(run)
        step.invalidate_recordset()
        self.assertEqual(step.state, "working")
        self.assertEqual(run.state, "queued")
        self.assertIn("progress", [payload["kind"] for _channel, payload in self.told()])
        self.assertEqual(step._get_card()["done_count"], 11)
        self.patch(plans, "now", lambda: 0)
        self.work(run)
        step.invalidate_recordset()
        self.assertEqual(step.state, "done")
        self.assertEqual(set(self.partners.mapped("function")), {"Buyer"})
        self.assertEqual(step._get_card()["done_count"], 31)
        self.assertTrue(step._get_card()["can_undo"])

    def test_a_single_change_can_be_undone_too(self):
        self.use_adapter(script=[
            asking(call("prepare_write", {
                "model": "res.partner", "ids": self.partners[:2].ids, "values": values(function="Buyer")})),
            saying("Done."),
        ])
        conversation, _events = self.order(self.agent, "Make them buyers")
        step = conversation.sudo().waiting_step_id
        self.decide(conversation)
        self.env["nudge.step"].with_user(self.agent).undo_changes(step.id)
        self.assertEqual(self.partners[:2].mapped("function"), ["Clerk", "Clerk"])

    def test_offered_to_the_agent(self):
        names = [tool.name for tool in tools.base.available(self.env(user=self.agent), "agent")]
        self.assertIn("prepare_plan", names)
        self.assertIn("prepare_plan", tools.base.ESSENTIAL_TOOLS)
        self.assertNotIn("prepare_plan", [tool.name for tool in tools.base.available(self.env(user=self.user_a), "talk")])
