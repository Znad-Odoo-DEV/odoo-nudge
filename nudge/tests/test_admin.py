import json

from odoo.tests import TransactionCase, tagged
from .common import new_test_user

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import prompt
from odoo.addons.nudge.llm.snapshot import HIDDEN
from odoo.addons.nudge.models.nudge_conversation import ADMIN_PARAM

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestAdmin(NudgeCase, TransactionCase):
    """The setup of Odoo, for administrators, when the switch is on."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = new_test_user(
            cls.env, login="nudge_admin",
            groups="nudge.group_nudge_agent,base.group_system,base.group_partner_manager")
        cls.Parameter = cls.env["ir.config_parameter"].sudo()
        cls.Parameter.set_param("nudge_test.secret_key", "sk-very-secret")
        cls.Parameter.set_param("nudge_test.plain", "plain-value")

    def run_tool(self, name, arguments, user=None):
        return tools.base.call(self.env(user=user or self.admin), name, arguments, None, "agent")

    def switch(self, on):
        self.Parameter.set_param(ADMIN_PARAM, "yes" if on else "no")

    def test_off_by_default(self):
        self.assertFalse(self.env["nudge.conversation"]._is_admin_allowed())
        self.assertIn("no model", self.run_tool("search_records", {"model": "res.users"}).content)
        self.assertIn("no model", self.run_tool("read_record", {"model": "res.groups", "id": 1}).content)

    def test_on_for_administrators_only(self):
        self.switch(True)
        found = json.loads(self.run_tool("search_records", {
            "model": "res.users", "fields": ["login", "name"],
            "conditions": [{"field": "login", "operator": "=", "value": "nudge_admin"}]}).content)
        self.assertEqual(found["total"], 1)
        self.assertEqual(found["records"][0]["login"], "nudge_admin")
        groups = json.loads(self.run_tool("search_records", {"model": "res.groups", "limit": 3}).content)
        self.assertGreaterEqual(groups["total"], 3)
        # An agent user who is not an administrator sees nothing of it
        self.assertIn("no model", self.run_tool("search_records", {"model": "res.users"}, user=self.agent).content)
        self.switch(False)
        self.assertIn("no model", self.run_tool("search_records", {"model": "res.users"}).content)

    def test_secrets_stay_hidden(self):
        self.switch(True)
        secret = self.Parameter.search([("key", "=", "nudge_test.secret_key")])
        plain = self.Parameter.search([("key", "=", "nudge_test.plain")])
        rows = {row["key"]: row for row in json.loads(self.run_tool("search_records", {
            "model": "ir.config_parameter", "fields": ["key", "value"],
            "conditions": [{"field": "key", "operator": "contains", "value": "nudge_test."}]}).content)["records"]}
        self.assertEqual(rows["nudge_test.secret_key"]["value"], HIDDEN)
        self.assertEqual(rows["nudge_test.plain"]["value"], "plain-value")
        self.assertEqual(set(rows), {"nudge_test.secret_key", "nudge_test.plain"})
        self.assertEqual(secret.value, "sk-very-secret", "Hidden from the model, not changed")
        self.assertTrue(plain)
        self.assertIn("no model", self.run_tool("search_records", {"model": "res.users.apikeys"}).content)
        self.assertIn("no model", self.run_tool("search_records", {"model": "res.users.log"}).content)
        refused = self.run_tool("search_records", {
            "model": "res.users", "conditions": [{"field": "password", "operator": "=", "value": "x"}]})
        self.assertTrue(refused.is_error, "Nothing is looked for in a password")

    def test_a_secret_parameter_leaves_by_no_other_way(self):
        """Review (ai #1, S2): the value of a parameter whose key names a secret stays hidden however it is asked."""
        self.switch(True)
        only_value = json.loads(self.run_tool("search_records", {
            "model": "ir.config_parameter", "fields": ["value"],
            "conditions": [{"field": "key", "operator": "=", "value": "nudge_test.secret_key"}]}).content)
        self.assertEqual(only_value["records"][0]["value"], HIDDEN, "The key is read even when not asked for")
        plain = json.loads(self.run_tool("search_records", {
            "model": "ir.config_parameter", "fields": ["value"],
            "conditions": [{"field": "key", "operator": "=", "value": "nudge_test.plain"}]}).content)
        self.assertEqual(plain["records"][0]["value"], "plain-value")
        for name, arguments in [
            ("aggregate_records", {"model": "ir.config_parameter", "group_by": ["value"]}),
            ("aggregate_records", {"model": "ir.config_parameter", "conditions": [
                {"field": "value", "operator": "contains", "value": "sk-"}]}),
            ("search_records", {"model": "ir.config_parameter", "conditions": [
                {"field": "value", "operator": "contains", "value": "sk-"}]}),
            ("search_records", {"model": "ir.config_parameter", "order": "value desc"}),
        ]:
            result = self.run_tool(name, arguments)
            self.assertTrue(result.is_error, (name, arguments, result.content))
            self.assertNotIn("sk-very-secret", result.content)
        self.Parameter.set_param("nudge.python", "yes")
        for code in (
            "result = env['ir.config_parameter'].search([('key', '=', 'nudge_test.secret_key')]).value",
            "result = env['ir.config_parameter'].search([('key', '=', 'nudge_test.secret_key')]).read()",
            "result = env['ir.config_parameter'].search_read([('key', '=', 'nudge_test.secret_key')], ['value'])",
            "result = env['ir.config_parameter'].search([('key', '=', 'nudge_test.secret_key')]).mapped('value')",
            "result = env['ir.config_parameter'].search_count([('value', 'like', 'sk-')])",
        ):
            result = self.run_tool("run_python", {"code": code, "purpose": "A test"})
            self.assertNotIn("sk-very-secret", result.content, code)
            if code.startswith("result = env['ir.config_parameter'].search_count"):
                self.assertTrue(result.is_error, "No condition on the value: it would tell it letter by letter")

    def test_a_change_goes_through_a_card_marked_sensitive(self):
        self.switch(True)
        pending = self.run_tool("prepare_write", {
            "model": "res.users", "ids": [self.user_a.id],
            "values": [{"field": "name", "value": "Renamed by the admin"}]})
        self.assertFalse(pending.is_error, pending.content)
        self.assertTrue(pending.pending["is_sensitive"])
        self.assertNotEqual(self.user_a.name, "Renamed by the admin", "Nothing before the yes")
        self.assertIn("no tool named", self.run_tool("unlink", {"model": "res.users"}).content)

    def test_sql_keeps_away_from_those_tables(self):
        self.switch(True)
        self.Parameter.set_param("nudge.sql", "yes")
        result = self.run_tool("run_sql", {"query": "SELECT login FROM res_users LIMIT 1"})
        self.assertTrue(result.is_error, result.content)

    def test_told_and_switchable(self):
        self.assertIn("setup of Odoo", prompt.AGENT_MODE)
        settings = self.env["res.config.settings"].with_user(self.admin).create({})
        self.assertFalse(settings.nudge_admin)
        settings.nudge_admin = True
        settings.set_values()
        self.assertTrue(self.env["nudge.conversation"]._is_admin_allowed())
