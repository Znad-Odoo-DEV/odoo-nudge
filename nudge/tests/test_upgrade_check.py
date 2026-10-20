import json
import os
import tempfile
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools

from .common import NudgeCase

OLD_ARCH = (
    "<xpath expr=\"//field[@name='name']\" position=\"after\"><field name=\"ref\" "
    "attrs=\"{'invisible': [('active', '=', False)]}\"/></xpath>")


@tagged("post_install", "-at_install")
class TestUpgradeCheck(NudgeCase, TransactionCase):
    """The check before an upgrade: deterministic, read-only, administrators only."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.Report = env["nudge.health.report"]
        cls.boss = new_test_user(env, login="upgrade_boss", groups="nudge.group_nudge_manager,base.group_system")
        partner = env["ir.model"]._get("res.partner")
        cls.x_field = env["ir.model.fields"].create({
            "name": "x_raghad_note", "model_id": partner.id, "ttype": "char", "field_description": "Raghad note"})
        cls.x_computed = env["ir.model.fields"].create({
            "name": "x_raghad_total", "model_id": partner.id, "ttype": "float", "field_description": "Raghad total",
            "compute": "for rec in self:\n    rec['x_raghad_total'] = 1.0"})
        base_view = env.ref("base.view_partner_form")
        cls.attrs_view = env["ir.ui.view"].create({
            "name": "Raghad attrs", "model": "res.partner", "inherit_id": base_view.id, "mode": "extension",
            "arch": "<xpath expr=\"//field[@name='name']\" position=\"after\"><field name=\"ref\"/></xpath>"})
        # The validator refuses attrs, so the old markup goes in as an upgraded database carries it
        env.cr.execute(
            "UPDATE ir_ui_view SET arch_db = jsonb_build_object('en_US', %s::text) WHERE id = %s",
            [OLD_ARCH, cls.attrs_view.id])
        cls.attrs_view.invalidate_recordset()
        users = env["ir.model"]._get("res.users")
        cls.web_action = env["ir.actions.server"].create({
            "name": "Raghad link", "model_id": users.id, "state": "code",
            "code": "action = {'type': 'ir.actions.act_url', 'url': '/web#id=%s&model=res.partner' % record.id}"})
        cls.groups_action = env["ir.actions.server"].create({
            "name": "Ebrahim groups", "model_id": users.id, "state": "code",
            "code": "names = record.groups_id.mapped('name')"})
        cls.module = env["ir.module.module"].create({
            "name": "x_raghad_custom", "shortdesc": "Raghad custom", "author": "Raghad Co",
            "state": "installed", "latest_version": "18.0.1.0.0"})

    def run_for(self, target, user=None):
        return self.Report.with_user(user or self.boss).run_upgrade_check(target)

    def line(self, report, check, key_start):
        return report.finding_ids.filtered(lambda f: f.check == check and f.key.startswith(key_start))

    def shown_ids(self, line):
        action = line.action_show()
        return set(self.env[action["res_model"]].search(action["domain"]).ids)

    def test_only_an_administrator_runs_it(self):
        with self.assertRaises(AccessError):
            self.run_for("19.0", self.manager)
        with self.assertRaises(AccessError):
            self.run_for("19.0", self.user_a)
        with self.assertRaises(UserError):
            self.run_for("99.0")

    def test_every_check_runs_and_the_report_is_kept_with_the_health_ones(self):
        report = self.run_for("19.0")
        self.assertEqual((report.scope, report.target), ("upgrade", "19.0"))
        skipped = [item for item in json.loads(report.skipped_json) if item["check"].startswith("up_")]
        self.assertFalse(skipped, "no upgrade check may fail to run")
        self.assertEqual(self.run_for("19.0").previous_id, report)
        health = self.Report.with_user(self.boss).run_check()
        self.assertEqual(health.scope, "health")
        self.assertNotEqual(health.previous_id, report)

    def test_the_check_changes_nothing_but_the_report(self):
        names = ("ir.ui.view", "ir.actions.server", "ir.model.fields", "ir.module.module", "res.partner")
        before = {name: self.env[name].search_count([]) for name in names}
        self.run_for("19.0")
        self.assertEqual(before, {name: self.env[name].search_count([]) for name in names})

    def test_a_studio_field_and_hidden_logic_are_listed(self):
        report = self.run_for("19.0")
        fields = self.line(report, "up_studio", "fields|")
        self.assertIn(self.x_field.id, self.shown_ids(fields))
        compute = self.line(report, "up_studio", "compute|")
        self.assertIn(self.x_computed.id, self.shown_ids(compute))
        self.assertEqual(compute.severity, "warning")

    def test_a_view_with_attrs_is_flagged_for_18(self):
        report = self.run_for("18.0")
        line = self.line(report, "up_markup", "attrs|views")
        self.assertEqual(line.severity, "critical")
        self.assertIn(self.attrs_view.id, self.shown_ids(line))
        self.assertIn("attrs=", line.message)

    def test_a_standard_view_is_left_alone(self):
        line = self.line(self.run_for("19.0"), "up_markup", "attrs|views")
        self.assertNotIn(self.env.ref("base.view_partner_form").id, self.shown_ids(line))

    def test_a_server_action_with_web_hash_is_flagged_for_19(self):
        line = self.line(self.run_for("19.0"), "up_markup", "webhash|server")
        self.assertEqual(line.severity, "warning")
        self.assertIn(self.web_action.id, self.shown_ids(line))
        self.assertIn("/odoo", line.message)

    def test_a_custom_module_without_a_migration_folder_is_flagged(self):
        line = self.line(self.run_for("19.0"), "up_modules", "nomigration|x_raghad_custom")
        self.assertEqual(line.severity, "warning")
        self.assertIn("x_raghad_custom", line.message)
        with tempfile.TemporaryDirectory() as folder:
            os.makedirs(os.path.join(folder, "migrations", "19.0.1.0.0"))
            with patch.object(tools.upgrade, "get_module_path", return_value=folder):
                report = self.run_for("19.0")
        self.assertFalse(self.line(report, "up_modules", "nomigration|x_raghad_custom"))

    def test_the_odoo_modules_are_not_custom(self):
        names = tools.upgrade.custom_modules(self.env).mapped("name")
        self.assertNotIn("base", names)
        self.assertNotIn("sale_management", names)
        self.assertIn("x_raghad_custom", names)

    def test_a_bundled_rename_is_matched_on_the_model_of_the_record(self):
        line = self.line(self.run_for("19.0"), "up_renamed", "server|res_users_groups")
        self.assertEqual(line.severity, "critical")
        self.assertIn(self.groups_action.id, self.shown_ids(line))
        self.assertIn("group_ids", line.message)
        # The same field name on another model's action is not the rename
        other = self.env["ir.actions.server"].create({
            "name": "Raghad other", "model_id": self.env["ir.model"]._get("res.partner").id, "state": "code",
            "code": "names = record.groups_id"})
        again = self.line(self.run_for("19.0"), "up_renamed", "server|res_users_groups")
        self.assertNotIn(other.id, self.shown_ids(again))

    def test_the_rename_list_is_per_target(self):
        self.assertFalse(self.line(self.run_for("18.0"), "up_renamed", "server|res_users_groups"))
        self.assertTrue(tools.upgrade.rules_for("19.0"))
        self.assertFalse(tools.upgrade.rules_for("20.0"))

    def test_the_platform_note_and_arabic(self):
        report = self.run_for("19.0")
        self.assertIn("daily backup", self.line(report, "up_modules", "platform").message)
        english = self.line(report, "up_markup", "webhash|server").message
        self.env["res.lang"]._activate_lang("ar_001")
        arabic = self.line(report.with_context(lang="ar_001"), "up_markup", "webhash|server")
        self.assertNotEqual(arabic.message, english)
        rename = self.line(report.with_context(lang="ar_001"), "up_renamed", "server|res_users_groups")
        self.assertIn("في 19", rename.message)

    def test_a_dangling_action_is_critical(self):
        action = self.env["ir.actions.act_window"].create({"name": "Ebrahim gone", "res_model": "res.partner"})
        self.env.cr.execute("UPDATE ir_act_window SET res_model = 'x_gone.model' WHERE id = %s", [action.id])
        line = self.line(self.run_for("19.0"), "up_data", "windows_nomodel")
        self.assertEqual(line.severity, "critical")
