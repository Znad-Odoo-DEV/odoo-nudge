from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import prompt

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestMenuPath(NudgeCase, TransactionCase):
    """The way through the menus, shown on the screen."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Menu = cls.env["ir.ui.menu"]
        cls.app = Menu.create({"name": "Nudge Test App"})
        cls.section = Menu.create({"name": "Nudge Reports", "parent_id": cls.app.id})
        cls.leaf = Menu.create({
            "name": "Nudge Partner Report",
            "parent_id": cls.section.id,
            "action": f"ir.actions.act_window,{cls.env.ref('base.action_partner_form').id}",
        })

    def show(self, arguments, user=None):
        return tools.base.call(self.env(user=user or self.user_a), "show_menu_path", arguments)

    def test_the_steps_go_from_the_app_down(self):
        result = self.show({"menu_id": self.leaf.id, "texts": ["افتح التطبيق", "افتح التقارير"]})
        self.assertFalse(result.is_error, result.content)
        steps = result.event["steps"]
        self.assertEqual(result.event["type"], "guidance")
        self.assertEqual([step["target"]["name"] for step in steps],
                         [str(self.app.id), str(self.section.id), str(self.leaf.id)])
        self.assertEqual([step["target"]["type"] for step in steps], ["menu"] * 3)
        self.assertEqual([step["target"]["app"] for step in steps], [True, False, False])
        self.assertEqual(steps[1]["target"]["parent"], str(self.app.id))
        self.assertEqual(steps[2]["target"]["parent_label"], "Nudge Reports")
        self.assertEqual([step["text"] for step in steps][:2], ["افتح التطبيق", "افتح التقارير"])
        self.assertEqual(steps[2]["text"], "Click “Nudge Partner Report”.", "A step without text names its menu")
        self.assertIn("3 step(s)", result.content)

    def test_a_heading_is_no_step(self):
        """In the list a section opens, a menu that holds others is a heading:
        the pointer goes from the section straight to the screen under it."""
        Menu = self.env["ir.ui.menu"]
        heading = Menu.create({"name": "Nudge Warehouse Management", "parent_id": self.section.id})
        leaf = Menu.create({
            "name": "Nudge Warehouses",
            "parent_id": heading.id,
            "action": f"ir.actions.act_window,{self.env.ref('base.action_partner_form').id}",
        })
        result = self.show({"menu_id": leaf.id, "texts": ["App", "Section", "Heading", "Screen"]})
        self.assertFalse(result.is_error, result.content)
        steps = result.event["steps"]
        self.assertEqual([step["target"]["name"] for step in steps],
                         [str(self.app.id), str(self.section.id), str(leaf.id)])
        self.assertEqual(steps[2]["target"]["parent"], str(self.section.id))
        self.assertEqual(steps[2]["target"]["parent_label"], "Nudge Reports")
        self.assertEqual([step["text"] for step in steps], ["App", "Section", "Screen"])
        self.assertIn("3 step(s)", result.content)

    def test_by_words(self):
        result = self.show({"words": "nudge partner report"})
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.event["steps"][-1]["target"]["name"], str(self.leaf.id))

    def test_what_the_person_cannot_open_is_not_shown(self):
        self.section.group_ids = self.env.ref("base.group_system")
        result = self.show({"menu_id": self.leaf.id})
        self.assertTrue(result.is_error)
        self.assertIn("No menu the person can open", result.content)
        self.assertTrue(self.show({"menu_id": 999999999}).is_error)
        self.assertTrue(self.show({}).is_error)
        self.assertTrue(self.show({"words": "nothing like this anywhere"}).is_error)

    def test_offered_in_both_modes_and_told_to_the_model(self):
        for mode in ("talk", "agent"):
            names = [tool.name for tool in tools.base.available(self.env(user=self.agent), mode)]
            self.assertIn("show_menu_path", names)
        self.assertIn("show_menu_path", prompt.SYSTEM_PERSONA)
        self.assertIn("Egyptian", prompt.SYSTEM_PERSONA)
        self.assertIn("Arabizi", prompt.SYSTEM_PERSONA)
