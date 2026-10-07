from types import SimpleNamespace

from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.models.nudge_conversation import DEVELOPER_PARAM
from odoo.addons.nudge.tools.building import CUSTOM_MODULE

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestReports(NudgeCase, TransactionCase):
    """A PDF report is made of fields, tried for one record, and made after a yes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = new_test_user(
            cls.env, login="nudge_report_admin",
            groups="nudge.group_nudge_agent,base.group_system,base.group_partner_manager")
        cls.env["ir.config_parameter"].sudo().set_param(DEVELOPER_PARAM, "yes")
        cls.company_partner = cls.env["res.partner"].create({
            "name": "Nudge Printing Co", "email": "print@nudge.example", "function": "Printer",
            "child_ids": [(0, 0, {"name": "Nudge Contact One", "email": "one@nudge.example", "function": "Printer"})],
        })

    def dev_env(self):
        return self.env(user=self.admin, context={"nudge_mode": "dev"})

    def run_tool(self, name, arguments):
        return tools.base.call(self.dev_env(), name, arguments, None, "dev")

    def do(self, name, result):
        step = SimpleNamespace(
            values=result.pending["values"], res_model=result.pending["res_model"],
            res_ids=result.pending["res_ids"])
        return tools.base.TOOLS[name].execute(self.dev_env(), step)

    def test_a_report_of_fields_prints_the_record(self):
        Report = self.env["ir.actions.report"]
        before = Report.search_count([])
        result = self.run_tool("prepare_report", {
            "model": "res.partner", "name": "Contact Sheet", "title": "Contact sheet",
            "fields": ["name", "email", "function"],
            "lines": {"field": "child_ids", "columns": ["name", "email"]},
        })
        self.assertFalse(result.is_error, result.content)
        lines = {line["label"]: line for line in result.pending["preview"]["lines"]}
        self.assertEqual(lines["Shows"]["value"], "Name, Email, Job Position")
        self.assertEqual(lines["Lines"]["value"], "Contact: Name, Email")
        self.assertTrue(lines["Template"]["code"])
        self.assertIn('t-call="web.external_layout"', lines["Template"]["value"])
        # The newest contact is printed: the child, with its own lines (none)
        printed = lines["Prints, for one record"]["value"]
        self.assertIn("Nudge Contact One", printed)
        self.assertIn("one@nudge.example", printed)
        self.assertNotIn("data:image", printed, "The logo is not text")
        self.assertEqual(Report.search_count([]), before, "Nothing before the yes")
        self.assertFalse(self.env["ir.ui.view"].search([("key", "like", "nudge_custom.report_contact_sheet")]))

        done = self.do("prepare_report", result)
        self.assertFalse(done.is_error, done.content)
        report = Report.search([("name", "=", "Contact Sheet")])
        self.assertEqual(len(report), 1)
        self.assertEqual((report.model, report.report_type, report.binding_model_id.model), ("res.partner", "qweb-pdf", "res.partner"))
        self.assertEqual(report.report_name, "nudge_custom.report_contact_sheet")
        view = self.env["ir.ui.view"].search([("key", "=", report.report_name)])
        self.assertEqual((view.type, view.get_external_id()[view.id].split(".")[0]), ("qweb", CUSTOM_MODULE))
        html, _kind = Report._render_qweb_html(report, self.company_partner.ids)
        self.assertIn(b"print@nudge.example", html)
        self.assertIn(b"one@nudge.example", html)
        # A second one of the same name gets a key of its own
        again = self.run_tool("prepare_report", {"model": "res.partner", "name": "Contact Sheet", "fields": ["name"]})
        self.assertEqual(again.pending["values"]["report"]["report_name"], "nudge_custom.report_contact_sheet_2")

        # Deleted when asked: the template goes with it
        gone = self.run_tool("prepare_delete", {"model": "ir.actions.report", "ids": [report.id]})
        self.assertFalse(gone.is_error, gone.content)
        self.do("prepare_delete", gone)
        self.assertFalse(report.exists())
        self.assertFalse(view.exists())

    def test_what_cannot_be_printed_is_told(self):
        refused = self.run_tool("prepare_report", {"model": "res.partner", "name": "Bad", "fields": ["image_1920"]})
        self.assertTrue(refused.is_error)
        self.assertIn("not printed as a value", refused.content)
        refused = self.run_tool("prepare_report", {"model": "res.partner", "name": "Bad", "fields": ["nowhere"]})
        self.assertTrue(refused.is_error)
        refused = self.run_tool("prepare_report", {
            "model": "res.partner", "name": "Bad", "fields": ["name"], "lines": {"field": "name", "columns": ["name"]}})
        self.assertTrue(refused.is_error)
        self.assertIn("not a one2many", refused.content)
        refused = self.run_tool("prepare_report", {"model": "res.partner", "name": "Bad"})
        self.assertTrue(refused.is_error)

    def test_a_template_of_their_own_is_rendered_first(self):
        template = (
            '<t t-call="web.html_container"><t t-foreach="docs" t-as="o">'
            '<t t-call="web.external_layout"><div class="page"><h1 t-out="o.name"/>'
            '<p>Role: <span t-field="o.function"/></p></div></t></t></t>')
        result = self.run_tool("prepare_report", {"model": "res.partner", "name": "Own Layout", "template": template})
        self.assertFalse(result.is_error, result.content)
        self.assertTrue(result.pending["is_sensitive"])
        self.assertIn("Role: Printer", {line["label"]: line["value"] for line in result.pending["preview"]["lines"]}["Prints, for one record"])
        self.assertIn('t-name="nudge_custom.report_own_layout"', result.pending["values"]["arch"])
        broken = template.replace("o.function", "o.nowhere_at_all")
        refused = self.run_tool("prepare_report", {"model": "res.partner", "name": "Broken", "template": broken})
        self.assertTrue(refused.is_error)
        self.assertIn("cannot be rendered", refused.content)
        refused = self.run_tool("prepare_report", {"model": "res.partner", "name": "Bad", "template": "<div><script>x</script></div>"})
        self.assertTrue(refused.is_error)
        refused = self.run_tool("prepare_report", {"model": "res.partner", "name": "Bad", "template": "<div>no records</div>"})
        self.assertTrue(refused.is_error)
        self.assertIn("docs", refused.content)

    def test_the_studio_pages_are_found_by_their_app(self):
        if not self.env["nudge.doc.page"].sudo().search_count([("category", "=", "studio")]):
            self.skipTest("The documentation is not loaded")
        found = tools.base.call(self.env(user=self.user_a), "search_odoo_documentation", {"query": "approval step", "app": "studio"}, None, "talk")
        self.assertFalse(found.is_error, found.content)
        self.assertIn("approval_rules", found.content)

    def test_a_template_reads_its_records_only(self):
        for reach in (
            "o.env['ir.config_parameter'].sudo().get_param('database.secret')",
            "o.sudo().name",
            "o.write({'name': 'x'})",
            "o.__class__",
            "o.with_user(1).name",
            "o.search([])",
            "request.session",
        ):
            template = (
                '<t t-call="web.html_container"><t t-foreach="docs" t-as="o">'
                f'<div class="page"><span t-out="{reach}"/></div></t></t>')
            refused = self.run_tool("prepare_report", {"model": "res.partner", "name": "Reach", "template": template})
            self.assertTrue(refused.is_error, reach)
            self.assertIn("reaches beyond", refused.content)
