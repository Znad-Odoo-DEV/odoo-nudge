from unittest.mock import patch

from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.models import res_company

from .common import NudgeCase, require_modules

ALL = ["clock", "photo", "tasks", "record", "note"]


@tagged("post_install", "-at_install")
class TestMobileTiles(NudgeCase, TransactionCase):
    """The quick actions of the panel on a phone are the company's, and only of the apps that are installed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        require_modules(cls, "hr")
        cls.company_b = cls.env["res.company"].create({"name": "Raghad Branch"})
        cls.worker = new_test_user(
            cls.env, login="nudge_field", groups="nudge.group_nudge_agent,base.group_user")
        cls.worker.employee_ids = [(0, 0, {"name": "Raghad Field"})]
        cls.plain = new_test_user(cls.env, login="nudge_plain", groups="nudge.group_nudge_user")

    def tiles(self, user=None, company=None):
        user = user or self.worker
        env = self.env(user=user, context=dict(allowed_company_ids=[(company or self.env.company).id]))
        if company:
            user.sudo().company_ids |= company
        return env["ir.http"]._get_nudge_session_info()["mobile_tiles"]

    def test_the_default_is_every_tile_in_order(self):
        for name in ("hr.attendance", "project.task"):
            if name not in self.env:
                self.skipTest(f"{name} is not installed")
        self.assertEqual(self.tiles(), ALL)

    def test_each_company_chooses_its_own_tiles(self):
        self.env.company.write({"nudge_mobile_tile_tasks": False, "nudge_mobile_tile_note": False})
        self.company_b.write({"nudge_mobile_tile_record": False})
        main = self.tiles()
        other = self.tiles(company=self.company_b)
        self.assertNotIn("tasks", main)
        self.assertNotIn("note", main)
        self.assertIn("record", main)
        self.assertNotIn("record", other)
        self.assertIn("note", other)

    def test_a_tile_of_an_app_that_is_not_installed_is_absent(self):
        real = res_company.app_installed

        def installed(env, model_name):
            return False if model_name in ("hr.attendance", "project.task") else real(env, model_name)

        with patch.object(res_company, "app_installed", installed):
            tiles = self.tiles()
        self.assertNotIn("clock", tiles)
        self.assertNotIn("tasks", tiles)
        self.assertEqual(tiles, ["photo", "record", "note"])

    def test_the_photo_of_a_bill_needs_the_agent_and_the_clock_an_employee(self):
        tiles = self.tiles(user=self.plain)
        self.assertNotIn("photo", tiles, "A bill is made by the agent")
        self.assertNotIn("clock", tiles, "Nobody to clock in")

    def test_the_settings_are_per_company(self):
        settings = self.env["res.config.settings"].create({"nudge_mobile_tile_photo": False})
        settings.execute()
        self.assertFalse(self.env.company.nudge_mobile_tile_photo)
        self.assertTrue(self.company_b.nudge_mobile_tile_photo)
        view = self.env["res.config.settings"].get_views([(False, "form")])["views"]["form"]["arch"]
        for name in ("nudge_mobile_tile_clock", "nudge_mobile_tile_note"):
            self.assertIn(name, view)
