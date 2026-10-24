from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, TransactionCase, tagged

from .test_background import BackgroundCase

NAMED = "[('name', 'ilike', 'Nudge Rule')]"


@tagged("post_install", "-at_install")
class TestTaskRules(BackgroundCase, TransactionCase):
    """A rule counts by itself, as its owner, and never calls a provider."""

    def make(self, **given):
        values = {"name": "Rule", "engine": "rule", "rule_model_id": self.env["ir.model"]._get_id("res.partner"),
                  "rule_domain": NAMED, **given}
        return self.env["nudge.task"].with_user(self.agent).create(values)

    def run_rule(self, task):
        task.sudo().next_run = fields.Datetime.now() - timedelta(minutes=1)
        self.env["nudge.task"]._cron_run()

    def partners(self, *names):
        return self.env["res.partner"].create([{"name": f"Nudge Rule {n}"} for n in names])

    def test_counts_without_a_provider_and_links_the_list(self):
        adapter = self.use_adapter()
        self.partners("A", "B")
        task = self.make()
        self.run_rule(task)
        self.assertEqual(adapter.calls, [], "No provider is asked")
        self.assertIn("- Total: 2", task.last_result)
        self.assertEqual(task.run_count, 1)
        run = task.conversation_id.sudo().run_id
        self.assertEqual((run.state, run.is_background, run.is_unseen), ("done", True, True))
        self.assertIn("#action=", task.last_result)
        # The link opens the list, filtered, for the one who opens it
        server = self.env.ref("nudge.nudge_task_records_action")
        action = server.with_user(self.agent).with_context(active_id=task.id).run()
        self.assertEqual(action["res_model"], "res.partner")
        self.assertEqual(self.env["res.partner"].search_count(action["domain"]), 2)

    def test_the_result_is_said_in_the_language_of_the_owner(self):
        self.env["res.lang"]._activate_lang("ar_001")
        self.agent.lang = "ar_001"
        self.partners("A")
        task = self.make()
        self.run_rule(task)
        self.assertIn("افتح القائمة", task.last_result)
        self.assertNotIn("Open the list", task.last_result)

    def test_group_and_sum(self):
        self.partners("A", "B")
        field = self.env["ir.model.fields"]._get("res.partner", "color")
        by = self.env["ir.model.fields"]._get("res.partner", "active")
        self.env["res.partner"].search([("name", "ilike", "Nudge Rule")]).write({"color": 3})
        task = self.make(rule_measure="sum", rule_field_id=field.id, rule_groupby_id=by.id)
        self.run_rule(task)
        self.assertIn("- Yes: 6", task.last_result)

    def test_threshold(self):
        self.partners("A", "B")
        task = self.make(rule_condition="above", rule_threshold=5)
        self.run_rule(task)
        self.assertEqual((task.run_count, task.skip_count), (0, 1))
        self.assertFalse(task.conversation_id)
        task.rule_threshold = 1
        self.run_rule(task)
        self.assertEqual(task.run_count, 1)
        task.write({"rule_condition": "below", "rule_threshold": 1})
        self.run_rule(task)
        self.assertEqual(task.skip_count, 2)

    def test_changed_since_the_last_run(self):
        self.partners("A")
        task = self.make(rule_condition="changed")
        self.run_rule(task)
        self.assertEqual(task.run_count, 1, "The first run has nothing to compare with")
        self.run_rule(task)
        self.assertEqual((task.run_count, task.skip_count), (1, 1))
        self.partners("B")
        self.run_rule(task)
        self.assertEqual(task.run_count, 2)
        self.assertIn("Before: Total 1", task.last_result)

    def test_email_like_an_ai_task(self):
        self.partners("A")
        task = self.make(send_email=True)
        self.agent.email = "raghad@example.com"
        self.run_rule(task)
        mail = self.env["mail.mail"].sudo().search([("subject", "=", "Nudge: Rule")])
        self.assertEqual(len(mail), 1)

    def test_owner_rights_and_checks(self):
        with self.assertRaises(ValidationError):
            self.make(rule_domain="not a domain")
        with self.assertRaises(ValidationError):
            self.make(rule_measure="sum")
        with self.assertRaises(ValidationError):
            self.env["nudge.task"].with_user(self.agent).create({"name": "No words", "engine": "ai"})
        # A model the owner may not read is not revealed
        with self.assertRaises(ValidationError):
            self.make(rule_model_id=self.env["ir.model"]._get_id("ir.config_parameter"), rule_domain="[]")

    def test_the_model_is_chosen_as_a_model(self):
        task = self.make()
        self.assertEqual(task.model_name, "res.partner")
        task.rule_model_id = self.env["ir.model"]._get_id("res.partner.category")
        self.assertEqual(task.model_name, "res.partner.category")

    def test_an_agent_reads_no_model_row_and_picks_only_what_they_may_read(self):
        # Stock Odoo gives employees no right on these rows: the count of every table, the code of custom fields
        for name in ("ir.model", "ir.model.fields"):
            with self.assertRaises(AccessError):
                self.env[name].with_user(self.agent).search_read([], ["name"], limit=1)
        Model = self.env["ir.model"].with_user(self.agent)
        partner = self.env["ir.model"]._get("res.partner")
        offered = dict(Model.name_search("", [], limit=None))
        self.assertIn(partner.id, offered)
        self.assertNotIn(self.env["ir.model"]._get_id("ir.config_parameter"), offered)
        self.assertIn(partner.id, [line["id"] for line in Model.web_name_search(partner.name, {"display_name": {}})])
        listed = Model.web_search_read([("id", "=", partner.id)], {"name": {}, "count": {}})
        self.assertEqual(listed["records"], [{"id": partner.id, "name": partner.name}])
        # A search on anything but names and types is refused as before: no path to the code of a field
        with self.assertRaises(AccessError):
            Model.name_search("", [("field_id.compute", "!=", False)])
        Fields = self.env["ir.model.fields"].with_user(self.agent)
        color = self.env["ir.model.fields"]._get("res.partner", "color")
        numbers = dict(Fields.name_search("", [("model_id", "=", partner.id), ("ttype", "in", ["integer", "float"])]))
        self.assertIn(color.id, numbers)
        hidden = [name for name, field in self.env["res.partner"]._fields.items()
                  if field.groups and not self.env["res.partner"].with_user(self.agent)._has_field_access(field, "read")]
        names = dict(Fields.name_search("", [("model_id", "=", partner.id)], limit=None))
        for name in hidden:
            self.assertNotIn(self.env["ir.model.fields"]._get("res.partner", name).id, names)
        # The form of a rule, as they fill it in
        with Form(self.env["nudge.task"].with_user(self.agent)) as form:
            form.name = "Raghad's tiles"
            form.engine = "rule"
            form.rule_model_id = partner
            form.rule_measure = "sum"
            form.rule_field_id = color
        self.assertEqual((form.record.model_name, form.record.rule_field_id), ("res.partner", color))

    def test_limit_applies(self):
        self.env["ir.config_parameter"].sudo().set_param("nudge.tasks_per_user", "1")
        self.make()
        with self.assertRaises(UserError):
            self.make(name="Second")

    # ------------------------------------------------------------------ plain users and rights lost
    def test_a_plain_user_makes_no_task_and_picks_no_model(self):
        for user in (self.outsider, self.user_a):
            with self.assertRaises(AccessError):
                self.env["nudge.task"].with_user(user).create({
                    "name": "Raghad rule", "engine": "rule", "rule_domain": "[]",
                    "rule_model_id": self.env["ir.model"]._get_id("res.partner")})

    def test_an_owner_who_lost_the_rights_of_the_model_gets_no_rows_and_no_error(self):
        self.partners("A", "B")
        task = self.make()
        # The record rule of a group: from now on the owner reads no contact
        self.env["ir.rule"].create({
            "name": "Nudge test: Ebrahim reads no contact", "model_id": self.env["ir.model"]._get_id("res.partner"),
            "domain_force": "[('id', '=', 0)]",
            "groups": [(4, self.env.ref("nudge.group_nudge_agent").id)]})
        self.run_rule(task)
        self.assertIn("- Total: 0", task.last_result, "A count of what the owner may read")
        # The model itself is closed to the owner: the run says so in the task, it does not raise
        closed = self.make(name="Closed", rule_model_id=self.env["ir.model"]._get_id("res.partner.category"),
                           rule_domain="[]")
        self.env["ir.model.access"].search([("model_id.model", "=", "res.partner.category")]).write({"perm_read": False})
        self.run_rule(closed)
        self.assertIn("Could not run", closed.last_result)
        self.assertNotIn("sk-", closed.last_result)
