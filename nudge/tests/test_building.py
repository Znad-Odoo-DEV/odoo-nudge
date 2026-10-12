from types import SimpleNamespace

from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.models.nudge_conversation import DEVELOPER_PARAM
from odoo.addons.nudge.tools.building import CUSTOM_MODULE, field_name_for
from odoo.addons.nudge.tools.records import Refused

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestBuilding(NudgeCase, TransactionCase):
    """Screens are changed by extension, buttons run actions, fields are added: after a yes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = new_test_user(
            cls.env, login="nudge_build_admin",
            groups="nudge.group_nudge_agent,base.group_system,base.group_partner_manager")
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

    def form_arch(self):
        self.env.registry.clear_cache("templates")
        return self.env["res.partner"].get_view(False, "form")["arch"]

    # ------------------------------------------------------------- reading
    def test_a_screen_is_read_with_its_lines_numbered(self):
        result = self.run_tool("read_view", {"model": "res.partner"})
        self.assertFalse(result.is_error, result.content)
        self.assertIn("(form)", result.content.splitlines()[0])
        self.assertIn("Lines 1-", result.content)
        found = self.run_tool("read_view", {"model": "res.partner", "find": "name=\"function\""})
        self.assertIn("line(s) hold", found.content)
        self.assertIn('<field name="function"', found.content)
        listed = self.run_tool("read_view", {"model": "res.partner", "view_type": "tree"})
        self.assertIn("(tree)", listed.content.splitlines()[0])
        self.assertTrue(self.run_tool("read_view", {"model": "res.partner", "view_id": 1}).is_error)

    # ------------------------------------------------------------- changing a screen
    def test_a_change_is_tried_shown_as_a_difference_and_made_after_a_yes(self):
        result = self.run_tool("prepare_view_change", {
            "model": "res.partner", "name": "A note under the job",
            "xpath": "//field[@name='function']", "position": "after",
            "change": '<div class="o_nudge_test_note">Set by Nudge</div>'})
        self.assertFalse(result.is_error, result.content)
        lines = {line["label"]: line for line in result.pending["preview"]["lines"]}
        self.assertIn("//field[@name='function'] · after", lines["Where"]["value"])
        self.assertTrue(lines["Change"]["code"])
        self.assertIn("+", lines["Result"]["value"])
        self.assertIn("o_nudge_test_note", lines["Result"]["value"])
        self.assertNotIn("o_nudge_test_note", self.form_arch(), "Nothing before the yes")
        self.assertFalse(self.env["ir.ui.view"].search([("name", "=", "A note under the job")]))

        done = self.do("prepare_view_change", result)
        self.assertFalse(done.is_error, done.content)
        self.assertIn("o_nudge_test_note", self.form_arch())
        view = self.env["ir.ui.view"].search([("name", "=", "A note under the job")])
        self.assertEqual((view.mode, view.model, view.type), ("extension", "res.partner", "form"))
        self.assertTrue(view.inherit_id)
        self.assertEqual(view.get_external_id()[view.id].split(".")[0], CUSTOM_MODULE)
        self.assertEqual(done.event["action"]["tag"], "reload")
        listed = self.run_tool("read_view", {"model": "res.partner"})
        self.assertIn(f"extension #{view.id} 'A note under the job', made here", listed.content)

        # Switched off, then deleted; the view of the module never
        off = self.run_tool("prepare_write", {
            "model": "ir.ui.view", "ids": [view.id], "values": [{"field": "active", "value": "false"}]})
        self.assertFalse(off.is_error, off.content)
        self.assertTrue(off.pending["is_sensitive"])
        gone = self.run_tool("prepare_delete", {"model": "ir.ui.view", "ids": [view.id]})
        self.assertFalse(gone.is_error, gone.content)
        self.do("prepare_delete", gone)
        self.assertFalse(view.exists())
        self.assertNotIn("o_nudge_test_note", self.form_arch())
        root = self.env["res.partner"].get_view(False, "form")["id"]
        refused = self.run_tool("prepare_delete", {"model": "ir.ui.view", "ids": [root]})
        self.assertTrue(refused.is_error)
        self.assertIn("module", refused.content)

    def test_what_odoo_refuses_is_told(self):
        refused = self.run_tool("prepare_view_change", {
            "model": "res.partner", "xpath": "//field[@name='nowhere_at_all']", "position": "after",
            "change": "<div>x</div>"})
        self.assertTrue(refused.is_error)
        self.assertIn("Odoo refuses", refused.content)
        refused = self.run_tool("prepare_view_change", {
            "model": "res.partner", "xpath": "//sheet", "position": "inside",
            "change": "<script>alert(1)</script>"})
        self.assertTrue(refused.is_error)
        self.assertIn("not an element of a view", refused.content)
        refused = self.run_tool("prepare_view_change", {
            "model": "res.partner", "xpath": "//sheet", "position": "inside",
            "change": '<div t-on-click="boom">x</div>'})
        self.assertTrue(refused.is_error)
        refused = self.run_tool("prepare_view_change", {
            "model": "res.partner", "xpath": "//sheet", "position": "inside", "change": "<div><p>unclosed</div>"})
        self.assertTrue(refused.is_error)
        self.assertIn("well-formed", refused.content)
        self.assertNotIn("nudge_custom", str(self.env["ir.model.data"].sudo().search([("module", "=", CUSTOM_MODULE)]).mapped("name")))

    # ------------------------------------------------------------- a button
    def test_a_button_runs_a_server_action(self):
        model = self.env["ir.model"]._get("res.partner")
        action = self.env["ir.actions.server"].create({
            "name": "Mark as buyer", "model_id": model.id, "state": "object_write",
            "update_path": "function", "evaluation_type": "value", "value": "Buyer"})
        result = self.run_tool("prepare_button", {
            "model": "res.partner", "label": "Mark as buyer", "action_id": action.id,
            "primary": True, "invisible": "function == 'Buyer'"})
        self.assertFalse(result.is_error, result.content)
        lines = {line["label"]: line for line in result.pending["preview"]["lines"]}
        self.assertEqual(lines["Runs"]["value"], f"Mark as buyer (#{action.id}, object_write)")
        self.assertRegex(lines["Shown"]["value"], r"^unless function == 'Buyer': on \d+ of the \d+ records looked at$")
        self.assertIn(f'name="{action.id}" type="action"', lines["Result"]["value"])
        # Hidden on every record: "active == True" hid an Archive button from every contact
        hidden = self.run_tool("prepare_button", {
            "model": "res.partner", "label": "Archive", "action_id": action.id, "invisible": "active == True"})
        shown = {line["label"]: line["value"] for line in hidden.pending["preview"]["lines"]}["Shown"]
        self.assertRegex(shown, r": on 0 of the \d+ records looked at$")
        self.assertIn("hidden on every one", hidden.content)
        done = self.do("prepare_button", result)
        self.assertFalse(done.is_error, done.content)
        arch = self.form_arch()
        self.assertIn(f'<button name="{action.id}" type="action" string="Mark as buyer" class="oe_highlight" invisible="function == \'Buyer\'"', arch)
        other = self.env["ir.actions.server"].create({"name": "Elsewhere", "model_id": self.env["ir.model"]._get("res.users").id, "state": "code", "code": "pass"})
        refused = self.run_tool("prepare_button", {"model": "res.partner", "label": "x", "action_id": other.id})
        self.assertTrue(refused.is_error)
        self.assertIn("not on res.partner", refused.content)

    # ------------------------------------------------------------- a field
    def test_no_field_on_a_form_that_is_not_there(self):
        # Built on lines edited inside another form, it failed on the yes
        result = self.run_tool("prepare_field", {
            "model": "product.attribute.value", "label": "Nudge Shade", "type": "char"})
        self.assertTrue(result.is_error)
        self.assertIn("no form of its own", result.content)
        made = self.run_tool("prepare_field", {
            "model": "product.attribute.value", "label": "Nudge Shade", "type": "char", "on_form": False})
        self.assertFalse(made.is_error, made.content)

    def test_the_field_to_follow_is_found_by_its_label(self):
        # A small model wrote customer_id for partner_id
        result = self.run_tool("prepare_field", {
            "model": "res.partner", "label": "Nudge Title", "type": "char", "after": "job_position"})
        self.assertFalse(result.is_error, result.content)
        lines = {line["label"]: line["value"] for line in result.pending["preview"]["lines"]}
        self.assertIn("after Job Position", lines["On the form"])
        nowhere = self.run_tool("prepare_field", {
            "model": "res.partner", "label": "Nudge Other", "type": "char", "after": "zzz_unknown"})
        self.assertFalse(nowhere.is_error, "Not on the form: in the first group, as the card says")

    def test_a_view_id_of_another_model_is_let_go(self):
        # A small model wrote view_id 1, a view of ir.demo
        other = self.env["ir.ui.view"].search([("model", "!=", "res.partner")], limit=1)
        result = self.run_tool("prepare_field", {
            "model": "res.partner", "label": "Nudge Site", "type": "char", "view_id": other.id})
        self.assertFalse(result.is_error, result.content)

    def test_a_field_is_added_and_put_on_the_form(self):
        self.assertEqual(field_name_for("Nudge Score"), "x_nudge_score")
        self.assertEqual(field_name_for("x", "x_given_name"), "x_given_name")
        # An Arabic label: its letters in Latin, never x_field, never a refusal
        self.assertEqual(field_name_for("الموقع"), "x_mwqa")
        self.assertEqual(field_name_for("رقم المشروع"), "x_rqm_mshrwa")
        self.assertEqual(field_name_for("الموقع", "x_site"), "x_site")
        with self.assertRaises(Refused):
            field_name_for("x", "bad name")
        result = self.run_tool("prepare_field", {
            "model": "res.partner", "label": "Nudge Score", "type": "integer",
            "help": "From 0 to 10", "after": "function"})
        self.assertFalse(result.is_error, result.content)
        self.assertTrue(result.pending["is_sensitive"])
        lines = {line["label"]: line["value"] for line in result.pending["preview"]["lines"]}
        self.assertEqual((lines["Technical name"], lines["Type"]), ("x_nudge_score", "Whole number"))
        self.assertIn("after Job Position", lines["On the form"], "The field it follows, as the form names it")
        self.assertNotIn("x_nudge_score", self.env["res.partner"]._fields, "Nothing before the yes")

        done = self.do("prepare_field", result)
        self.assertFalse(done.is_error, done.content)
        self.assertIn("Added to the form", done.content)
        Partner = self.env["res.partner"]
        self.assertIn("x_nudge_score", Partner._fields)
        field = self.env["ir.model.fields"].search([("model", "=", "res.partner"), ("name", "=", "x_nudge_score")])
        self.assertEqual((field.state, field.ttype, field.help), ("manual", "integer", "From 0 to 10"))
        self.assertIn('<field name="x_nudge_score"', self.form_arch())
        partner = Partner.create({"name": "Scored", "x_nudge_score": 7})
        self.assertEqual(partner.x_nudge_score, 7)

        refused = self.run_tool("prepare_field", {"model": "res.partner", "label": "Nudge Score", "type": "char"})
        self.assertTrue(refused.is_error)
        self.assertIn("already has", refused.content)

        # Deleted only when asked: the column and its values go with it
        gone = self.run_tool("prepare_delete", {"model": "ir.model.fields", "ids": [field.id]})
        self.assertFalse(gone.is_error, gone.content)
        self.assertTrue(gone.pending["is_sensitive"])
        self.assertIn("every value", " ".join(line["value"] for line in gone.pending["preview"]["lines"] if isinstance(line.get("value"), str)))
        self.do("prepare_delete", gone)
        self.assertNotIn("x_nudge_score", self.env["res.partner"]._fields)
        base_field = self.env["ir.model.fields"].search([("model", "=", "res.partner"), ("name", "=", "name")])
        refused = self.run_tool("prepare_delete", {"model": "ir.model.fields", "ids": [base_field.id]})
        self.assertTrue(refused.is_error)

    def test_a_selection_and_a_relation(self):
        result = self.run_tool("prepare_field", {
            "model": "res.partner", "label": "Nudge Tier", "type": "selection", "on_form": False,
            "selection": [{"value": "gold", "label": "Gold"}, {"value": "silver", "label": "Silver"}]})
        self.assertFalse(result.is_error, result.content)
        done = self.do("prepare_field", result)
        self.assertFalse(done.is_error, done.content)
        self.assertNotIn("Added to the form", done.content)
        field = self.env["res.partner"]._fields["x_nudge_tier"]
        self.assertEqual(field.get_description(self.env)["selection"], [("gold", "Gold"), ("silver", "Silver")])
        result = self.run_tool("prepare_field", {
            "model": "res.partner", "label": "Nudge Sponsor", "type": "many2one", "relation": "res.users", "on_form": False})
        self.assertFalse(result.is_error, result.content)
        done = self.do("prepare_field", result)
        self.assertFalse(done.is_error, done.content)
        self.assertEqual(self.env["res.partner"]._fields["x_nudge_sponsor"].comodel_name, "res.users")
        for bad in (
            {"model": "res.partner", "label": "No values", "type": "selection"},
            {"model": "res.partner", "label": "No relation", "type": "many2one"},
            {"model": "res.partner", "label": "Twice", "type": "selection", "selection": [{"value": "a", "label": "A"}, {"value": "a", "label": "B"}]},
        ):
            self.assertTrue(self.run_tool("prepare_field", bad).is_error, bad["label"])

    def test_a_script_is_refused_however_written(self):
        for value in ("javascript:alert(1)", "java\tscript:alert(1)", " JaVaScRiPt :alert(1)", "data:text/html,<b>x</b>", "vbscript:x"):
            refused = self.run_tool("prepare_view_change", {
                "model": "res.partner", "xpath": "//sheet", "position": "inside",
                "change": f'<a href="{value}">x</a>'})
            self.assertTrue(refused.is_error, value)
