from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import hooks, tools
from odoo.addons.nudge.models.nudge_conversation import DEVELOPER_PARAM
from odoo.addons.nudge.tools.building import CUSTOM_MODULE

from .common import NudgeCase, require_modules

RULE = {
    "name": "Mark new buyers", "model": "res.partner", "trigger": "on_create_or_write",
    "fields": ["function"], "actions": [{"kind": "code", "code": "for r in records:\n    r.write({'comment': 'A buyer'})"}]}
EMAIL = {
    "name": "Statement", "model": "res.partner",
    "email": {"to": ["self"], "subject": "Your statement", "body": "<p>Attached.</p>"}}


@tagged("post_install", "-at_install")
class TestBuilt(NudgeCase, TransactionCase):
    """Made by Nudge: everything Developer mode builds is tagged and listed; switching off asks first,
    deletes nothing and is logged; only managers see the list; uninstalling keeps the business data."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        require_modules(cls, "base_automation")
        cls.admin = new_test_user(
            cls.env, login="nudge_built_admin",
            groups="nudge.group_nudge_agent,base.group_system,base.group_partner_manager")
        cls.env["ir.config_parameter"].sudo().set_param(DEVELOPER_PARAM, "yes")
        cls.Built = cls.env["nudge.built"]

    def setUp(self):
        # Fields made in a test go with its rollback: the registry forgets them after it
        self.addCleanup(self.registry.reset_changes)
        super().setUp()
        conversation = self.env["nudge.conversation"].with_user(self.admin)._chat_open()
        self.run_ = self.env["nudge.run"].sudo().create({
            "name": "Build things", "conversation_id": conversation.id, "user_id": self.admin.id,
            "is_developer": True})
        self.dev = self.env(user=self.admin, context={"nudge_mode": "dev", "nudge_conversation_id": conversation.id})
        self.patch(tools.developer, "designed_emails_here", lambda env: False)

    def make(self, name, arguments):
        asked = tools.base.call(self.dev, name, arguments, None, "dev")
        self.assertFalse(asked.is_error, asked.content)
        step = self.env["nudge.step"].sudo().create({
            "run_id": self.run_.id, "tool": name, "kind": "create", "state": "pending",
            "res_model": asked.pending["res_model"], "values": asked.pending["values"]})
        done = tools.base.TOOLS[name].execute(self.dev, step)
        self.assertFalse(done.is_error, done.content)
        model, ids, _names = done.records
        step.write({"state": "done", "res_model": model, "res_ids": ids})
        return step, self.env[model].browse(ids)

    def listed(self, record):
        return self.Built.search([("res_model", "=", record._name), ("res_id", "=", record.id)])

    def assert_tagged_and_listed(self, record, kind, step=None):
        tag = self.env["ir.model.data"].sudo().search([
            ("module", "=", CUSTOM_MODULE), ("model", "=", record._name), ("res_id", "=", record.id)])
        self.assertTrue(tag, f"{record._name} carries the tag")
        row = self.listed(record)
        self.assertEqual(len(row), 1, f"{record._name} is listed")
        self.assertEqual(row.kind, kind)
        self.assertEqual(row.user_id, self.admin, "Who asked")
        self.assertEqual(row.run_id, self.run_)
        self.assertTrue(row.conversation_id, "In which conversation")
        if step:
            self.assertEqual(row.step_id, step)
        return row

    # ------------------------------------------------------------- tagged and listed
    def test_each_kind_is_tagged_and_listed(self):
        step, rule = self.make("prepare_automation", RULE)
        row = self.assert_tagged_and_listed(rule, "automation", step)
        self.assertTrue(row.can_undo)
        self.assertEqual(row.state, "active")
        self.assert_tagged_and_listed(rule.action_server_ids, "server_action")

        step, action = self.make("prepare_server_action", {
            "name": "Mark as buyer", "model": "res.partner", "in_menu": True,
            "action": {"kind": "update", "field": "function", "value": "Buyer"}})
        self.assert_tagged_and_listed(action, "server_action", step)

        step, template = self.make("prepare_email_template", EMAIL)
        self.assert_tagged_and_listed(template, "email_template", step)

        step, cron = self.make("prepare_scheduled_action", {
            "name": "Nightly buyers", "model": "res.partner", "code": "log('night')",
            "every": {"number": 1, "unit": "days"}, "first_run": "now"})
        self.assert_tagged_and_listed(cron, "scheduled_action", step)

        step, button = self.make("prepare_button", {
            "model": "res.partner", "label": "Mark as buyer", "action_id": action.id})
        self.assert_tagged_and_listed(button, "button", step)

        step, field = self.make("prepare_field", {"model": "res.partner", "label": "Nudge Shade", "type": "char"})
        self.assert_tagged_and_listed(field, "field", step)

        step, report = self.make("prepare_report", {
            "model": "res.partner", "name": "Contact Sheet", "title": "Contact sheet", "fields": ["name", "email"]})
        self.assert_tagged_and_listed(report, "report", step)
        self.assertFalse(self.Built.search([("res_model", "=", "ir.ui.view"), ("name", "like", "Contact Sheet")]),
                         "The template of a report is part of the report, not a line of its own")

    def test_a_thing_is_listed_once(self):
        _step, field = self.make("prepare_field", {"model": "res.partner", "label": "Nudge Once", "type": "char"})
        self.Built._note(field)
        self.assertEqual(len(self.listed(field)), 1)

    # ------------------------------------------------------------- the backfill
    def plain_rule(self, name):
        model_id = self.env["ir.model"]._get_id("res.partner")
        return self.env["base.automation"].create({
            "name": name, "model_id": model_id, "trigger": "on_create_or_write",
            "action_server_ids": [(0, 0, {"name": name, "model_id": model_id, "state": "code", "code": "pass"})]})

    def test_the_log_of_steps_finds_what_was_made_before(self):
        rule = self.plain_rule("Old rule")
        stranger = self.plain_rule("Mark new buyers (made by a person)")
        self.env["nudge.step"].sudo().create({
            "run_id": self.run_.id, "tool": "prepare_automation", "kind": "create", "state": "done",
            "res_model": "base.automation", "res_ids": [rule.id]})
        self.assertFalse(self.listed(rule))
        self.Built._sync()
        row = self.listed(rule)
        self.assertEqual((row.source, row.user_id, row.run_id), ("log", self.admin, self.run_))
        self.assertTrue(self.env["ir.model.data"].search([
            ("module", "=", CUSTOM_MODULE), ("model", "=", "base.automation"), ("res_id", "=", rule.id)]))
        self.assertFalse(self.listed(stranger), "Never by the name alone: only a done step or a tag")
        self.assertFalse(self.env["ir.model.data"].search([
            ("module", "=", CUSTOM_MODULE), ("model", "=", "base.automation"), ("res_id", "=", stranger.id)]))
        self.Built._sync()
        self.assertEqual(len(self.listed(rule)), 1, "Run twice, listed once")

    def test_a_declined_step_lists_nothing(self):
        rule = self.plain_rule("Never asked")
        self.env["nudge.step"].sudo().create({
            "run_id": self.run_.id, "tool": "prepare_automation", "kind": "create", "state": "declined",
            "res_model": "base.automation", "res_ids": [rule.id]})
        self.Built._sync()
        self.assertFalse(self.listed(rule))

    # ------------------------------------------------------------- switching off
    def switch_off(self, built, user=None, understood=False):
        env = self.env(user=user or self.admin)
        card = built.with_env(env).action_switch_off()
        wizard = env["nudge.built.switch"].browse(card["res_id"])
        if understood:
            wizard.understood = True
        return wizard.action_confirm()

    def test_switch_off_archives_after_a_card_and_logs(self):
        _step, rule = self.make("prepare_automation", RULE)
        built = self.listed(rule)
        wizard = self.env["nudge.built.switch"].with_user(self.admin).create({"built_ids": [(6, 0, built.ids)]})
        self.assertIn("Archived", wizard.summary, "The card says what will happen")
        self.assertTrue(rule.active, "Nothing happens before the yes")
        self.switch_off(built)
        self.assertFalse(rule.active)
        self.assertTrue(rule.exists(), "Never deleted")
        built.invalidate_recordset()
        self.assertEqual(built.state, "archived")
        self.assertEqual(built.switched_off_by, self.admin)
        log = built.log_ids
        self.assertEqual((log.action, log.user_id), ("switch_off", self.admin))
        self.assertEqual(log.before, {f"base.automation,{rule.id}": {"active": True}})
        self.assertEqual(log.after, {f"base.automation,{rule.id}": {"active": False}})
        with self.assertRaises(AccessError):
            log.with_user(self.admin).write({"summary": "changed"})
        with self.assertRaises(AccessError):
            log.with_user(self.admin).unlink()
        with self.assertRaises(UserError):
            self.switch_off(built)

    def test_switching_off_a_server_action_takes_its_button_off(self):
        _step, action = self.make("prepare_server_action", {
            "name": "Mark as buyer", "model": "res.partner", "in_menu": True,
            "action": {"kind": "update", "field": "function", "value": "Buyer"}})
        _step, button = self.make("prepare_button", {
            "model": "res.partner", "label": "Mark as buyer", "action_id": action.id})
        self.switch_off(self.listed(action))
        self.assertFalse(action.binding_model_id)
        self.assertFalse(button.active, "The button goes off the form")
        self.assertTrue(action.exists())

    def test_switching_off_a_field_keeps_it_and_hides_its_view(self):
        _step, field = self.make("prepare_field", {"model": "res.partner", "label": "Nudge Shade", "type": "char"})
        views = self.env["ir.ui.view"].search([("model", "=", "res.partner"), ("arch_db", "like", f'name="{field.name}"')])
        self.assertTrue(views)
        self.switch_off(self.listed(field))
        self.assertTrue(field.exists())
        self.assertFalse(views.filtered("active"))

    def test_a_change_made_since_needs_a_tick(self):
        _step, rule = self.make("prepare_automation", RULE)
        built = self.listed(rule)
        built.sudo().made_on = "2000-01-01 00:00:00"
        wizard = self.env["nudge.built.switch"].with_user(self.admin).create({"built_ids": [(6, 0, built.ids)]})
        self.assertTrue(wizard.edited)
        self.assertIn("Changed since", wizard.summary)
        with self.assertRaises(UserError):
            wizard.action_confirm()
        self.assertTrue(rule.active)
        wizard.understood = True
        wizard.action_confirm()
        self.assertFalse(rule.active)

    def test_the_person_needs_the_rights_for_the_object(self):
        _step, rule = self.make("prepare_automation", RULE)
        built = self.listed(rule)
        with self.assertRaises(AccessError):
            # A manager of Nudge who is not an administrator of Odoo cannot archive a rule
            self.switch_off(built, user=self.manager)
        self.assertTrue(rule.active)

    # ------------------------------------------------------------- who sees it
    def test_a_plain_user_cannot_see_or_act_on_the_list(self):
        _step, rule = self.make("prepare_automation", RULE)
        built = self.listed(rule)
        for user in (self.user_a, self.agent, self.outsider):
            with self.assertRaises(AccessError):
                built.with_user(user).read(["name"])
            with self.assertRaises(AccessError):
                self.Built.with_user(user).search([])
            with self.assertRaises(AccessError):
                built.with_user(user).action_switch_off()
            with self.assertRaises(AccessError):
                self.env["nudge.built.switch"].with_user(user).create({"built_ids": [(6, 0, built.ids)]})
            with self.assertRaises(AccessError):
                self.env["nudge.built.log"].with_user(user).search([])
        self.assertTrue(self.Built.with_user(self.manager).search([("id", "=", built.id)]))
        with self.assertRaises(AccessError):
            built.with_user(self.manager).write({"name": "other"})
        with self.assertRaises(AccessError):
            built.with_user(self.manager).unlink()
        with self.assertRaises(AccessError):
            self.Built.with_user(self.manager).create(
                {"name": "x", "kind": "view", "res_model": "ir.ui.view", "res_id": 1})

    def test_the_list_follows_the_companies_of_the_user(self):
        other = self.env["res.company"].create({"name": "Your other company"})
        _step, rule = self.make("prepare_automation", RULE)
        built = self.listed(rule)
        built.sudo().company_id = other
        manager = new_test_user(self.env, login="nudge_built_m2", groups="nudge.group_nudge_manager")
        self.assertFalse(self.Built.with_user(manager).search([("id", "=", built.id)]), "Another company's")
        manager.company_ids |= other
        both = [other.id, manager.company_id.id]
        found = self.Built.with_user(manager).with_context(allowed_company_ids=both).search([("id", "=", built.id)])
        self.assertTrue(found, "Seen from the company it was made in")
        built.sudo().company_id = False
        self.assertTrue(self.Built.with_user(self.manager).search([("id", "=", built.id)]), "No company: for all")

    # ------------------------------------------------------------- uninstall
    def test_uninstall_switches_off_and_keeps_business_data(self):
        _step, rule = self.make("prepare_automation", RULE)
        _step, field = self.make("prepare_field", {"model": "res.partner", "label": "Nudge Shade", "type": "char"})
        _step, template = self.make("prepare_email_template", EMAIL)
        partner = self.env["res.partner"].create({"name": "Raghad", field.name: "Teal"})
        self.env["ir.config_parameter"].sudo().set_param("nudge.retention_days", "30")
        self.env.cr.execute("CREATE SEQUENCE IF NOT EXISTS nudge_embedding_version_seq")
        views = self.env["ir.ui.view"].search(
            [("arch_db", "like", f'name="{field.name}"'), ("model", "=", "res.partner")])
        self.assertTrue(views.filtered("active"))

        hooks.uninstall_hook(self.env)

        self.assertFalse(rule.active, "Switched off")
        self.assertTrue(rule.exists() and field.exists() and template.exists(), "Nothing deleted")
        self.assertFalse(views.filtered("active"))
        self.assertEqual(partner[field.name], "Teal", "Business data stays")
        self.env.cr.execute("SELECT 1 FROM pg_class WHERE relname = 'nudge_embedding_version_seq'")
        self.assertFalse(self.env.cr.fetchone(), "The sequence goes")
        self.env.cr.execute("SELECT 1 FROM pg_indexes WHERE indexname LIKE 'nudge\\_partner\\_%\\_key\\_idx'")
        self.assertFalse(self.env.cr.fetchone(), "The indexes go")
        self.env.cr.execute("SELECT 1 FROM ir_config_parameter WHERE key LIKE 'nudge.%'")
        self.assertFalse(self.env.cr.fetchone(), "The parameters go")
        self.assertEqual(self.listed(rule).log_ids.action, "uninstall")
        self.assertTrue(self.env["ir.model.data"].search([
            ("module", "=", CUSTOM_MODULE), ("model", "=", "ir.model.fields"), ("res_id", "=", field.id)]),
            "The tag stays: a reinstall finds it again")
