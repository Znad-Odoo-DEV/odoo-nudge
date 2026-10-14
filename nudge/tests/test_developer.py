from types import SimpleNamespace

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import base as llm
from odoo.addons.nudge.llm import loop, prompt
from odoo.addons.nudge.models.nudge_conversation import DEVELOPER_PARAM
from odoo.addons.nudge.tools import developer
from odoo.addons.nudge.tools.developer import compile_code
from odoo.addons.nudge.tools.records import Refused

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestDeveloper(NudgeCase, TransactionCase):
    """Developer mode: Nudge builds, for an administrator, after a yes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = new_test_user(
            cls.env, login="nudge_dev_admin",
            groups="nudge.group_nudge_agent,base.group_system,base.group_partner_manager")
        cls.Parameter = cls.env["ir.config_parameter"].sudo()
        cls.has_rules = "base.automation" in cls.env

    def setUp(self):
        super().setUp()
        # The plain emails of Nudge, whether Nudge: Emails is installed here or not
        self.patch(developer, "designed_emails_here", lambda env: False)

    def switch(self, on):
        self.Parameter.set_param(DEVELOPER_PARAM, "yes" if on else "no")

    def dev_env(self, user=None):
        return self.env(user=user or self.admin, context={"nudge_mode": "dev"})

    def run_tool(self, name, arguments, user=None, mode="dev", context=None):
        env = self.env(user=user or self.admin, context=context if context is not None else {"nudge_mode": mode})
        return tools.base.call(env, name, arguments, None, mode)

    def execute(self, pending, user=None):
        tool = tools.base.TOOLS[pending["tool"]] if isinstance(pending, dict) and "tool" in pending else None
        raise AssertionError("use do()")

    def do(self, name, result, user=None):
        """Run what a card waits for, as the person who said yes."""
        step = SimpleNamespace(
            values=result.pending["values"], res_model=result.pending["res_model"],
            res_ids=result.pending["res_ids"])
        return tools.base.TOOLS[name].execute(self.dev_env(user), step)

    # ------------------------------------------------------------- the switch
    def test_off_by_default(self):
        self.assertFalse(self.env["nudge.conversation"].with_user(self.admin)._can_develop())
        offered = [tool.name for tool in tools.base.available(self.env(user=self.admin), "dev")]
        self.assertNotIn("prepare_automation", offered)
        self.assertNotIn("prepare_delete", offered)
        with self.assertRaises(UserError):
            self.env["nudge.conversation"].with_user(self.admin)._chat_open()._chat_prepare("Build", mode="dev")

    def test_on_for_administrators_only(self):
        self.switch(True)
        self.assertTrue(self.env["nudge.conversation"].with_user(self.admin)._can_develop())
        self.assertFalse(self.env["nudge.conversation"].with_user(self.agent)._can_develop())
        offered = [tool.name for tool in tools.base.available(self.env(user=self.admin), "dev")]
        for name in ("prepare_automation", "prepare_server_action", "prepare_email_template",
                     "prepare_delete", "read_automations", "check_python_code",
                     # Everything of Agent mode holds
                     "search_records", "prepare_write", "aggregate_records", "show_on_screen"):
            self.assertIn(name, offered, name)
        agent = [tool.name for tool in tools.base.available(self.env(user=self.admin), "agent")]
        self.assertNotIn("prepare_automation", agent, "Building is Developer mode's")
        self.assertNotIn("prepare_automation", [
            tool.name for tool in tools.base.available(self.env(user=self.agent), "dev")])

    def test_developer_models_open_in_developer_mode_only(self):
        self.switch(True)
        closed = self.run_tool("search_records", {"model": "ir.actions.server"}, mode="agent", context={"nudge_mode": "agent"})
        self.assertIn("no model", closed.content)
        opened = self.run_tool("search_records", {"model": "ir.actions.server", "limit": 1})
        self.assertFalse(opened.is_error, opened.content)
        # The context alone opens nothing to someone the mode is not open to
        refused = self.run_tool("search_records", {"model": "ir.actions.server"}, user=self.agent, mode="agent")
        self.assertIn("no model", refused.content)

    # ------------------------------------------------------------- code
    def test_code_is_checked(self):
        self.switch(True)
        self.assertEqual(compile_code("record.write({'comment': 'x'})"), "")
        self.assertIn("line 1", compile_code("record.write({"))
        ok = self.run_tool("check_python_code", {"code": "for r in records:\n    r.write({'function': 'Buyer'})"})
        self.assertFalse(ok.is_error, ok.content)
        bad = self.run_tool("check_python_code", {"code": "import os"})
        self.assertTrue(bad.is_error)
        refused = self.run_tool("prepare_server_action", {
            "name": "Broken", "model": "res.partner", "action": {"kind": "code", "code": "record.write({"}})
        self.assertTrue(refused.is_error)
        self.assertIn("does not compile", refused.content)

    # ------------------------------------------------------------- automation rules
    def test_an_automation_rule_is_made_after_a_yes(self):
        if not self.has_rules:
            self.skipTest("Automation Rules is not installed")
        self.switch(True)
        Rule = self.env["base.automation"]
        before = Rule.search_count([])
        result = self.run_tool("prepare_automation", {
            "name": "Mark new buyers",
            "model": "res.partner",
            "trigger": "on_create_or_write",
            "fields": ["function"],
            "conditions": [{"field": "function", "operator": "contains", "value": "Buyer"}],
            "actions": [{"kind": "code", "code": "for r in records:\n    r.write({'comment': 'A buyer'})"}],
        })
        self.assertFalse(result.is_error, result.content)
        pending = result.pending
        self.assertEqual(pending["summary"], "Create: automation rule “Mark new buyers”")
        self.assertTrue(pending["is_sensitive"], "Code is sensitive")
        lines = {line["label"]: line for line in pending["preview"]["lines"]}
        self.assertEqual(lines["When"]["value"], "when a record is created or changed")
        self.assertEqual(lines["Only when these change"]["value"], "Job Position")
        self.assertIn("Job Position contains Buyer", lines["Only for"]["value"])
        action = next(line for label, line in lines.items() if label.startswith("Action 1"))
        self.assertTrue(action["lines"][0]["code"], "The code is shown as code")
        self.assertIn("r.write", action["lines"][0]["value"])
        self.assertEqual(Rule.search_count([]), before, "Nothing before the yes")

        done = self.do("prepare_automation", result)
        self.assertFalse(done.is_error, done.content)
        rule = Rule.search([("name", "=", "Mark new buyers")])
        self.assertEqual(len(rule), 1)
        self.assertEqual((rule.trigger, rule.model_name), ("on_create_or_write", "res.partner"))
        self.assertEqual(rule.trigger_field_ids.mapped("name"), ["function"])
        self.assertIn("Buyer", rule.filter_domain)
        self.assertEqual(rule.action_server_ids.mapped("state"), ["code"])
        self.assertEqual(rule.action_server_ids.usage, "base_automation")
        self.assertEqual(done.event["model"], "base.automation")
        # It runs: a new buyer is marked
        partner = self.env["res.partner"].create({"name": "Nudge Buyer", "function": "Buyer"})
        self.assertIn("A buyer", partner.comment or "")

    def test_a_time_trigger_needs_its_delay(self):
        if not self.has_rules:
            self.skipTest("Automation Rules is not installed")
        self.switch(True)
        refused = self.run_tool("prepare_automation", {
            "name": "Remind", "model": "res.partner", "trigger": "on_time",
            "actions": [{"kind": "update", "field": "function", "value": "Late"}]})
        self.assertTrue(refused.is_error)
        self.assertIn("delay", refused.content)
        result = self.run_tool("prepare_automation", {
            "name": "Remind", "model": "res.partner", "trigger": "on_time_created",
            "delay": {"number": 2, "unit": "day"},
            "actions": [{"kind": "update", "field": "function", "value": "Follow up"}]})
        self.assertFalse(result.is_error, result.content)
        lines = {line["label"]: line["value"] for line in result.pending["preview"]["lines"] if "value" in line}
        self.assertEqual(lines["Delay"], "2 day after")
        self.assertFalse(result.pending["is_sensitive"], "An update is not code")
        done = self.do("prepare_automation", result)
        rule = self.env["base.automation"].search([("name", "=", "Remind")])
        self.assertEqual((rule.trg_date_range, rule.trg_date_range_type), (2, "day"))
        self.assertEqual(rule.action_server_ids.update_path, "function")
        self.assertEqual(rule.action_server_ids.value, "Follow up")

    # ------------------------------------------------------------- emails
    def test_an_email_on_an_event_makes_its_template_and_its_rule(self):
        if not self.has_rules or "mail.template" not in self.env:
            self.skipTest("Automation Rules or Discuss is not installed")
        self.switch(True)
        result = self.run_tool("prepare_automation", {
            "name": "Welcome the customer",
            "model": "res.partner",
            "trigger": "on_create",
            "actions": [{"kind": "email", "email": {
                "to": ["email", "sales@nudge.example"],
                "cc": ["user_id"],
                "subject": "Welcome, {{ object.name }}",
                "body": "Hello {{ object.name }},\nWelcome aboard.",
            }}],
        })
        self.assertFalse(result.is_error, result.content)
        lines = {line["label"]: line for line in result.pending["preview"]["lines"]}
        email = {line["label"]: line for line in next(
            line for label, line in lines.items() if label.startswith("Action 1"))["lines"]}
        self.assertEqual(email["To"]["value"], "Email (email), sales@nudge.example")
        self.assertEqual(email["Copy to"]["value"], "Salesperson (user_id)")
        self.assertTrue(email["Body"]["code"])
        self.assertFalse(result.pending["is_sensitive"])

        done = self.do("prepare_automation", result)
        self.assertFalse(done.is_error, done.content)
        rule = self.env["base.automation"].search([("name", "=", "Welcome the customer")])
        action = rule.action_server_ids
        self.assertEqual((action.state, action.mail_post_method), ("mail_post", "email"))
        template = action.template_id
        self.assertEqual(template.model, "res.partner")
        self.assertEqual(template.email_to, "{{ object.email }},sales@nudge.example")
        self.assertEqual(template.email_cc, "{{ object.user_id.partner_id.email_formatted }}")
        self.assertEqual(template.subject, "Welcome, {{ object.name }}")
        self.assertIn("Welcome aboard", template.body_html)

    def test_only_fields_of_the_record_go_between_braces(self):
        if "mail.template" not in self.env:
            self.skipTest("Discuss is not installed")
        self.switch(True)
        for bad in ("{{ object.env.cr }}", "{{ object.__class__ }}", "{{ user.password }}", "{{ 1 + 1 }}"):
            refused = self.run_tool("prepare_email_template", {
                "name": "Bad", "model": "res.partner",
                "email": {"to": ["email"], "subject": "Hi", "body": "Hello " + bad}})
            self.assertTrue(refused.is_error, bad)
        refused = self.run_tool("prepare_email_template", {
            "name": "Bad", "model": "res.partner",
            "email": {"to": ["color"], "subject": "Hi", "body": "Hello"}})
        self.assertTrue(refused.is_error)
        self.assertIn("neither an address nor a field", refused.content)

    def test_an_email_template_alone(self):
        if "mail.template" not in self.env:
            self.skipTest("Discuss is not installed")
        self.switch(True)
        result = self.run_tool("prepare_email_template", {
            "name": "Statement", "model": "res.partner",
            "email": {"to": ["self"], "subject": "Your statement", "body": "<p>Attached.</p>"}})
        self.assertFalse(result.is_error, result.content)
        done = self.do("prepare_email_template", result)
        self.assertFalse(done.is_error, done.content)
        template = self.env["mail.template"].search([("name", "=", "Statement")])
        self.assertEqual(template.partner_to, "{{ object.self.id }}")
        self.assertEqual(template.body_html, "<p>Attached.</p>")
        # Odoo's own sending goes to the people it names, not the record's default recipients
        self.assertFalse(template.use_default_to)

    # ------------------------------------------------------------- server actions
    def test_a_server_action_in_the_actions_menu(self):
        self.switch(True)
        result = self.run_tool("prepare_server_action", {
            "name": "Mark as buyer", "model": "res.partner", "in_menu": True,
            "action": {"kind": "update", "field": "function", "value": "Buyer"}})
        self.assertFalse(result.is_error, result.content)
        lines = {line["label"]: line["value"] for line in result.pending["preview"]["lines"] if "value" in line}
        self.assertEqual(lines["Shown in"], "the Actions menu of Contact")
        self.assertEqual(lines["Job Position"], "Buyer")
        done = self.do("prepare_server_action", result)
        self.assertFalse(done.is_error, done.content)
        action = self.env["ir.actions.server"].search([("name", "=", "Mark as buyer")])
        self.assertEqual((action.state, action.update_path, action.value), ("object_write", "function", "Buyer"))
        self.assertEqual(action.binding_model_id.model, "res.partner")
        # It does what it says
        partner = self.env["res.partner"].create({"name": "Nudge Menu"})
        action.with_context(active_model="res.partner", active_ids=partner.ids).run()
        self.assertEqual(partner.function, "Buyer")

        refused = self.run_tool("prepare_server_action", {
            "name": "Bad", "model": "res.partner",
            "action": {"kind": "update", "field": "country_id", "value": "Belgium"}})
        self.assertTrue(refused.is_error)
        self.assertIn("code", refused.content)

        # A choice is updated too: refused, a small model wrote lang = 'ar' as code instead
        result = self.run_tool("prepare_server_action", {
            "name": "In English", "model": "res.partner",
            "action": {"kind": "update", "field": "lang", "value": "en_US"}})
        self.assertFalse(result.is_error, result.content)
        done = self.do("prepare_server_action", result)
        self.assertFalse(done.is_error, done.content)
        action = self.env["ir.actions.server"].search([("name", "=", "In English")])
        partner.lang = False
        action.with_context(active_model="res.partner", active_ids=partner.ids, active_id=partner.id).run()
        self.assertEqual(partner.lang, "en_US")
        # Tried on a dummy record given no name: a contact needs one
        tried = self.run_tool("try_automation", {"action_id": action.id})
        if tried.pending:
            tried = self.do("try_automation", tried)
        self.assertFalse(tried.is_error, tried.content)
        self.assertIn("Nudge trial", tried.content)

    def test_an_expression_is_computed(self):
        self.switch(True)
        result = self.run_tool("prepare_server_action", {
            "name": "Upper", "model": "res.partner",
            "action": {"kind": "update", "field": "function", "expression": "record.name.upper()"}})
        self.assertFalse(result.is_error, result.content)
        done = self.do("prepare_server_action", result)
        action = self.env["ir.actions.server"].search([("name", "=", "Upper")])
        self.assertEqual(action.evaluation_type, "equation")
        partner = self.env["res.partner"].create({"name": "lower"})
        action.with_context(active_model="res.partner", active_ids=partner.ids).run()
        self.assertEqual(partner.function, "LOWER")

    def test_a_value_or_an_expression_never_both(self):
        # "Archive them": Gemini sent false, and active = record.action_archive() with it
        self.switch(True)
        refused = self.run_tool("prepare_server_action", {
            "name": "Archive", "model": "res.partner",
            "action": {"kind": "update", "field": "active", "value": "false",
                       "expression": "record.action_archive()"}})
        self.assertTrue(refused.is_error)
        self.assertIn("not both", refused.content)
        archive = self.run_tool("prepare_server_action", {
            "name": "Archive", "model": "res.partner",
            "action": {"kind": "update", "field": "active", "value": "false", "expression": ""}})
        self.assertFalse(archive.is_error, archive.content)

    def test_labels_are_in_the_language_of_the_person(self):
        # In Arabic the card said "Customer › Email": field.string is the English of the code
        from odoo import fields  # noqa: PLC0415

        from odoo.addons.nudge.tools import developer  # noqa: PLC0415

        self.patch(fields.Field, "_description_string", lambda field, env: f"«{field.string}»")
        self.assertEqual(developer.path_label(self.env["res.partner"], "user_id.email"), "«Salesperson» › «Email»")

    # ------------------------------------------------------------- stopping, deleting
    def test_switched_off_then_deleted(self):
        self.switch(True)
        action = self.env["ir.actions.server"].create({
            "name": "Nudge to stop", "model_id": self.env["ir.model"]._get("res.partner").id,
            "state": "code", "code": "pass", "binding_model_id": self.env["ir.model"]._get("res.partner").id})
        # Out of the Actions menu, with the tool every change goes through
        result = self.run_tool("prepare_write", {
            "model": "ir.actions.server", "ids": [action.id],
            "values": [{"field": "binding_model_id", "value": "none"}]})
        self.assertFalse(result.is_error, result.content)
        tools.base.TOOLS["prepare_write"].execute(self.dev_env(), SimpleNamespace(
            values=result.pending["values"], res_model="ir.actions.server", res_ids=[action.id]))
        self.assertFalse(action.binding_model_id)

        listed = self.run_tool("read_automations", {"words": "Nudge to stop"})
        self.assertIn("Nudge to stop", listed.content)

        refused = self.run_tool("prepare_delete", {"model": "res.partner", "ids": [1]})
        self.assertTrue(refused.is_error, "Records of the business are never deleted")
        result = self.run_tool("prepare_delete", {"model": "ir.actions.server", "ids": [action.id]})
        self.assertFalse(result.is_error, result.content)
        self.assertTrue(result.pending["is_sensitive"])
        self.assertEqual(result.pending["summary"], "Delete: Nudge to stop")
        self.assertTrue(action.exists(), "Nothing before the yes")
        done = self.do("prepare_delete", result)
        self.assertFalse(done.is_error, done.content)
        self.assertFalse(action.exists())
        # Not without the mode: what the conversation would tell the model as a refusal
        with self.assertRaises(Refused):
            tools.base.TOOLS["prepare_delete"].execute(
                self.env(user=self.admin), SimpleNamespace(values={}, res_model="ir.actions.server", res_ids=[1]))

    def test_what_runs_by_itself_is_read(self):
        if not self.has_rules:
            self.skipTest("Automation Rules is not installed")
        self.switch(True)
        model = self.env["ir.model"]._get("res.partner")
        self.env["base.automation"].create({
            "name": "Nudge existing rule", "model_id": model.id, "trigger": "on_create",
            "action_server_ids": [(0, 0, {"name": "Say so", "model_id": model.id, "state": "code",
                                          "code": "log('created')", "usage": "base_automation"})]})
        listed = self.run_tool("read_automations", {"model": "res.partner"})
        self.assertFalse(listed.is_error, listed.content)
        self.assertIn("Nudge existing rule on res.partner: when a record is created", listed.content)
        self.assertIn("log('created')", listed.content)
        self.assertTrue(self.run_tool("read_automations", {}).is_error)

    # ------------------------------------------------------------- trying
    RULE = {
        "name": "Mark buyers", "model": "res.partner", "trigger": "on_create_or_write",
        "fields": ["function"],
        "conditions": [{"field": "function", "operator": "contains", "value": "Buyer"}],
        "actions": [{"kind": "code", "code": "for r in records:\n    r.write({'comment': 'A buyer'})"}],
    }

    def counts(self):
        Rule = self.env["base.automation"] if self.has_rules else None
        return (
            Rule.search_count([]) if Rule else 0,
            self.env["res.partner"].search_count([]),
            self.env["ir.actions.server"].search_count([]),
            self.env["mail.mail"].sudo().search_count([]) if "mail.mail" in self.env else 0,
        )

    def test_a_rule_is_tried_on_a_dummy_record_and_nothing_is_kept(self):
        if not self.has_rules:
            self.skipTest("Automation Rules is not installed")
        self.switch(True)
        before = self.counts()
        asked = self.run_tool("try_automation", {
            "rule": self.RULE,
            "record": [{"field": "name", "value": "Dummy (Nudge)"}],
            "change": [{"field": "function", "value": "Buyer"}],
        })
        self.assertFalse(asked.is_error, asked.content)
        self.assertTrue(asked.pending["is_sensitive"], "Code runs only after a yes")
        code = [line for line in asked.pending["preview"]["lines"] if line.get("code")]
        self.assertIn("r.write", code[0]["value"])
        self.assertEqual(self.counts(), before, "Nothing ran before the yes")
        result = self.do("try_automation", asked)
        self.assertFalse(result.is_error, result.content)
        self.assertIn("dummy Contact", result.content)
        self.assertIn("The conditions match", result.content)
        self.assertIn("Changed by the actions: Notes: None → 'A buyer'", result.content)
        self.assertNotIn("Job Position", result.content, "What the trial wrote is not what the actions did")
        self.assertIn("Nothing was kept", result.content)
        self.assertEqual(self.counts(), before)
        self.assertFalse(self.env["res.partner"].search([("name", "=", "Dummy (Nudge)")]))
        # A record that does not match is left alone, and said so
        result = self.do("try_automation", self.run_tool("try_automation", {
            "rule": self.RULE,
            "record": [{"field": "name", "value": "Dummy (Nudge)"}],
            "change": [{"field": "function", "value": "Seller"}],
        }))
        self.assertIn("do NOT match", result.content)
        self.assertIn("No field of the dummy record was changed", result.content)
        self.assertEqual(self.counts(), before)

    def test_a_rule_that_fails_says_so(self):
        if not self.has_rules:
            self.skipTest("Automation Rules is not installed")
        self.switch(True)
        before = self.counts()
        rule = dict(self.RULE, trigger="on_create", fields=[], conditions=[],
                    actions=[{"kind": "code", "code": "raise UserError('Stop right there')"}])
        result = self.do("try_automation", self.run_tool("try_automation", {"rule": rule, "record": [{"field": "name", "value": "Dummy"}]}))
        self.assertFalse(result.is_error, result.content)
        self.assertIn("It failed", result.content)
        self.assertIn("Stop right there", result.content)
        self.assertIn("Nothing was kept", result.content)
        self.assertEqual(self.counts(), before)
        # The rules in force are untouched by the trial
        self.env["res.partner"].create({"name": "After the trial"})

    def test_an_email_rule_is_tried(self):
        if not self.has_rules or "mail.template" not in self.env:
            self.skipTest("Automation Rules or Discuss is not installed")
        self.switch(True)
        before = self.counts()
        rule = dict(self.RULE, name="Welcome", trigger="on_create", fields=[], conditions=[], actions=[{
            "kind": "email", "email": {"to": ["email"], "subject": "Welcome {{ object.name }}", "body": "Hi"}}])
        result = self.run_tool("try_automation", {
            "rule": rule, "record": [{"field": "name", "value": "Dummy"}, {"field": "email", "value": "dummy@nudge.example"}]})
        self.assertFalse(result.is_error, result.content)
        self.assertIn("Emails that would be sent: 1", result.content)
        self.assertIn("'Welcome Dummy' to dummy@nudge.example", result.content)
        self.assertEqual(self.counts(), before, "The email was discarded with the rest")

    def test_what_exists_is_tried_too(self):
        self.switch(True)
        model = self.env["ir.model"]._get("res.partner")
        action = self.env["ir.actions.server"].create({
            "name": "Nudge trial action", "model_id": model.id, "state": "object_write",
            "update_path": "function", "evaluation_type": "value", "value": "Tried"})
        before = self.counts()
        result = self.run_tool("try_automation", {"action_id": action.id, "record": [{"field": "name", "value": "Dummy"}]})
        self.assertFalse(result.is_error, result.content)
        self.assertIn("Job Position: None → 'Tried'", result.content)
        self.assertEqual(self.counts(), before)
        self.assertTrue(self.run_tool("try_automation", {}).is_error)
        # A rule is checked as prepare_automation would check it
        invalid = self.run_tool("try_automation", {"rule": {"name": "Half a rule", "model": "res.partner"}})
        self.assertTrue(invalid.is_error)
        self.assertIn("Invalid rule", invalid.content)
        self.assertIn("trigger", invalid.content)
        self.assertTrue(self.run_tool("try_automation", {"action_id": action.id, "cron_id": 1}).is_error)

    # ------------------------------------------------------------- scheduled actions
    def test_a_scheduled_action_is_made_tried_and_deleted(self):
        self.switch(True)
        result = self.run_tool("prepare_scheduled_action", {
            "name": "Nightly buyers", "model": "res.partner",
            "code": "model.search([('function', '=', 'Buyer')]).write({'comment': 'Checked tonight'})",
            "every": {"number": 1, "unit": "days"}, "first_run": "now"})
        self.assertFalse(result.is_error, result.content)
        self.assertTrue(result.pending["is_sensitive"])
        lines = {line["label"]: line for line in result.pending["preview"]["lines"]}
        self.assertEqual(lines["Every"]["value"], "day")
        self.assertTrue(lines["Code"]["code"])
        self.assertFalse(self.env["ir.cron"].search([("cron_name", "=", "Nightly buyers")]), "Nothing before the yes")
        done = self.do("prepare_scheduled_action", result)
        self.assertFalse(done.is_error, done.content)
        cron = self.env["ir.cron"].search([("cron_name", "=", "Nightly buyers")])
        self.assertEqual((cron.interval_number, cron.interval_type, cron.user_id), (1, "days", self.admin))
        self.assertEqual((cron.ir_actions_server_id.state, cron.ir_actions_server_id.usage), ("code", "ir_cron"))
        self.assertIn("Checked tonight", cron.ir_actions_server_id.code)

        buyer = self.env["res.partner"].create({"name": "Nudge Night Buyer", "function": "Buyer"})
        asked = self.run_tool("try_automation", {"cron_id": cron.id})
        self.assertTrue(asked.pending, "A scheduled action runs code: a yes first")
        tried = self.do("try_automation", asked)
        self.assertFalse(tried.is_error, tried.content)
        self.assertIn("ran once", tried.content)
        self.assertFalse(buyer.comment, "What the trial wrote was discarded")

        listed = self.run_tool("read_automations", {"model": "res.partner"})
        self.assertIn("Nightly buyers on res.partner: every 1 days", listed.content)
        refused = self.run_tool("prepare_scheduled_action", {
            "name": "Broken", "model": "res.partner", "code": "model.search(", "every": {"number": 1, "unit": "days"},
            "first_run": "now"})
        self.assertTrue(refused.is_error)
        self.assertIn("does not compile", refused.content)
        self.assertIn("Nothing was made", refused.content, "A refusal is never told as done")
        # When it first runs is always said: "every Monday" ran every 7 days from now
        unsaid = self.run_tool("prepare_scheduled_action", {
            "name": "Monday", "model": "res.partner", "code": "log('x')", "every": {"number": 1, "unit": "weeks"}})
        self.assertIn("'first_run' is missing", unsaid.content)

        result = self.run_tool("prepare_delete", {"model": "ir.cron", "ids": [cron.id]})
        self.assertFalse(result.is_error, result.content)
        behind = cron.ir_actions_server_id
        self.do("prepare_delete", result)
        self.assertFalse(cron.exists())
        self.assertFalse(behind.exists(), "Its code goes with it: Odoo keeps the server action behind")

    def test_what_a_small_model_writes_wrong_is_read_or_shown(self):
        from odoo.addons.nudge.tools import developer  # noqa: PLC0415

        # A recipient written as the placeholder itself
        written = developer.read_recipients(self.env, self.env["res.partner"], ["{{ object.email }}"])
        self.assertIn("{{ object.email }}", str(written))
        # An update with code and no field is shown the two shapes
        self.switch(True)
        refused = self.run_tool("prepare_server_action", {
            "name": "Archive", "model": "res.partner",
            "action": {"kind": "update", "code": "record.write({'active': False})"}})
        self.assertTrue(refused.is_error)
        self.assertIn('"kind": "code"', refused.content)

    def test_code_that_would_fail_or_do_nothing_is_refused(self):
        from odoo.addons.nudge.tools.developer import compile_code  # noqa: PLC0415

        # What qwen3.5:4b wrote, and the cards said yes to
        self.assertIn("only opens a window", compile_code("action = dict(active='False')", self.env))
        self.assertIn("no model 'ir.lang'", compile_code("env['ir.lang'].search([])", self.env))
        self.assertIn("datetime.datetime.now()", compile_code("record.write({'comment': datetime.now()})", self.env))
        self.assertIn("record.write(", compile_code("record.active = False", self.env))
        self.assertIn("changes nothing", compile_code("names = list(records.ids)", self.env))
        # What is right passes
        for code in ("records.write({'active': False})", "log('checked')",
                     "action = {'type': 'ir.actions.act_window', 'res_model': 'res.partner', 'views': [[False, 'list']]}",
                     "env['res.lang'].search([]).mapped('code')\nlog('ok')",
                     "record.write({'comment': str(datetime.datetime.now())})"):
            self.assertEqual(compile_code(code, self.env), "", code)

    def test_code_that_reads_only_or_writes_what_is_not_there_is_refused(self):
        # What the trial's model wrote: a search alone, a condition in two parts, a
        # language by a code that is not one
        self.assertIn("changes nothing", compile_code(
            "old = env['res.partner'].search([('create_date', '<=', datetime.datetime.now() - "
            "datetime.timedelta(days=7))])\nnames = old.mapped('name')", self.env, "res.partner"))
        shape = compile_code(
            "records.search([('create_date <=', datetime.datetime.now())]).write({'comment': 'x'})",
            self.env, "res.partner")
        self.assertIn("('create_date <=', datetime.datetime.now()), which is not a condition", shape)
        self.assertIn("(field, operator, value)", shape)
        self.assertIn("res.partner has no field 'langue'",
                      compile_code("record.write({'langue': 'en_US'})", self.env, "res.partner"))
        lang = compile_code("for partner in records:\n    partner.write({'lang': 'ar'})", self.env, "res.partner")
        self.assertIn("lang is a choice, and 'ar' is not one of its keys", lang)
        self.assertIn("'en_US'", lang)
        self.assertIn('{"kind": "update", "field": "active", "value": false}', compile_code("record.active = False", self.env))
        # What is right passes; records of another model are not judged by the model of the action
        for code in ("record.write({'lang': 'en_US'})", "record.write(dict(comment='x', lang=False))",
                     "records.filtered(lambda one: not one.comment).write({'comment': 'x'})",
                     "for user in record.user_ids:\n    user.write({'signature': 'x'})",
                     "env['res.users'].search(['|', ('share', '=', False), ('active', '=', True)], limit=1)"
                     ".write({'signature': 'x'})"):
            self.assertEqual(compile_code(code, self.env, "res.partner"), "", code)
        # The model of the action is checked where the card is made
        self.switch(True)
        refused = self.run_tool("prepare_server_action", {
            "name": "In Arabic", "model": "res.partner",
            "action": {"kind": "code", "code": "record.write({'lang': 'ar'})"}})
        self.assertTrue(refused.is_error)
        self.assertIn("lang is a choice", refused.content)

    def test_a_server_action_is_deleted_with_its_button(self):
        self.switch(True)
        partner_model = self.env["ir.model"]._get("res.partner")
        action = self.env["ir.actions.server"].create({
            "name": "Nudge Archive", "model_id": partner_model.id, "state": "code", "code": "records.action_archive()"})
        button = self.run_tool("prepare_button", {"model": "res.partner", "label": "Archive it", "action_id": action.id})
        self.assertFalse(button.is_error, button.content)
        made = self.do("prepare_button", button)
        self.assertFalse(made.is_error, made.content)
        view = self.env["ir.ui.view"].browse(made.records[1])
        # A view that holds other changes too, as Studio makes them: only the button leaves it
        studio = self.env["ir.ui.view"].create({
            "name": "Nudge Studio-like", "type": "form", "model": "res.partner",
            "inherit_id": view.inherit_id.id, "mode": "extension",
            "arch_db": f'<data><xpath expr="//sheet" position="before"><header><button name="{action.id}" '
                       'type="action" string="Archive too"/></header></xpath><xpath expr="//field[@name=\'name\']" '
                       'position="attributes"><attribute name="placeholder">Nudge kept</attribute></xpath></data>'})
        result = self.run_tool("prepare_delete", {"model": "ir.actions.server", "ids": [action.id]})
        self.assertFalse(result.is_error, result.content)
        lines = {line["label"]: line["value"] for line in result.pending["preview"]["lines"]}
        self.assertIn(view.name, lines["Views"], "The card says the button goes")
        self.assertTrue(action.exists() and view.exists(), "Nothing before the yes")
        done = self.do("prepare_delete", result)
        self.assertFalse(done.is_error, done.content)
        self.assertFalse(action.exists())
        self.assertFalse(view.exists(), "The button left the form with the action it ran")
        self.assertTrue(studio.exists())
        self.assertNotIn(f'name="{action.id}"', studio.arch_db)
        self.assertIn("Nudge kept", studio.arch_db)

    def test_automations_are_found_by_any_of_their_words(self):
        # "contact archive" found nothing named "Archive contact"
        self.switch(True)
        partner_model = self.env["ir.model"]._get("res.partner")
        Action = self.env["ir.actions.server"]
        Action.create({"name": "Nudge contact birthday", "model_id": partner_model.id, "state": "code", "code": "pass"})
        Action.create({"name": "Archive Nudge contact", "model_id": partner_model.id, "state": "code", "code": "pass"})
        listed = self.run_tool("read_automations", {"words": "nudge contact archive"})
        self.assertFalse(listed.is_error, listed.content)
        self.assertIn("Archive Nudge contact", listed.content)
        self.assertIn("Nudge contact birthday", listed.content)
        self.assertLess(listed.content.index("Archive Nudge contact"), listed.content.index("Nudge contact birthday"),
                        "Named with every word comes first")

    def test_a_new_server_action_says_its_id_for_a_button(self):
        # prepare_button was called with action_id 1 before the action was made
        self.switch(True)
        result = self.run_tool("prepare_server_action", {
            "name": "Nudge Buyer", "model": "res.partner",
            "action": {"kind": "update", "field": "function", "value": "Buyer"}})
        self.assertFalse(result.is_error, result.content)
        self.assertIn("no id before their yes", result.content)
        done = self.do("prepare_server_action", result)
        self.assertFalse(done.is_error, done.content)
        action = self.env["ir.actions.server"].search([("name", "=", "Nudge Buyer")])
        self.assertIn(f"Its id is {action.id}: a button that runs it is made with prepare_button, "
                      f"action_id {action.id}.", done.content)

    def test_a_trial_given_nothing_tries_what_was_made_last(self):
        self.switch(True)
        conversation = self.env["nudge.conversation"].with_user(self.admin)._chat_open()
        context = {"nudge_mode": "dev", "nudge_conversation_id": conversation.id}
        refused = self.run_tool("try_automation", {}, context=context)
        self.assertTrue(refused.is_error)
        self.assertIn("nothing made in this conversation", refused.content)
        run = self.env["nudge.run"].sudo().create({
            "name": "Build", "conversation_id": conversation.id, "user_id": self.admin.id, "is_developer": True})
        action = self.env["ir.actions.server"].create({
            "name": "Nudge Job", "model_id": self.env["ir.model"]._get("res.partner").id, "state": "object_write",
            "update_path": "function", "evaluation_type": "value", "value": "Buyer"})
        self.env["nudge.step"].sudo().create({
            "run_id": run.id, "tool": "prepare_server_action", "kind": "create", "state": "done",
            "res_model": "ir.actions.server", "res_ids": action.ids})
        tried = self.run_tool("try_automation", {}, context=context)
        self.assertFalse(tried.is_error, tried.content)
        self.assertIn(f"the server action #{action.id} 'Nudge Job', made last in this conversation, is tried",
                      tried.content)
        self.assertIn("Buyer", tried.content)

    def test_the_instructions_quote_no_trial(self):
        # The trial's own sentences in what the model reads: its results told nothing of real use
        from odoo.addons.nudge.llm import ollama  # noqa: PLC0415

        told = [ollama.PRODUCT_PROMPT, *(tool.description for tool in tools.base.TOOLS.values())]
        for mode in ("talk", "agent", "dev"):
            for compact in (False, True):
                told += prompt.build_system_blocks(
                    mode=mode, can_use_agent=True, can_develop=True, is_administrator=True, compact=compact)
        told = "\n".join(told)
        for sentence in ("Cabinet, large", "two doors", "Raghad", "older than 30 days", "remind the salespeople",
                         "quotation is confirmed", "every morning at 8", "Follow up this quotation", "رجّع كل شي"):
            self.assertNotIn(sentence.lower(), told.lower(), sentence)

    def test_developer_mode_deletes_what_it_built(self):
        # "Delete the rule" was refused: Developer mode was told "you never delete" too
        for compact in (False, True):
            dev = "\n".join(prompt.build_system_blocks(
                mode="dev", can_use_agent=True, can_develop=True, is_administrator=True, compact=compact))
            agent = "\n".join(prompt.build_system_blocks(
                mode="agent", can_use_agent=True, can_develop=True, is_administrator=True, compact=compact))
            self.assertNotIn("You never delete", dev)
            self.assertIn("prepare_delete", dev)
            self.assertIn("You never delete", agent)

    def test_a_choice_by_the_start_of_its_key_or_of_its_label(self):
        # "ar" and "Arabic" were refused for the language "Arabic / الْعَرَبيّة" (ar_001)
        from odoo.addons.nudge.tools.records import read_choice  # noqa: PLC0415

        self.env["res.lang"]._activate_lang("ar_001")
        field = self.env["res.partner"]._fields["lang"]
        for text in ("ar", "Arabic", "arabic", "ar_001"):
            self.assertEqual(read_choice(text, field, self.env), "ar_001", text)
        english = [key for key, _label in field._description_selection(self.env) if key.startswith("en_")]
        if english == ["en_US"]:
            self.assertEqual(read_choice("en", field, self.env), "en_US")
        with self.assertRaises(Refused):
            read_choice("a", field, self.env)
        self.switch(True)
        result = self.run_tool("prepare_server_action", {
            "name": "In Arabic", "model": "res.partner",
            "action": {"kind": "update", "field": "lang", "value": "Arabic"}})
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.pending["values"]["action"]["value"], "ar_001")

    def test_a_button_asked_for_is_not_said_done_before_it_is_there(self):
        # The action of the button was made, and a small model told the person the
        # button was on the form: the yes said "tell them in a sentence what was done"
        from .test_agent import asking, call, saying  # noqa: PLC0415

        self.switch(True)
        adapter = self.use_adapter(script=[
            asking(call("prepare_server_action", {
                "name": "Nudge Archive", "model": "res.partner",
                "action": {"kind": "update", "field": "active", "value": "false"}})),
            saying("Now the button."),
        ])
        conversation = self.env["nudge.conversation"].with_user(self.admin)._chat_open()
        job = conversation._chat_prepare("Add an Archive button on the contact form", mode="dev")
        list(loop.run(job, conversation._chat_record))
        self.decide(conversation)
        action = self.env["ir.actions.server"].search([("name", "=", "Nudge Archive")])
        told = adapter.calls[1]["turns"][-1]["results"][0]["content"]
        self.assertIn(f"Not finished: the button is not on the form yet. Call prepare_button now: "
                      f"action_id {action.id}, model res.partner.", told)
        self.assertNotIn("Tell them", told)

    def test_an_unfinished_yes_is_gone_on_with_even_after_a_question(self):
        # Told "Call prepare_button now", a small model asked the person a question instead
        from .test_agent import asking, call, saying  # noqa: PLC0415

        self.switch(True)
        adapter = self.use_adapter(script=[
            asking(call("prepare_server_action", {
                "name": "Nudge Archive", "model": "res.partner",
                "action": {"kind": "update", "field": "active", "value": "false"}})),
            saying("Which label should the button have?"),
            saying("I cannot."),
        ])
        conversation = self.env["nudge.conversation"].with_user(self.admin)._chat_open()
        job = conversation._chat_prepare("Add an Archive button on the contact form", mode="dev")
        list(loop.run(job, conversation._chat_record))
        self.decide(conversation)
        self.assertEqual(len(adapter.calls), 3, "Pushed once to make the button")
        self.assertEqual(adapter.calls[2]["turns"][-1]["text"], loop.PUSH_NOTE)
        # A yes to what finished the work leaves a question as it is
        adapter = self.use_adapter(script=[
            asking(call("prepare_server_action", {
                "name": "Nudge Job", "model": "res.partner",
                "action": {"kind": "update", "field": "function", "value": "Buyer"}})),
            saying("Anything else?"),
        ])
        conversation = self.env["nudge.conversation"].with_user(self.admin)._chat_open()
        job = conversation._chat_prepare("Make an action that sets the job to Buyer", mode="dev")
        list(loop.run(job, conversation._chat_record))
        self.decide(conversation)
        self.assertEqual(len(adapter.calls), 2)

    def test_an_undo_that_went_through_reads_as_done(self):
        # The card of an undo that deleted the one rule made read "Undo: Not possible:
        # deleted for good" and "Not undone: What was changed rather than made"
        from .test_agent import asking, call, saying  # noqa: PLC0415

        self.switch(True)
        self.use_adapter(script=[
            asking(call("prepare_server_action", {
                "name": "Nudge Undo Me", "model": "res.partner",
                "action": {"kind": "update", "field": "function", "value": "Buyer"}})),
            saying("Made."),
            asking(call("prepare_undo", {})),
            saying("Undone."),
        ])
        conversation = self.env["nudge.conversation"].with_user(self.admin)._chat_open()
        job = conversation._chat_prepare("Make an action that sets the job to Buyer", mode="dev")
        list(loop.run(job, conversation._chat_record))
        self.decide(conversation)
        self.assertTrue(self.env["ir.actions.server"].search([("name", "=", "Nudge Undo Me")]))

        job = conversation._chat_prepare("Undo what you built", mode="dev")
        list(loop.run(job, conversation._chat_record))
        step = conversation.sudo().waiting_step_id
        self.assertEqual(step.tool, "prepare_undo")
        # Before the yes: what deleting means, and nothing about exceptions there are none of
        self.assertEqual(step._get_card()["lines"], [
            {"label": "Deleted", "value": "For good: it cannot be brought back"}])

        events = self.decide(conversation)
        decided = next(event for event in events if event["type"] == "decided")
        self.assertEqual(decided["state"], "done")
        self.assertEqual(decided["lines"], [{"label": "Deleted", "value": "All of it"}])
        self.assertFalse(self.env["ir.actions.server"].search([("name", "=", "Nudge Undo Me")]))
        # Read again later, the card says the same
        card = self.env["nudge.step"].with_user(self.admin).get_cards(conversation.id)[step.message_id.id]
        self.assertEqual(card["lines"], [{"label": "Deleted", "value": "All of it"}])

    def test_what_an_undo_leaves_as_it_is_is_named(self):
        self.switch(True)
        conversation = self.env["nudge.conversation"].with_user(self.admin)._chat_open()
        run = self.env["nudge.run"].sudo().create({
            "name": "Build", "conversation_id": conversation.id, "user_id": self.admin.id, "is_developer": True})
        env = self.env(user=self.admin, context={"nudge_mode": "dev", "nudge_conversation_id": conversation.id})
        made = tools.base.call(env, "prepare_server_action", {
            "name": "Nudge Undo Too", "model": "res.partner",
            "action": {"kind": "update", "field": "function", "value": "Buyer"}}, None, "dev")
        done = self.do("prepare_server_action", made)
        model, ids, _names = done.records
        Step = self.env["nudge.step"].sudo()
        Step.create({"run_id": run.id, "tool": "prepare_server_action", "kind": "create", "state": "done",
                     "res_model": model, "res_ids": ids})
        # Changed rather than made; and tried on a dummy record, which kept nothing
        Step.create({"run_id": run.id, "tool": "prepare_write", "kind": "write", "state": "done",
                     "res_model": "res.partner", "res_ids": [self.admin.partner_id.id],
                     "summary": "Change: Contact"})
        Step.create({"run_id": run.id, "tool": "try_automation", "kind": "code", "state": "done",
                     "summary": "Try on a dummy record"})

        asked = tools.base.call(env, "prepare_undo", {}, None, "dev")
        self.assertEqual(asked.pending["preview"]["lines"], [
            {"label": "Deleted", "value": "For good: it cannot be brought back"},
            {"label": "Stays as it is", "value": "Change: Contact"}])
        undone = self.do("prepare_undo", asked)
        self.assertFalse(undone.is_error, undone.content)
        self.assertEqual(undone.card_lines, [
            {"label": "Deleted", "value": "All of it"},
            {"label": "Stays as it is", "value": "Change: Contact"}])
        self.assertIn("Left as it is", undone.content)
        self.assertIn("Change: Contact", undone.content)

    def test_empty_code_says_where_the_lines_go(self):
        # A code action came with no code, and "The code is empty." showed no way forward
        told = compile_code("  ", self.env)
        self.assertIn("its lines go in code", told)
        self.assertIn('{"kind": "update", "field": "active", "value": "false"}', told)

    def test_code_that_only_defines_is_refused(self):
        # def archive_contact(record): ... was made and never called: the button archived nothing
        refused = compile_code("def archive_contact(record):\n    record.write({'active': False})", self.env)
        self.assertIn("only defines archive_contact, which nothing calls", refused)
        self.assertIn("no def, no return", compile_code("record.write({'active': False})\nreturn True", self.env))
        self.assertEqual(compile_code(
            "def archive(one):\n    one.write({'active': False})\nfor one in records:\n    archive(one)", self.env), "")

    def test_a_reminder_without_code(self):
        # Eight tries at the code of a reminder, all refused: Nudge writes it
        self.switch(True)
        result = self.run_tool("prepare_scheduled_action", {
            "name": "Nudge companies reminder", "model": "res.partner",
            "every": {"number": 1, "unit": "weeks"}, "first_run": "monday 08:00",
            "remind": {"summary": "Call this company", "older_than_days": 30,
                       "conditions": [{"field": "is_company", "operator": "=", "value": "true"}]}})
        self.assertFalse(result.is_error, result.content)
        lines = {line["label"]: line["value"] for line in result.pending["preview"]["lines"]}
        self.assertEqual(lines["Reminds"], "Salesperson")
        self.assertEqual(lines["Summary"], "Call this company")
        self.assertIn("30 days", lines["Older than"])
        self.assertIn("datetime.timedelta(days=30)", lines["Code"])
        self.assertIn("rec.activity_schedule('mail.mail_activity_data_todo', user_id=rec.user_id.id", lines["Code"])
        self.assertEqual(compile_code(lines["Code"], self.env, "res.partner"), "")
        # Made, and run: once for each, however often it runs
        result = self.run_tool("prepare_scheduled_action", {
            "name": "Nudge reminder now", "model": "res.partner",
            "every": {"number": 1, "unit": "days"}, "first_run": "now",
            "remind": {"summary": "Nudge: call them",
                       "conditions": [{"field": "name", "operator": "=", "value": "Nudge Reminded Co"}]}})
        self.assertFalse(result.is_error, result.content)
        self.do("prepare_scheduled_action", result)
        cron = self.env["ir.cron"].search([("cron_name", "=", "Nudge reminder now")])
        partner = self.env["res.partner"].create({"name": "Nudge Reminded Co", "user_id": self.admin.id})
        cron.ir_actions_server_id.run()
        cron.ir_actions_server_id.run()
        self.assertEqual(partner.activity_ids.mapped("summary"), ["Nudge: call them"])
        self.assertEqual(partner.activity_ids.user_id, self.admin)
        # What is not there is said
        refused = self.run_tool("prepare_scheduled_action", {
            "name": "Nudge bad", "model": "res.partner", "every": {"number": 1, "unit": "days"},
            "first_run": "now", "remind": {"summary": "x x", "user_field": "parent_id"}})
        self.assertIn("user_field is the field of the person reminded", refused.content)
        nothing = self.run_tool("prepare_scheduled_action", {
            "name": "Nudge none", "model": "res.partner", "every": {"number": 1, "unit": "days"},
            "first_run": "now"})
        self.assertIn("Give code, or for a reminder remind", nothing.content)

    def test_a_reminder_is_shown_the_way(self):
        # mail.send(...) was refused, and nothing showed how a person is reminded
        refused = compile_code("for rec in records:\n    mail.send(rec.user_id, 'Overdue')", self.env, "res.partner")
        self.assertIn("mail is not known", refused)
        self.assertIn("rec.activity_schedule('mail.mail_activity_data_todo'", refused)
        self.assertNotIn("activity_schedule", compile_code("object.write({'comment': 'x'})", self.env))
        described = tools.base.TOOLS["prepare_scheduled_action"].description
        self.assertIn("check_python_code", described)
        self.assertIn("activity_schedule", described)
        self.assertNotIn("run_python", described)

    def test_deleting_what_is_not_there_is_said(self):
        # prepare_delete of an id that does not exist failed with KeyError: 'unlink'
        self.switch(True)
        refused = self.run_tool("prepare_delete", {"model": "ir.actions.server", "ids": [987654]})
        self.assertTrue(refused.is_error)
        self.assertIn("cannot delete them", refused.content)

    def test_a_trial_tries_what_is_asked_only(self):
        from odoo.addons.nudge.tools.actions import writable_field  # noqa: PLC0415
        from odoo.addons.nudge.tools.records import Refused  # noqa: PLC0415

        # "When the quotation is confirmed" is tried by setting what Odoo fills, discarded after
        partners = self.env["res.partner"]
        with self.assertRaises(Refused):
            writable_field(partners, "commercial_partner_id")
        self.assertTrue(writable_field(partners, "commercial_partner_id", trial=True))
        if "base.automation" not in self.env:
            self.skipTest("No automation rules here")
        self.switch(True)
        model = self.env["ir.model"]._get("res.partner")
        broken = self.env["base.automation"].create({
            "name": "Broken old rule", "model_id": model.id, "trigger": "on_create",
            "action_server_ids": [(0, 0, {"name": "Fails", "model_id": model.id, "state": "code",
                                          "code": "raise UserError('the old rule')"})]})
        tried = self.env["base.automation"].create({
            "name": "Mark buyers", "model_id": model.id, "trigger": "on_create",
            "action_server_ids": [(0, 0, {"name": "Buyer", "model_id": model.id, "state": "code",
                                          "code": "record.write({'function': 'Buyer'})"})]})
        result = self.run_tool("try_automation", {"rule_id": tried.id})
        if result.pending:
            result = self.do("try_automation", result)
        self.assertNotIn("the old rule", result.content, "Another rule of the model failed the trial")
        self.assertIn("Broken old rule", result.content, "Said to be off for the trial")
        self.assertIn("Buyer", result.content)
        self.assertTrue(broken.active, "Back on after the trial")
        # A scheduled action says what it made
        cron = self.env["ir.cron"].create({
            "name": "Note on a contact", "model_id": model.id, "state": "code",
            "code": "model.search([], limit=1).message_post(body='checked')",
            "interval_number": 1, "interval_type": "days"})
        tried = self.run_tool("try_automation", {"cron_id": cron.id})
        if tried.pending:
            tried = self.do("try_automation", tried)
        self.assertIn("Messages it posted: 1", tried.content)

    def test_an_expression_that_is_a_value(self):
        # "expression": "false" failed with NameError when run
        self.switch(True)
        result = self.run_tool("prepare_server_action", {
            "name": "Archive it", "model": "res.partner",
            "action": {"kind": "update", "field": "active", "expression": "false"}})
        self.assertFalse(result.is_error, result.content)
        done = self.do("prepare_server_action", result)
        action = self.env["ir.actions.server"].search([("name", "=", "Archive it")])
        self.assertEqual((action.evaluation_type, action.update_boolean_value), ("value", "false"))
        self.assertTrue(done)
        refused = self.run_tool("prepare_server_action", {
            "name": "Unknown", "model": "res.partner",
            "action": {"kind": "update", "field": "comment", "expression": "note_of_today"}})
        self.assertIn("note_of_today is not known", refused.content)

    def test_every_monday_starts_on_a_monday(self):
        from datetime import datetime  # noqa: PLC0415

        from odoo.addons.nudge.tools import developer  # noqa: PLC0415

        self.admin.tz = "Asia/Damascus"
        env = self.env(user=self.admin)
        # Sunday 11 October 2026, 09:00 in Damascus (06:00 universal)
        sunday = datetime(2026, 10, 11, 6, 0)
        self.assertEqual(developer.next_weekday(env, "monday 08:00", sunday), datetime(2026, 10, 12, 5, 0))
        self.assertEqual(developer.next_weekday(env, "Monday", sunday), datetime(2026, 10, 12, 5, 0))
        # Today, the hour gone: next week; the hour to come: today
        self.assertEqual(developer.next_weekday(env, "sunday 08:00", sunday), datetime(2026, 10, 18, 5, 0))
        self.assertEqual(developer.next_weekday(env, "sunday 17:30", sunday), datetime(2026, 10, 11, 14, 30))
        self.assertIsNone(developer.next_weekday(env, "2026-10-12 08:00", sunday))
        with self.assertRaises(developer.Refused):
            developer.next_weekday(env, "monday morning", sunday)
        self.assertEqual(developer.every_text(env, 2, "weeks"), "2 weeks")

        self.switch(True)
        result = self.run_tool("prepare_scheduled_action", {
            "name": "Monday reminder", "model": "res.partner", "code": "log('monday')",
            "every": {"number": 1, "unit": "weeks"}, "first_run": "monday 08:00"})
        self.assertFalse(result.is_error, result.content)
        lines = {line["label"]: line["value"] for line in result.pending["preview"]["lines"]}
        self.assertEqual(lines["Every"], "week")
        self.assertTrue(lines["First run"].endswith("08:00"))
        first = datetime.strptime(lines["First run"], "%Y-%m-%d %H:%M")
        self.assertEqual(first.weekday(), 0, "A Monday")

    # ------------------------------------------------------------- the conversation
    def test_a_developer_conversation(self):
        self.switch(True)
        adapter = self.use_adapter(script=[[llm.text_event("What should it do?"), llm.stop_event(llm.STOP_END)]])
        conversation = self.env["nudge.conversation"].with_user(self.admin)._chat_open()
        job = conversation._chat_prepare("Build me a rule", mode="dev")
        events = list(loop.run(job, conversation._chat_record))
        self.assertEqual(events[-1]["state"], "complete")
        run = conversation.sudo().run_id or conversation.sudo().run_ids[-1:]
        self.assertTrue(run.is_developer)
        self.assertEqual(conversation.message_ids[0].mode, "dev")
        offered = [tool["name"] for tool in adapter.calls[0]["tools"]]
        self.assertIn("prepare_automation", offered)
        self.assertIn("prepare_write", offered)
        self.assertIn("## Mode: Developer", "\n".join(adapter.calls[0]["system"]))

    def test_the_rules_are_in_the_instructions(self):
        dev = "\n".join(prompt.build_system_blocks(mode="dev"))
        self.assertIn("## Mode: Agent", dev)
        self.assertIn("## Mode: Developer", dev)
        agent = "\n".join(prompt.build_system_blocks(mode="agent", can_develop=True))
        self.assertIn("Developer mode, the third button", agent)
        self.assertNotIn("## Mode: Developer", agent)
        talk = "\n".join(prompt.build_system_blocks(mode="talk", can_use_agent=True, can_develop=True))
        self.assertIn("Developer mode, the third button", talk)
        plain = "\n".join(prompt.build_system_blocks(mode="talk", can_use_agent=True))
        self.assertNotIn("Developer mode, the third button", plain)
        self.assertIn("by an administrator in Nudge's Developer mode", plain)
        # An administrator with the switch off is told how to open it, never an excuse
        off = "\n".join(prompt.build_system_blocks(mode="agent", is_administrator=True))
        self.assertIn("Settings ‣ Nudge ‣ Nudge for Developers", off)
        self.assertIn("Never give another reason", off)
        self.assertIn("approval rule on that button", dev)

    def test_what_a_module_owns_is_never_deleted(self):
        self.switch(True)
        owned = self.env["ir.actions.server"].search([], limit=200).filtered(
            lambda action: (action.get_external_id().get(action.id) or "").split(".")[0] not in ("", "nudge_custom", "__export__"))[:1]
        if not owned:
            self.skipTest("No server action of a module here")
        refused = self.run_tool("prepare_delete", {"model": "ir.actions.server", "ids": owned.ids})
        self.assertTrue(refused.is_error)
        self.assertIn("belongs to the module", refused.content)
        self.assertIn("Switch it off", refused.content)

    def test_a_request_whose_card_was_applied_is_done(self):
        # "Hide the Website field" was applied, the page reloaded for it, and the request read Stopped
        self.switch(True)
        conversation = self.env["nudge.conversation"].with_user(self.admin)._chat_open()
        run = self.env["nudge.run"].sudo().create({
            "name": "Hide", "conversation_id": conversation.id, "user_id": self.admin.id,
            "is_developer": True, "state": "running"})
        Step = self.env["nudge.step"].sudo()
        Step.create({"run_id": run.id, "tool": "read_view", "kind": "read", "state": "done"})
        Step.create({"run_id": run.id, "tool": "prepare_view_change", "kind": "create", "state": "done",
                     "res_model": "ir.ui.view", "res_ids": [1]})
        conversation.sudo().run_id = run
        conversation._chat_prepare("Thanks", mode="dev")
        self.assertEqual(run.state, "done")
        # A card left undecided is a request stopped
        second = self.env["nudge.run"].sudo().create({
            "name": "Hide more", "conversation_id": conversation.id, "user_id": self.admin.id,
            "is_developer": True, "state": "waiting"})
        Step.create({"run_id": second.id, "tool": "prepare_view_change", "kind": "create", "state": "pending"})
        conversation.sudo().write({"run_id": second.id, "stream_started_at": False})
        conversation._chat_prepare("Something else", mode="dev")
        self.assertEqual(second.state, "stopped")
        self.assertEqual(run._outcome("failed"), "done")
        self.assertEqual(self.env["nudge.run"]._outcome("stopped"), "stopped", "Nothing done is no outcome")

    def test_what_was_built_is_undone(self):
        self.switch(True)
        conversation = self.env["nudge.conversation"].with_user(self.admin)._chat_open()
        run = self.env["nudge.run"].sudo().create({
            "name": "Build", "conversation_id": conversation.id, "user_id": self.admin.id, "is_developer": True})
        env = self.env(user=self.admin, context={"nudge_mode": "dev", "nudge_conversation_id": conversation.id})

        def made(tool, arguments):
            result = tools.base.call(env, tool, arguments, None, "dev")
            self.assertFalse(result.is_error, result.content)
            done = tools.base.TOOLS[tool].execute(env, SimpleNamespace(
                values=result.pending["values"], res_model=result.pending["res_model"], res_ids=[]))
            self.assertFalse(done.is_error, done.content)
            model, ids, _names = done.records
            self.env["nudge.step"].sudo().create({
                "run_id": run.id, "tool": tool, "kind": "create", "state": "done", "res_model": model, "res_ids": ids})
            return self.env[model].browse(ids)

        action = made("prepare_server_action", {
            "name": "Undo me", "model": "res.partner", "action": {"kind": "update", "field": "function", "value": "x"}})
        field = made("prepare_field", {"model": "res.partner", "label": "Undo Field", "type": "char"})
        self.assertIn("x_undo_field", self.env["res.partner"]._fields)
        # What a module made is kept, said so
        owned = self.env["ir.actions.server"].search([], limit=200).filtered(
            lambda one: (one.get_external_id().get(one.id) or "").split(".")[0] not in ("", "nudge_custom", "__export__"))[:1]
        if owned:
            self.env["nudge.step"].sudo().create({
                "run_id": run.id, "tool": "prepare_server_action", "kind": "create", "state": "done",
                "res_model": "ir.actions.server", "res_ids": owned.ids})

        asked = tools.base.call(env, "prepare_undo", {}, None, "dev")
        self.assertFalse(asked.is_error, asked.content)
        self.assertTrue(asked.pending["is_sensitive"])
        self.assertIn("Undo Field", asked.pending["record_names"])
        self.assertTrue(action.exists(), "Nothing before the yes")
        done = tools.base.TOOLS["prepare_undo"].execute(env, SimpleNamespace(values=asked.pending["values"], res_model="nudge.run", res_ids=[]))
        self.assertFalse(done.is_error, done.content)
        self.assertFalse(action.exists())
        self.assertFalse(field.exists())
        self.assertNotIn("x_undo_field", self.env["res.partner"]._fields)
        if owned:
            self.assertTrue(owned.exists(), "What a module owns is not Nudge's to undo")
            self.assertNotIn(owned.name, asked.pending["record_names"])
        again = tools.base.call(env, "prepare_undo", {}, None, "dev")
        self.assertTrue(again.is_error, "Nothing left to undo")

        # The button of the request, for its own requests only
        theirs = self.env["nudge.conversation"].with_user(self.agent)._chat_open()
        other = self.env["nudge.run"].sudo().create({
            "name": "Not mine", "conversation_id": theirs.id, "user_id": self.agent.id, "is_developer": True})
        with self.assertRaises(UserError):
            other.with_user(self.admin).action_undo_built()
        notification = run.with_user(self.admin).action_undo_built()
        self.assertEqual(notification["tag"], "display_notification")
