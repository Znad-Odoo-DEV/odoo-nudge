from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestNudgeApp(TransactionCase):
    """One Nudge app for every employee: it opens on My Nudge Home, and each menu inside keeps to its groups."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plain = new_test_user(cls.env, login="nudge_app_plain", name="Raghad", groups="base.group_user")
        cls.member = new_test_user(cls.env, login="nudge_app_member", name="Ebrahim", groups="nudge.group_nudge_user")
        cls.manager = new_test_user(cls.env, login="nudge_app_manager", groups="nudge.group_nudge_manager")
        cls.root = cls.env.ref("nudge.nudge_menu_root")
        cls.home = cls.env.ref("nudge.nudge_menu_home")
        cls.home_action = cls.env.ref("nudge.nudge_home_action")
        cls.manager_menus = {
            "nudge.nudge_menu_conversations", "nudge.nudge_menu_agent", "nudge.nudge_menu_knowledge",
            "nudge.nudge_menu_usage", "nudge.nudge_menu_configuration", "nudge.nudge_menu_coach",
            "nudge.nudge_menu_health", "nudge.nudge_menu_my_reports",
        }

    def reachable(self, user):
        """The xmlids of the nudge menus the web client of ``user`` can reach from the list of apps."""
        self.env.registry.clear_cache()
        menus = self.env["ir.ui.menu"].with_user(user).load_menus(False)
        seen, todo = set(), list(menus["root"]["children"])
        while todo:
            menu_id = todo.pop()
            if menu_id not in seen:
                seen.add(menu_id)
                todo.extend(menus[menu_id]["children"])
        return {menus[menu_id]["xmlid"] for menu_id in seen if menus[menu_id]["xmlid"].startswith("nudge.")}

    def test_one_app_that_opens_on_the_home(self):
        menus = self.env["ir.model.data"].search([("module", "=", "nudge"), ("model", "=", "ir.ui.menu")])
        apps = self.env["ir.ui.menu"].browse(menus.mapped("res_id")).filtered(lambda menu: not menu.parent_id)
        self.assertEqual(apps, self.root, "Nudge is one app, not two")
        self.assertEqual(self.root.action, self.home_action, "The app opens on My Nudge Home")
        self.assertEqual(self.home.parent_id, self.root)
        self.assertEqual(self.home.action, self.home_action)
        others = self.root.child_id - self.home
        self.assertTrue(all(self.home.sequence < menu.sequence for menu in others), "Home is the first menu")

    def test_an_employee_sees_the_app_and_its_home_only(self):
        self.assertEqual(self.reachable(self.plain), {"nudge.nudge_menu_root", "nudge.nudge_menu_home"})

    def test_a_nudge_user_sees_the_home_only_too(self):
        # their saved reports are on the tiles of the home; the menu of reports is for managers
        self.assertEqual(self.reachable(self.member), {"nudge.nudge_menu_root", "nudge.nudge_menu_home"})

    def test_a_manager_keeps_every_menu(self):
        menus = self.reachable(self.manager)
        self.assertLessEqual(self.manager_menus | {"nudge.nudge_menu_home"}, menus)
