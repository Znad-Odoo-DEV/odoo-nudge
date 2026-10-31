from types import SimpleNamespace

from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.models.nudge_conversation import DEVELOPER_PARAM
from odoo.addons.nudge.tests.common import NudgeCase


@tagged("post_install", "-at_install")
class TestApprovals(NudgeCase, TransactionCase):
    """An approval rule on a button, made the way Studio makes it, after a yes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = new_test_user(
            cls.env, login="nudge_studio_admin", name="Nudge Studio Admin",
            groups="nudge.group_nudge_agent,base.group_system,base.group_partner_manager")
        cls.approver = new_test_user(cls.env, login="nudge_approver", name="Nudge Approver", groups="base.group_user")
        cls.env["ir.config_parameter"].sudo().set_param(DEVELOPER_PARAM, "yes")

    def dev_env(self):
        return self.env(user=self.admin, context={"nudge_mode": "dev"})

    def run_tool(self, name, arguments):
        return tools.base.call(self.dev_env(), name, arguments, None, "dev")

    def do(self, name, result):
        step = SimpleNamespace(
            values=result.pending["values"], res_model=result.pending["res_model"],
            res_ids=result.pending["res_ids"])
        return tools.base.TOOLS[name].execute(self.dev_env(), step)

    def test_a_rule_is_read_made_and_deleted(self):
        listed = self.run_tool("read_approval_rules", {"model": "res.partner"})
        self.assertFalse(listed.is_error, listed.content)
        self.assertIn("Buttons of the form a rule can go on:", listed.content)
        self.assertIn("method create_company", listed.content)

        result = self.run_tool("prepare_approval_rule", {
            "model": "res.partner", "method": "create_company",
            "approvers": ["nudge_approver"], "step": 2, "exclusive": True,
            "notify": ["Nudge Studio Admin"], "message": "A manager checks the company first",
            "conditions": [{"field": "is_company", "operator": "=", "value": "false"}],
        })
        self.assertFalse(result.is_error, result.content)
        lines = {line["label"]: line["value"] for line in result.pending["preview"]["lines"]}
        # The label of the button as the form says it: Create, in Odoo 19
        self.assertTrue(lines["Button"].startswith("Create"), lines["Button"])
        self.assertEqual(lines["Approvers"], "Nudge Approver")
        self.assertEqual(lines["Step"], "2")
        self.assertEqual(lines["Exclusive"], True)
        self.assertIn("Company", lines["Only for"])
        Rule = self.env["studio.approval.rule"]
        self.assertFalse(Rule.search([("model_name", "=", "res.partner"), ("method", "=", "create_company")]), "Nothing before the yes")

        done = self.do("prepare_approval_rule", result)
        self.assertFalse(done.is_error, done.content)
        rule = Rule.search([("model_name", "=", "res.partner"), ("method", "=", "create_company")])
        self.assertEqual(len(rule), 1)
        self.assertEqual((rule.approver_ids, rule.notification_order, rule.exclusive_user), (self.approver, "2", True))
        self.assertEqual(rule.users_to_notify, self.admin)
        self.assertTrue(rule.conditional)
        self.assertIn("is_company", rule.domain)
        self.assertEqual(rule.message, "A manager checks the company first")
        listed = self.run_tool("read_approval_rules", {"model": "res.partner"})
        self.assertIn(f"#{rule.id} on res.partner, button create_company: approvers Nudge Approver, step 2, exclusive", listed.content)

        gone = self.run_tool("prepare_delete", {"model": "studio.approval.rule", "ids": [rule.id]})
        self.assertFalse(gone.is_error, gone.content)
        self.do("prepare_delete", gone)
        self.assertFalse(rule.exists())

    def test_a_deleted_approver_or_model_is_told_plainly(self):
        result = self.run_tool("prepare_approval_rule", {
            "model": "res.partner", "method": "create_company", "approvers": ["nudge_approver"]})
        self.assertFalse(result.is_error, result.content)
        gone = new_test_user(self.env, login="nudge_gone_approver", name="Raghad Gone", groups="base.group_user")
        survivors = result.pending["values"]["rule"]["approver_ids"]
        # The only approver is deleted between the card and the yes
        result.pending["values"]["rule"]["approver_ids"] = [gone.id]
        gone.unlink()
        done = self.do("prepare_approval_rule", result)
        self.assertTrue(done.is_error)
        self.assertIn("approver", done.content)
        self.assertIn("deleted", done.content)
        # The model is deleted
        result.pending["values"]["rule"]["approver_ids"] = survivors
        result.pending["values"]["rule"]["model_id"] = 999999999
        done = self.do("prepare_approval_rule", result)
        self.assertTrue(done.is_error)
        self.assertIn("model", done.content)

    def test_a_rule_left_pointing_at_what_is_gone_is_listed_with_the_others(self):
        # On dev_0, rule #56 on purchase.order pointed at action #3375, deleted since: reading
        # its name failed (MissingError), no rule was listed, and the card was built blind
        for step in (1, 2):
            result = self.run_tool("prepare_approval_rule", {
                "model": "res.partner", "method": "create_company", "approvers": ["nudge_approver"], "step": step})
            self.assertFalse(result.is_error, result.content)
            self.assertFalse(self.do("prepare_approval_rule", result).is_error)
        rules = self.env["studio.approval.rule"].search(
            [("model_name", "=", "res.partner"), ("method", "=", "create_company")], order="id")
        self.assertEqual(len(rules), 2)
        action = self.env["ir.actions.act_window"].create({"name": "Nudge Gone", "res_model": "res.partner"})
        gone = action.id
        action.unlink()
        # Nothing in the database ties a rule to its action: it is left pointing at nothing
        self.env.cr.execute(
            "UPDATE studio_approval_rule SET method = NULL, action_id = %s WHERE id = %s", (gone, rules[0].id))
        self.env.cr.execute(
            "UPDATE studio_approval_rule SET method = 'action_gone_since' WHERE id = %s", (rules[1].id,))
        self.env.invalidate_all()

        listed = self.run_tool("read_approval_rules", {"model": "res.partner"})
        self.assertFalse(listed.is_error, listed.content)
        self.assertIn(f"#{rules[0].id} on res.partner, button action #{gone}, a button action that no "
                      "longer exists: approvers Nudge Approver", listed.content)
        self.assertIn(f"#{rules[1].id} on res.partner, button action_gone_since, a method res.partner "
                      "no longer has: approvers Nudge Approver, step 2", listed.content)
        self.assertIn("method create_company", listed.content, "The buttons of the form all the same")

    def test_what_is_wrong_is_told(self):
        for bad, said in (
            ({"model": "res.partner", "method": "nowhere", "approvers": ["nudge_approver"]}, "no such button"),
            ({"model": "res.partner", "method": "create_company"}, "who approves"),
            ({"model": "res.partner", "method": "create_company", "approvers": ["nobody at all"]}, "No user"),
            ({"model": "res.partner", "method": "create_company", "group": "No such group", "approvers": []}, "No group"),
            ({"model": "res.partner", "method": "create_company", "action_id": 1, "approvers": ["nudge_approver"]}, "not both"),
        ):
            refused = self.run_tool("prepare_approval_rule", bad)
            self.assertTrue(refused.is_error, bad)
            self.assertIn(said, refused.content)

    def test_a_group_is_found_by_its_external_id_or_full_name(self):
        # Found on dev_0 (2026-10-27): "Administrator" is the name of a dozen groups, and the flow gave only it
        group = self.env.ref("base.group_system")
        for named in ("base.group_system", group.full_name):
            with self.subTest(named=named):
                shown = self.run_tool("prepare_approval_rule", {
                    "model": "res.partner", "method": "create_company", "group": named, "approvers": []})
                self.assertFalse(shown.is_error, shown.content)
        # A group name is unique within one app only: two apps of this database share "Administrator", or the
        # refusal cannot be shown here
        if self.env["res.groups"].search_count([("name", "=ilike", "Administrator")]) > 1:
            refused = self.run_tool("prepare_approval_rule", {
                "model": "res.partner", "method": "create_company", "group": "Administrator", "approvers": []})
            self.assertTrue(refused.is_error)
            self.assertIn("full name", refused.content)

    def test_a_group_said_as_a_role_in_arabic_reaches_its_own_group(self):
        """Filming: "مدير النظام" produced Sales / Manager. A role word and a sales word never mix; unsure asks."""
        from odoo.addons.nudge_studio.tools.approvals import find_group
        for text, xmlid in (("مدير النظام", "base.group_system"), ("مدير المبيعات", "sales_team.group_sale_manager"),
                            ("المدير المالي", "account.group_account_manager")):
            expected = self.env.ref(xmlid, raise_if_not_found=False)
            if expected:
                self.assertEqual(find_group(self.env, text), expected, text)
        self.assertFalse(find_group(self.env, "المدير"), "Unsure: no group, so the person is asked")

    def test_the_card_of_a_rule_speaks_the_language_of_the_person(self):
        self.env["res.lang"]._activate_lang("ar_001")
        self.env["ir.module.module"].search([("name", "in", ("nudge", "nudge_studio"))])._update_translations(["ar_001"])
        self.admin.lang = "ar_001"
        shown = self.run_tool("prepare_approval_rule", {
            "model": "res.partner", "method": "create_company", "approvers": ["nudge_approver"],
            "conditions": [{"field": "is_company", "operator": "=", "value": "false"}]})
        self.assertFalse(shown.is_error, shown.content)
        for line in shown.pending["preview"]["lines"]:
            self.assertNotIn(line["label"], ("Model", "Button", "Only for", "Approvers", "Step"), line)

    def test_studio_steps_are_given_to_the_pointer(self):
        result = tools.base.call(self.env(user=self.user_a), "show_in_studio", {"steps": [
            {"target_type": "studio_menu", "target_name": "automations", "text": "Open Automations."},
            {"target_type": "studio_tab", "target_name": "new", "text": "Open the Add tab."},
            {"target_type": "studio_component", "target_name": "many2one", "text": "Drag Many2One into the form."},
        ]}, None, "talk")
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.event["type"], "guidance")
        self.assertEqual([step["target"] for step in result.event["steps"]], [
            {"type": "studio_menu", "name": "automations"},
            {"type": "studio_tab", "name": "new"},
            {"type": "studio_component", "name": "many2one"},
        ])
        refused = tools.base.call(self.env(user=self.user_a), "show_in_studio", {"steps": [
            {"target_type": "studio_tab", "target_name": "nowhere", "text": "x"}]}, None, "talk")
        self.assertTrue(refused.is_error)
