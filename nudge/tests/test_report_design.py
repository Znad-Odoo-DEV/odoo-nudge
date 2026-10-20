import base64
from types import SimpleNamespace

from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import loop
from odoo.addons.nudge.models.nudge_conversation import DEVELOPER_PARAM
from odoo.addons.nudge.tools import report_design
from odoo.addons.nudge.tools.building import CUSTOM_MODULE

from .common import NudgeCase, require_modules

# A one pixel picture: the logo of the company
LOGO = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82")


@tagged("post_install", "-at_install")
class TestReportDesign(NudgeCase, TransactionCase):
    """A printed document with the identity of the company: an extension view
    of the standard template, tried for a real record, made after a yes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        require_modules(cls, "account", "sale")
        cls.has_sales = "sale.order" in cls.env
        cls.admin = new_test_user(
            cls.env, login="nudge_design_admin",
            groups="nudge.group_nudge_agent,base.group_system,base.group_partner_manager,"
                   "sales_team.group_sale_manager,account.group_account_manager")
        cls.Parameter = cls.env["ir.config_parameter"].sudo()
        cls.Parameter.set_param(DEVELOPER_PARAM, "yes")
        if not cls.has_sales:
            return
        cls.company = cls.env.company
        cls.company.write({
            "logo": LOGO, "vat": "AE100200300400500", "report_footer": "Raghad Trading LLC, licence 4411",
            "primary_color": "#aa2244", "secondary_color": "#334455", "street": "Sheikh Zayed Road", "city": "Dubai",
        })
        bank = cls.env["res.bank"].create({"name": "Nudge National Bank"})
        cls.env["res.partner.bank"].create({
            "acc_number": "AE070331234567890123456", "partner_id": cls.company.partner_id.id, "bank_id": bank.id})
        cls.raghad = cls.env["res.partner"].create({"name": "Raghad"})
        product = cls.env["product.product"].create({"name": "Nudge Design Item", "type": "service"})
        cls.quotation = cls.env["sale.order"].create({
            "partner_id": cls.raghad.id, "order_line": [(0, 0, {
                "product_id": product.id, "product_uom_qty": 2, "price_unit": 50.0})]})
        cls.order = cls.quotation.copy()
        cls.order.action_confirm()
        cls.report = cls.env.ref("sale.action_report_saleorder")
        cls.root = cls.env.ref("sale.report_saleorder_document")

    def setUp(self):
        super().setUp()
        if not self.has_sales:
            self.skipTest("Sales is not installed")
        self.Parameter.set_param(DEVELOPER_PARAM, "yes")

    def dev_env(self, user=None):
        return self.env(user=user or self.admin, context={"nudge_mode": "dev"})

    def design(self, **arguments):
        arguments = {"report": "quotation", "record_id": self.quotation.id, **arguments}
        return tools.base.call(self.dev_env(), "prepare_report_design", arguments, None, "dev")

    def make(self, result):
        step = SimpleNamespace(
            values=result.pending["values"], res_model=result.pending["res_model"], res_ids=[])
        done = tools.base.TOOLS["prepare_report_design"].execute(self.dev_env(), step)
        self.assertFalse(done.is_error, done.content)
        return self.env["ir.ui.view"].browse(done.records[1])

    def printed(self, record=None):
        html, _kind = self.report._render_qweb_html(self.report, (record or self.quotation).ids)
        return html.decode("utf-8")

    def lines(self, result):
        return {line["label"]: line for line in result.pending["preview"]["lines"]}

    def test_the_design_inherits_the_standard_template_and_nothing_is_made_before_the_yes(self):
        before = self.env["ir.ui.view"].search_count([])
        original = self.root.arch_db
        result = self.design()
        self.assertFalse(result.is_error, result.content)
        values = result.pending["values"]["view"]
        self.assertEqual((values["type"], values["mode"], values["inherit_id"]), ("qweb", "extension", self.root.id))
        self.assertIn("hasclass('page')", values["arch_db"])
        self.assertEqual(self.env["ir.ui.view"].search_count([]), before, "Nothing before the yes")
        card = self.lines(result)
        self.assertEqual(card["Extends"]["value"], "sale.report_saleorder_document (never edited)")
        self.assertIn("+", card["Result"]["value"])
        self.assertTrue(card["Result"]["code"])
        self.assertIn("o_nudge_rd_head", card["Result"]["value"])
        # The page, as it prints, for a real record
        page = next(line for label, line in card.items() if label.startswith("As "))
        self.assertIn(self.quotation.display_name, page["label"])
        self.assertIn("o_nudge_rd_head", page["html"])
        self.assertIn("Sheikh Zayed Road", page["html"])
        view = self.make(result)
        self.assertEqual(view.inherit_id, self.root)
        self.assertEqual(view.get_external_id()[view.id].split(".")[0], CUSTOM_MODULE)
        self.assertEqual(self.root.arch_db, original, "The standard template is never edited")

    def test_the_printed_page_has_the_blocks_of_the_company(self):
        self.make(self.design())
        html = self.printed()
        for block in ("o_nudge_rd_head", "o_nudge_rd_totals", "o_nudge_rd_footer"):
            self.assertIn(block, html)
        self.assertIn("AE100200300400500", html)
        self.assertIn("AE070331234567890123456", html)
        self.assertIn("Nudge National Bank", html)
        self.assertIn("Raghad Trading LLC, licence 4411", html)
        self.assertIn("data:image/", html, "The logo")
        self.assertIn("#aa2244", html, "The colour of the company")
        self.assertIn(self.quotation.name, html)
        self.assertIn("Quotation", html)
        # The standard content is still there
        self.assertIn("Nudge Design Item", html)

    def test_a_draft_has_a_watermark_and_a_confirmed_order_has_none(self):
        self.make(self.design())
        self.assertEqual(self.quotation.state, "draft")
        self.assertIn('class="o_nudge_rd_watermark"', self.printed())
        self.assertEqual(self.order.state, "sale")
        self.assertNotIn('class="o_nudge_rd_watermark"', self.printed(self.order))
        off = self.design(watermark=False)
        self.assertNotIn('class="o_nudge_rd_watermark"', off.pending["values"]["view"]["arch_db"])

    def test_the_four_presets_and_the_colours_asked(self):
        sheets = {}
        for style in ("clean", "bold", "classic", "minimal"):
            result = self.design(style=style, primary_color="#112233")
            self.assertFalse(result.is_error, result.content)
            sheets[style] = result.pending["values"]["view"]["arch_db"]
            self.assertIn(style, result.pending["summary"])
            self.assertIn("the colours asked", self.lines(result)["Style"]["value"])
        self.assertEqual(len(set(sheets.values())), 4, "Each preset looks different")
        self.assertIn("background-color: #112233", sheets["bold"])
        self.assertIn("Georgia", sheets["classic"])
        self.assertNotIn("#aa2244", sheets["bold"], "The colour asked wins over the company's")
        # Without a colour asked, the company's
        result = self.design()
        self.assertIn("the colours of the company", self.lines(result)["Style"]["value"])

    def test_an_arabic_company_gets_a_right_to_left_bilingual_page(self):
        self.env["res.lang"]._activate_lang("ar_001")
        self.company.partner_id.lang = "ar_001"
        result = self.design()
        arch = result.pending["values"]["view"]["arch_db"]
        self.assertIn('dir="rtl"', arch)
        self.assertIn("direction: rtl", arch)
        self.assertIn("عرض سعر / Quotation", arch)
        self.assertIn("الرقم الضريبي / VAT", arch)
        self.assertIn("مسودة / DRAFT", arch)
        self.assertIn("Right to left", self.lines(result)["Page"]["value"])
        self.make(result)
        html = self.printed()
        self.assertIn("عرض سعر / Quotation", html)
        self.assertIn('dir="rtl"', html)
        # An English company, or Arabic switched off, prints left to right in English only
        self.company.partner_id.lang = "en_US"
        english = self.design().pending["values"]["view"]["arch_db"]
        self.assertIn('dir="ltr"', english)
        self.assertNotIn("عرض", english)
        forced = self.design(arabic=True).pending["values"]["view"]["arch_db"]
        self.assertIn('dir="rtl"', forced)

    def test_a_style_sheet_written_from_the_words_of_the_person_is_plain_css(self):
        result = self.design(custom_css=".o_nudge_rd_title h1 { color: #118833; font-size: 30px; }")
        self.assertIn("font-size: 30px", result.pending["values"]["view"]["arch_db"])
        for bad in ("@import 'x.css';", ".a { background: url(http://evil.example/x.png); }", "</style><script>",
                    ".a { width: expression(alert(1)); }",
                    '.a { background: image-set("http://169.254.169.254/x" 1x); }',
                    '.a { background: -webkit-image-set("//x/y" 1x); }',
                    "@font-face { font-family: x; src: local(y); }"):
            refused = self.design(custom_css=bad)
            self.assertTrue(refused.is_error, bad)

    def test_the_invoice_is_designed_too_and_a_missing_app_is_told(self):
        if "account.move" not in self.env:
            self.skipTest("Invoicing is not installed")
        invoice = self.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": self.raghad.id, "invoice_line_ids": [(0, 0, {
                "name": "Nudge Design Item", "quantity": 1, "price_unit": 120.0})]})
        result = self.design(report="invoice", record_id=invoice.id)
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.pending["values"]["view"]["inherit_id"], self.env.ref("account.report_invoice_document").id)
        self.make(result)
        report = self.env.ref("account.account_invoices")
        html, _kind = report._render_qweb_html(report, invoice.ids)
        self.assertIn("o_nudge_rd_footer", html.decode("utf-8"))
        self.assertIn('class="o_nudge_rd_watermark"', html.decode("utf-8"), "A draft invoice")
        if "stock.picking" not in self.env:
            slip = self.design(report="delivery_slip")
            self.assertTrue(slip.is_error)
            self.assertIn("not installed", slip.content)

    def test_the_pdf_engine_is_told_not_assumed(self):
        result = self.design()
        page = next(line for label, line in self.lines(result).items() if label.startswith("As "))
        self.assertTrue(page["value"], "The card says what the PDF engine did")

    def test_a_second_design_switches_the_first_off(self):
        first = self.make(self.design(style="bold"))
        second_result = self.design(style="classic")
        self.assertIn(first.name, self.lines(second_result)["Replaces"]["value"])
        second = self.make(second_result)
        self.assertFalse(first.active)
        self.assertTrue(first.exists(), "Nothing is deleted")
        self.assertTrue(second.active)
        html = self.printed()
        self.assertIn("Georgia", html)
        self.assertEqual(html.count('class="o_nudge_rd_head"'), 1, "One header, not two designs on top of each other")

    def test_undo_takes_the_design_away(self):
        conversation = self.env["nudge.conversation"].with_user(self.admin)._chat_open()
        run = self.env["nudge.run"].sudo().create({
            "name": "Design", "conversation_id": conversation.id, "user_id": self.admin.id, "is_developer": True})
        env = self.env(user=self.admin, context={"nudge_mode": "dev", "nudge_conversation_id": conversation.id})
        result = tools.base.call(env, "prepare_report_design", {
            "report": "quotation", "record_id": self.quotation.id}, None, "dev")
        done = tools.base.TOOLS["prepare_report_design"].execute(env, SimpleNamespace(
            values=result.pending["values"], res_model="ir.ui.view", res_ids=[]))
        model, ids, _names = done.records
        self.env["nudge.step"].sudo().create({
            "run_id": run.id, "tool": "prepare_report_design", "kind": "create", "state": "done",
            "res_model": model, "res_ids": ids})
        self.assertIn("o_nudge_rd_head", self.printed())
        asked = tools.base.call(env, "prepare_undo", {}, None, "dev")
        self.assertFalse(asked.is_error, asked.content)
        tools.base.TOOLS["prepare_undo"].execute(env, SimpleNamespace(
            values=asked.pending["values"], res_model="nudge.run", res_ids=[]))
        self.assertFalse(self.env["ir.ui.view"].browse(ids).exists())
        self.assertNotIn("o_nudge_rd_head", self.printed())
        self.assertIn("Nudge Design Item", self.printed(), "The standard document prints as before")

    def test_a_person_without_the_developer_group_is_refused(self):
        plain = new_test_user(self.env, login="nudge_design_plain", groups="nudge.group_nudge_agent")
        env = self.env(user=plain, context={"nudge_mode": "dev"})
        result = tools.base.call(env, "prepare_report_design", {"report": "quotation"}, None, "dev")
        self.assertTrue(result.is_error)
        self.assertIsNone(result.pending)
        self.assertNotIn("prepare_report_design", [
            tool.name for tool in tools.base.available(env, "dev")])
        # An administrator, with Developer mode closed here
        self.Parameter.set_param(DEVELOPER_PARAM, "no")
        closed = self.design()
        self.assertTrue(closed.is_error)
        self.assertIsNone(closed.pending)
        self.assertEqual(self.env["ir.ui.view"].search_count([("name", "like", report_design.NAME_PREFIX)]), 0)

    def test_the_provider_asks_for_the_design_and_the_person_says_yes(self):
        from .test_agent import asking, call, saying  # noqa: PLC0415

        adapter = self.use_adapter(script=[
            asking(call("prepare_report_design", {
                "report": "quotation", "style": "custom", "primary_color": "#0a7a5a",
                "custom_css": ".o_nudge_rd_title h1 { font-size: 28px; }", "record_id": self.quotation.id})),
            saying("Done: your quotation now prints with the identity of Raghad Trading."),
        ])
        conversation = self.env["nudge.conversation"].with_user(self.admin)._chat_open()
        job = conversation._chat_prepare("Design the quotation with our identity, green and big title", mode="dev")
        list(loop.run(job, conversation._chat_record))
        self.assertFalse(self.env["ir.ui.view"].search([("name", "like", report_design.NAME_PREFIX)]),
                         "Nothing before the yes")
        self.decide(conversation)
        view = self.env["ir.ui.view"].search([("name", "like", report_design.NAME_PREFIX)])
        self.assertEqual(len(view), 1)
        self.assertIn("font-size: 28px", view.arch_db)
        self.assertIn("#0a7a5a", view.arch_db)
        self.assertEqual(view.inherit_id, self.root)
        self.assertEqual(len(adapter.calls), 2)
