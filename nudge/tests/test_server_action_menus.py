"""A menu that opens a server action is run the way the browser runs it: through
``ir.actions.server.run()``, as each kind of user who sees the menu. Odoo asks write access on
the model of an action without groups, so a read-only model needs the groups of its menu."""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestServerActionMenus(NudgeCase, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.auditor = new_test_user(
            cls.env, login="nudge_sa_auditor", groups="nudge.group_nudge_auditor",
            nudge_auditor_until=fields.Date.context_today(cls.env["res.users"]) + timedelta(days=10))
        cls.system = new_test_user(cls.env, login="nudge_sa_system", groups="base.group_system")
        field = "group_ids" if "group_ids" in cls.env["res.users"]._fields else "groups_id"
        cls.manager.write({field: [(4, cls.env.ref("nudge.group_nudge_advanced_menus").id)]})
        cls.everyone = [cls.user_a, cls.agent, cls.manager, cls.auditor, cls.system, cls.outsider]

    def test_every_menu_server_action_runs_for_each_user_who_sees_the_menu(self):
        Menu = self.env["ir.ui.menu"]
        data = self.env["ir.model.data"].sudo().search([("model", "=", "ir.ui.menu"), ("module", "like", "nudge%")])
        menus = Menu.sudo().browse(data.mapped("res_id")).exists().filtered(
            lambda menu: menu.action and menu.action._name == "ir.actions.server")
        self.assertGreaterEqual(len(menus), 8, "The menus of server actions are found")
        run = 0
        for menu in menus:
            for user in self.everyone:
                if menu.id not in Menu.with_user(user)._visible_menu_ids():
                    continue
                action = self.env[menu.action._name].with_user(user).browse(menu.action.id)
                with self.subTest(menu=menu.complete_name, user=user.login):
                    action.run()
                run += 1
        self.assertGreaterEqual(run, len(menus), "Every menu was run by someone who sees it")
