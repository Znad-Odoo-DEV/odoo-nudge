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
        self.do("prepare_delete", result)
        self.assertFalse(cron.exists())

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

    def test_deleting_what_is_not_there_is_said(self):
        # prepare_delete of an id that does not exist failed with KeyError: 'unlink'
        self.switch(True)
        refused = self.run_tool("prepare_delete", {"model": "ir.actions.server", "ids": [987654]})
        self.assertTrue(refused.is_error)
        self.assertIn("cannot delete them", refused.content)

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
