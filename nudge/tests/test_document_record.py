import base64
import json
from unittest.mock import patch

from odoo import release
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import base, loop
from odoo.addons.nudge.llm.fake_adapter import FakeAdapter
from odoo.addons.nudge.tools import document_record

from .common import NudgeCase, require_modules
from .test_agent import asking, call, saying
from .test_attachments import pdf_with

BILL_PDF = "Invoice INV-77 from Raghad Trading LLC VAT 100123456700003 Copper wire 2.5 mm CW25 qty 10 at 20.00"

BILL = {
    "document_type": "vendor_bill",
    "partner": {"name": "Raghad Trading LLC", "vat": "100123456700003", "email": "billing@raghad-trading.example"},
    "reference": "INV-77", "date": "2026-10-01", "due_date": "2026-10-31", "currency": "AED",
    "lines": [
        {"description": "Copper wire 2.5 mm", "code": "CW25", "quantity": 10, "unit_price": 20.0, "tax": "VAT 5%"},
        {"description": "Delivery to site", "quantity": 1, "unit_price": 50.0, "tax": "VAT 5%"},
    ],
    "totals": {"untaxed": 250.0, "tax": 12.5, "total": 262.5},
}

EMAIL = "Hello, Ebrahim here from Ebrahim Hardware. We would like a quote for 3 drills. Call 0501234567."


def reading(documents):
    """The answer of the provider, by what the document says: deterministic"""
    def answer(self, system, text, images=None):
        answer.calls.append((text, images))
        for marker, data in documents.items():
            if marker in text:
                return "```json\n" + json.dumps(data) + "\n```"
        return "I cannot read this."
    answer.calls = []
    return answer


@tagged("post_install", "-at_install")
class TestDocumentToRecord(NudgeCase, TransactionCase):
    """A document becomes a draft record, after a yes, with the file kept on it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        require_modules(cls, "account", "purchase")
        Partner = cls.env["res.partner"]
        groups = ["nudge.group_nudge_agent", "base.group_partner_manager"]
        for model, group in (("account.move", "account.group_account_invoice"),
                             ("sale.order", "sales_team.group_sale_manager"),
                             ("purchase.order", "purchase.group_purchase_user")):
            if model in cls.env:
                groups.append(group)
        cls.accountant = new_test_user(cls.env, login="nudge_doc", groups=",".join(groups))
        cls.supplier = Partner.create({
            "name": "Raghad Trading", "is_company": True, "vat": "100123456700003"})
        cls.person = Partner.create({"name": "Raghad Clerk", "parent_id": cls.supplier.id})
        cls.twins = Partner.create([
            {"name": "Ebrahim Hardware", "is_company": True},
            {"name": "Ebrahim Hardware", "is_company": True},
        ])
        if "product.product" in cls.env:
            Product = cls.env["product.product"]
            cls.wire = Product.create({"name": "Copper wire 2.5 mm", "default_code": "CW25", "list_price": 25.0})
            cls.dups = Product.create([{"name": "Raghad Bracket", "default_code": "DUP-1"},
                                       {"name": "Raghad Bracket long", "default_code": "DUP-1"}])
        if "account.tax" in cls.env:
            Tax = cls.env["account.tax"]
            cls.tax_purchase = Tax.create({
                "name": "Nudge doc VAT 5% purchase", "amount": 5, "amount_type": "percent", "type_tax_use": "purchase"})
            cls.tax_sale = Tax.create({
                "name": "Nudge doc VAT 5% sale", "amount": 5, "amount_type": "percent", "type_tax_use": "sale"})
            cls.env.company  # noqa: B018

    # -- helpers -------------------------------------------------------
    def needs(self, model):
        if model not in self.env:
            self.skipTest(f"{model} is not installed")

    def use_reading(self, documents):
        function = reading(documents)
        self.patch(type(self.env["nudge.provider"]), "_complete_once", function)
        return function

    def attached(self, name="bill.pdf", data=None, mimetype="application/pdf", user=None):
        """:return: the environment of the tools in a conversation holding the file"""
        user = user or self.accountant
        data = data if data is not None else pdf_with(BILL_PDF)
        conversation = self.env["nudge.conversation"].with_user(user)._chat_open(None)
        conversation._chat_prepare("Make a bill from this", attachment=(name, data, mimetype), mode="agent")
        conversation.sudo().write({"stream_started_at": False})
        return self.env(user=user, context={"nudge_conversation_id": conversation.id}), conversation

    def run_tool(self, env, **arguments):
        return tools.base.call(env, "document_to_record", arguments, mode="agent")

    def say_yes(self, env, result):
        """The person says yes to what the tool described"""
        run = self.env["nudge.run"].sudo().create({
            "name": "doc", "conversation_id": env.context["nudge_conversation_id"],
            "user_id": env.uid, "company_id": self.env.company.id, "model_name": "m"})
        pending = result.pending
        step = self.env["nudge.step"].sudo().create({
            "run_id": run.id, "call_id": "c1", "tool": "document_to_record", "kind": "create",
            "state": "pending", "summary": pending["summary"], "res_model": pending["res_model"],
            "res_ids": [], "values": pending["values"], "preview": pending["preview"],
            "arguments": {}})
        done = tools.base.TOOLS["document_to_record"].execute(env, step)
        return step, done

    def lines_of(self, result):
        return {line["field"]: line for line in result.pending["preview"]["lines"]}

    # -- a vendor bill -------------------------------------------------
    def test_a_pdf_becomes_a_draft_vendor_bill_after_a_yes(self):
        self.needs("account.move")
        self.use_reading({"INV-77": BILL})
        env, _conversation = self.attached()
        result = self.run_tool(env, target="vendor_bill")
        self.assertFalse(result.is_error, result.content)
        self.assertTrue(result.pending)
        # Nothing is made before the yes
        self.assertFalse(self.env["account.move"].search([("ref", "=", "INV-77")]))
        shown = self.lines_of(result)
        self.assertIn("Raghad Trading", shown["partner_id"]["value"])
        self.assertIn("found by VAT", shown["partner_id"]["value"])
        self.assertEqual(len(shown["lines"]["lines"]), 2)
        self.assertIn("Copper wire 2.5 mm", shown["lines"]["lines"][0]["value"])
        self.assertIn("no product", shown["lines"]["lines"][1]["value"])
        self.assertNotIn("differences", {key for key, line in shown.items() if line.get("lines")})
        step, done = self.say_yes(env, result)
        bill = self.env["account.move"].browse(done.records[1])
        self.assertEqual((bill.move_type, bill.state), ("in_invoice", "draft"))
        self.assertEqual(bill.partner_id, self.supplier)
        self.assertEqual(bill.ref, "INV-77")
        self.assertEqual(len(bill.invoice_line_ids), 2)
        self.assertEqual(bill.invoice_line_ids[0].product_id, self.wire)
        self.assertEqual(bill.invoice_line_ids[0].tax_ids, self.tax_purchase)
        self.assertEqual(bill.amount_total, 262.5)
        # Created by the person, with the file on it
        self.assertEqual(bill.create_uid, self.accountant)
        attachment = self.env["ir.attachment"].search([("res_model", "=", "account.move"), ("res_id", "=", bill.id)])
        self.assertEqual(attachment.name, "bill.pdf")
        self.assertEqual(attachment.create_uid, self.accountant)
        self.assertTrue(attachment.raw.startswith(b"%PDF"))

    def test_a_difference_with_the_totals_of_the_document_is_shown(self):
        self.needs("account.move")
        wrong = json.loads(json.dumps(BILL))
        wrong["totals"] = {"untaxed": 250.0, "tax": 12.5, "total": 270.0}
        self.use_reading({"INV-77": wrong})
        env, _conversation = self.attached()
        result = self.run_tool(env, target="vendor_bill")
        shown = self.lines_of(result)
        differences = shown["differences"]["lines"]
        self.assertEqual(len(differences), 1)
        self.assertEqual(differences[0]["label"], "Total")
        self.assertIn("262.50", differences[0]["value"])
        self.assertIn("270.00", differences[0]["value"])

    def test_a_tax_the_company_does_not_have_is_said(self):
        self.needs("account.move")
        odd = json.loads(json.dumps(BILL))
        odd["lines"][0]["tax"] = "Mystery levy"
        self.use_reading({"INV-77": odd})
        env, _conversation = self.attached()
        result = self.run_tool(env, target="vendor_bill")
        self.assertTrue(any("Mystery levy" in line["value"] for line in result.pending["preview"]["lines"]
                            if line["field"] == "note"))

    def test_the_draft_is_never_posted_and_undo_cancels_it_and_keeps_its_file(self):
        self.needs("account.move")
        self.use_reading({"INV-77": BILL})
        env, _conversation = self.attached()
        step, done = self.say_yes(env, self.run_tool(env, target="vendor_bill"))
        bill = self.env["account.move"].browse(done.records[1])
        self.assertEqual(bill.state, "draft")
        step.state = "done"
        card = step._get_card()
        self.assertTrue(card["can_undo"])
        result = self.env["nudge.step"].with_user(self.accountant).undo_changes(step.id)
        # Never deleted: a draft bill is cancelled, and keeps its file
        self.assertEqual(bill.state, "cancel")
        self.assertTrue(self.env["ir.attachment"].search([("res_model", "=", "account.move"),
                                                          ("res_id", "=", bill.id)]))
        self.assertIn("archived or cancelled", result["message"])
        self.assertTrue(result["card"]["undone"])

    def test_undo_leaves_a_record_that_was_posted_since(self):
        self.needs("account.move")
        self.use_reading({"INV-77": BILL})
        env, _conversation = self.attached()
        step, done = self.say_yes(env, self.run_tool(env, target="vendor_bill"))
        bill = self.env["account.move"].browse(done.records[1])
        step.state = "done"
        bill.invoice_date = "2026-10-01"
        bill.action_post()
        result = self.env["nudge.step"].with_user(self.accountant).undo_changes(step.id)
        self.assertTrue(bill.exists())
        self.assertIn("changed since", result["message"])

    def test_a_lead_is_archived_and_a_model_with_no_way_back_is_said(self):
        self.needs("crm.lead")
        lead = self.env["crm.lead"].create({"name": "Raghad lead"})
        from odoo.addons.nudge.tools.plans import snapshot
        entry = {"created": True, "after": snapshot(lead, ["write_date"])}
        self.assertEqual(document_record.undo_created(self.env, "crm.lead", lead.id, entry), (True, ""))
        self.assertFalse(lead.active)
        state = self.env["res.country.state"].create({
            "name": "Ebrahim State", "code": "EB9", "country_id": self.env.ref("base.ae").id})
        entry = {"created": True, "after": snapshot(state, ["write_date"])}
        done, why = document_record.undo_created(self.env, "res.country.state", state.id, entry)
        self.assertFalse(done)
        self.assertIn("by hand", why)
        self.assertTrue(state.exists())

    def test_a_customer_invoice(self):
        self.needs("account.move")
        invoice = json.loads(json.dumps(BILL))
        invoice["document_type"] = "customer_invoice"
        invoice["partner"] = {"name": "Raghad Trading"}
        self.use_reading({"INV-77": invoice})
        env, _conversation = self.attached()
        result = self.run_tool(env, target="customer_invoice")
        step, done = self.say_yes(env, result)
        record = self.env["account.move"].browse(done.records[1])
        self.assertEqual((record.move_type, record.state, record.partner_id), ("out_invoice", "draft", self.supplier))
        self.assertEqual(record.invoice_line_ids[0].tax_ids, self.tax_sale)

    # -- orders --------------------------------------------------------
    def test_a_quotation_from_an_email(self):
        self.needs("sale.order")
        data = {"document_type": "sales_order", "partner": {"name": "Raghad Trading"}, "reference": "PO-5",
                "lines": [{"description": "Copper wire 2.5 mm", "quantity": 4, "unit_price": 25.0}]}
        self.use_reading({"quote for wire": data})
        env, _conversation = self.attached()
        result = self.run_tool(env, target="sales_order", text="Please quote for wire, 4 rolls, ref PO-5")
        self.assertFalse(result.is_error, result.content)
        step, done = self.say_yes(env, result)
        order = self.env["sale.order"].browse(done.records[1])
        self.assertEqual((order.state, order.partner_id, order.client_order_ref), ("draft", self.supplier, "PO-5"))
        self.assertEqual(order.order_line.product_id, self.wire)
        self.assertEqual(order.order_line.product_uom_qty, 4)
        # The pasted text is kept as a file
        attachment = self.env["ir.attachment"].search([("res_model", "=", "sale.order"), ("res_id", "=", order.id)])
        self.assertIn(b"quote for wire", attachment.raw)

    def test_a_purchase_order_from_a_csv(self):
        self.needs("purchase.order")
        data = {"document_type": "purchase_order", "partner": {"name": "Raghad Trading"},
                "lines": [{"description": "Copper wire", "code": "CW25", "quantity": 2, "unit_price": 19.0}]}
        self.use_reading({"CW25": data})
        csv = b"code,description,qty,price\nCW25,Copper wire,2,19.0\n"
        env, _conversation = self.attached("order.csv", csv, "text/csv")
        step, done = self.say_yes(env, self.run_tool(env, target="purchase_order"))
        order = self.env["purchase.order"].browse(done.records[1])
        self.assertEqual((order.state, order.partner_id), ("draft", self.supplier))
        self.assertEqual(order.order_line.product_id, self.wire)
        self.assertEqual(order.order_line.price_unit, 19.0)

    # -- leads and contacts ---------------------------------------------
    def test_a_lead_from_an_email(self):
        self.needs("crm.lead")
        data = {"document_type": "lead", "subject": "Quote for 3 drills",
                "partner": {"name": "Ebrahim Hardware", "contact_name": "Ebrahim", "phone": "0501234567"},
                "notes": "Wants three drills."}
        self.use_reading({"3 drills": data})
        env, _conversation = self.attached()
        result = self.run_tool(env, target="guess", text=EMAIL)
        self.assertFalse(result.is_error, result.content)
        step, done = self.say_yes(env, result)
        lead = self.env["crm.lead"].browse(done.records[1])
        self.assertEqual(lead.name, "Quote for 3 drills")
        self.assertEqual(lead.contact_name, "Ebrahim")
        # Two contacts have the same name: none is linked to the lead, and it is said
        self.assertFalse(lead.partner_id)

    def test_a_contact_from_a_card_and_a_second_one_is_not_made_by_chance(self):
        data = {"document_type": "contact", "partner": {
            "name": "Ebrahim Steel", "is_company": True, "email": "info@ebrahim-steel.example", "phone": "0501111111",
            "country": "United Arab Emirates"}}
        self.use_reading({"Steel card": data})
        env, _conversation = self.attached()
        result = self.run_tool(env, target="contact", text="Steel card")
        step, done = self.say_yes(env, result)
        partner = self.env["res.partner"].browse(done.records[1])
        self.assertEqual((partner.name, partner.email, partner.is_company), ("Ebrahim Steel", "info@ebrahim-steel.example", True))
        self.assertEqual(partner.country_id.code, "AE")
        again = self.run_tool(env, target="contact", text="Steel card")
        self.assertFalse(again.pending)
        self.assertIn("exists already", again.content)
        self.assertTrue(self.run_tool(env, target="contact", text="Steel card", create_partner=True).pending)

    def test_a_guess_follows_the_document(self):
        self.needs("account.move")
        self.use_reading({"INV-77": BILL})
        env, _conversation = self.attached()
        result = self.run_tool(env, target="guess")
        self.assertEqual(result.pending["res_model"], "account.move")
        self.assertEqual(result.pending["values"]["values"]["move_type"], "in_invoice")

    def test_a_document_that_says_nothing_is_not_guessed(self):
        self.use_reading({"INV-77": {"document_type": "unknown"}})
        env, _conversation = self.attached()
        result = self.run_tool(env, target="guess")
        self.assertTrue(result.is_error)
        self.assertFalse(result.pending)

    # -- ambiguity -----------------------------------------------------
    def test_two_contacts_with_the_name_are_asked_about(self):
        self.needs("sale.order")
        data = {"document_type": "sales_order", "partner": {"name": "Ebrahim Hardware"},
                "lines": [{"description": "Drill", "quantity": 3, "unit_price": 100.0}]}
        self.use_reading({"drills": data})
        env, _conversation = self.attached()
        result = self.run_tool(env, target="sales_order", text="3 drills")
        self.assertFalse(result.pending)
        self.assertIn("ambiguous", result.content)
        self.assertIn(str(self.twins[0].id), result.content)
        # The document is not read twice
        function = self.use_reading({"drills": data})
        chosen = self.run_tool(env, target="sales_order", text="3 drills", partner_id=self.twins[1].id)
        self.assertTrue(chosen.pending)
        self.assertEqual(chosen.pending["values"]["values"]["partner_id"], self.twins[1].id)
        self.assertEqual(function.calls, [])

    def test_two_products_with_the_code_are_asked_about(self):
        self.needs("sale.order")
        data = {"document_type": "sales_order", "partner": {"name": "Raghad Trading"},
                "lines": [{"description": "Bracket", "code": "DUP-1", "quantity": 3, "unit_price": 10.0}]}
        self.use_reading({"brackets": data})
        env, _conversation = self.attached()
        result = self.run_tool(env, target="sales_order", text="brackets")
        self.assertIn("product", result.content)
        self.assertIn("ambiguous", result.content)
        chosen = self.run_tool(env, target="sales_order", text="brackets",
                               product_choices=[{"line": 1, "product_id": self.dups[1].id}])
        self.assertEqual(chosen.pending["values"]["lines"][0]["product_id"], self.dups[1].id)
        none = self.run_tool(env, target="sales_order", text="brackets",
                             product_choices=[{"line": 1, "product_id": 0}])
        self.assertNotIn("product_id", none.pending["values"]["lines"][0])

    def test_a_contact_that_is_not_in_odoo_is_created_only_when_agreed(self):
        self.needs("sale.order")
        data = {"document_type": "sales_order", "partner": {"name": "Ebrahim Newcomers", "email": "n@example.com"},
                "lines": [{"description": "Drill", "quantity": 1, "unit_price": 100.0}]}
        self.use_reading({"newcomers": data})
        env, _conversation = self.attached()
        asked = self.run_tool(env, target="sales_order", text="newcomers")
        self.assertFalse(asked.pending)
        self.assertIn("create_partner", asked.content)
        self.assertFalse(self.env["res.partner"].search([("name", "=", "Ebrahim Newcomers")]))
        agreed = self.run_tool(env, target="sales_order", text="newcomers", create_partner=True)
        self.assertIn("a new contact is created", self.lines_of(agreed)["partner_id"]["value"])
        step, done = self.say_yes(env, agreed)
        order = self.env["sale.order"].browse(done.records[1])
        self.assertEqual(order.partner_id.name, "Ebrahim Newcomers")
        step.state = "done"
        result = self.env["nudge.step"].with_user(self.accountant).undo_changes(step.id)
        self.assertEqual(order.state, "cancel", result["message"])
        # The new contact is archived, not deleted
        partner = self.env["res.partner"].with_context(active_test=False).search([("name", "=", "Ebrahim Newcomers")])
        self.assertTrue(partner and not partner.active)

    # -- rights --------------------------------------------------------
    def test_it_is_made_as_the_person_who_may_not_create_it(self):
        self.needs("account.move")
        self.use_reading({"INV-77": BILL})
        clerk = new_test_user(self.env, login="nudge_doc_clerk", groups="nudge.group_nudge_agent")
        env, _conversation = self.attached(user=clerk)
        result = self.run_tool(env, target="vendor_bill")
        self.assertTrue(result.is_error)
        self.assertFalse(result.pending)

    def test_no_right_to_create_is_refused_before_any_provider_call(self):
        self.needs("account.move")
        function = self.use_reading({"INV-77": BILL})
        clerk = new_test_user(self.env, login="nudge_doc_plain", groups="nudge.group_nudge_agent")
        env, _conversation = self.attached(user=clerk)
        for target in ("vendor_bill", "customer_invoice"):
            result = self.run_tool(env, target=target)
            self.assertTrue(result.is_error, target)
            self.assertFalse(result.pending, target)
        self.assertEqual(function.calls, [], "The document is not sent to the provider for one who may not make the record")

    def test_a_line_with_no_product_is_a_note_line_where_the_database_demands_one(self):
        """Odoo 17 refuses an order line with neither a product nor a section/note type: the line is kept as a note"""
        self.needs("sale.order")
        data = {"document_type": "sales_order", "partner": {"name": "Raghad Trading"},
                "lines": [{"description": "Copper wire 2.5 mm", "quantity": 4, "unit_price": 25.0},
                          {"description": "Ebrahim special drill", "quantity": 2, "unit_price": 100.0}]}
        for as_note in (True, False):
            with self.subTest(as_note=as_note), patch.object(
                    document_record, "unmatched_as_note", lambda target, forced=as_note: forced or release.version_info[0] < 18):
                self.use_reading({"special drill": data})
                env, _conversation = self.attached()
                result = self.run_tool(env, target="sales_order", text="special drill")
                self.assertFalse(result.is_error, result.content)
                lines = self.lines_of(result)["lines"]["lines"]
                if as_note or release.version_info[0] < 18:
                    self.assertIn("note line", lines[1]["value"])
                _step, done = self.say_yes(env, result)
                order = self.env["sale.order"].browse(done.records[1])
                self.assertEqual(len(order.order_line), 2)
                special = order.order_line.filtered(lambda line: "special drill" in line.name)
                if as_note or release.version_info[0] < 18:
                    self.assertIn("quantity 2", special.name)
                    self.assertIn("100.00", special.name)
                    self.assertEqual(special.display_type, "line_note")
                    self.assertEqual(order.order_line.filtered("product_id").product_id, self.wire)
                else:
                    self.assertFalse(special.display_type)
                    self.assertEqual(special.price_unit, 100.0)

    def test_a_purchase_line_with_no_product_is_a_note_line_where_the_database_demands_one(self):
        self.needs("purchase.order")
        data = {"document_type": "purchase_order", "partner": {"name": "Raghad Trading"},
                "lines": [{"description": "Ebrahim special bolt", "quantity": 3, "unit_price": 7.0}]}
        with patch.object(document_record, "unmatched_as_note", lambda target: True):
            self.use_reading({"special bolt": data})
            env, _conversation = self.attached()
            _step, done = self.say_yes(env, self.run_tool(env, target="purchase_order", text="special bolt"))
        line = self.env["purchase.order"].browse(done.records[1]).order_line
        self.assertEqual(line.display_type, "line_note")
        self.assertIn("special bolt", line.name)

    def test_an_app_that_is_not_installed_is_said(self):
        with patch.object(tools.document_record.DocumentToRecord, "_check_target",
                          side_effect=tools.records.Refused("The CRM app is not installed in this database.")):
            self.use_reading({})
            env, _conversation = self.attached()
            result = self.run_tool(env, target="lead")
        self.assertTrue(result.is_error)
        self.assertIn("not installed", result.content)

    # -- Odoo's own digitisation ----------------------------------------
    def test_odoos_own_digitisation_comes_first_for_a_vendor_bill(self):
        self.needs("account.move")
        function = self.use_reading({"INV-77": BILL})
        env, _conversation = self.attached()
        with patch.object(document_record, "ocr_installed", lambda env: True):
            first = self.run_tool(env, target="vendor_bill")
            self.assertFalse(first.pending)
            self.assertIn("digitisation", first.content)
            self.assertEqual(function.calls, [])
            self.assertTrue(self.run_tool(env, target="vendor_bill", insist=True).pending)
            # A customer invoice is not what that digitisation does
            self.assertFalse(self.run_tool(env, target="customer_invoice").is_error)

    # -- private mode and no provider ------------------------------------
    def test_private_mode_refuses_the_tool_before_anything_is_sent(self):
        self.needs("account.move")
        env, _conversation = self.attached()
        self.env.company.nudge_private_mode = True
        adapter = FakeAdapter(reply="{}")
        with patch.object(base, "get_adapter", return_value=adapter) as made:
            result = self.run_tool(env, target="vendor_bill")
        self.assertTrue(result.is_error)
        self.assertIn("Private mode is on", result.content)
        made.assert_not_called()
        self.assertEqual(adapter.calls, [])
        self.assertFalse(result.pending)

    def test_no_provider_is_said(self):
        self.needs("account.move")
        env, _conversation = self.attached()
        self.env["nudge.provider"].search([]).write({"active": False})
        result = self.run_tool(env, target="vendor_bill")
        self.assertTrue(result.is_error)
        self.assertIn("not set up", result.content)

    # -- reading the file -----------------------------------------------
    def test_a_scanned_pdf_says_so(self):
        self.needs("account.move")
        from .test_attachments import blank_pdf
        self.use_reading({})
        env, _conversation = self.attached(data=blank_pdf())
        result = self.run_tool(env, target="vendor_bill")
        self.assertTrue(result.is_error)
        self.assertIn("scanned", result.content)

    def test_no_file_and_no_text_is_said(self):
        env = self.env(user=self.accountant, context={})
        result = self.run_tool(env, target="vendor_bill")
        self.assertTrue(result.is_error)
        self.assertIn("No document", result.content)

    def test_a_picture_is_looked_at_by_a_model_that_sees(self):
        self.needs("account.move")
        function = self.use_reading({"File: bill.png": BILL})
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
        env, _conversation = self.attached("bill.png", png, "image/png")
        with patch.object(type(self.env["nudge.provider"]), "_can_see_pictures", lambda self: True):
            result = self.run_tool(env, target="vendor_bill")
        self.assertTrue(result.pending, result.content)
        self.assertEqual(function.calls[0][1][0]["media_type"], "image/png")

    def test_a_picture_is_copied_first_by_a_model_that_cannot_see(self):
        self.needs("account.move")
        function = self.use_reading({"INV-77": BILL})
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
        env, _conversation = self.attached("bill.png", png, "image/png")
        provider = type(self.env["nudge.provider"])
        with patch.object(provider, "_can_see_pictures", lambda self: False), \
                patch.object(provider, "_read_picture", lambda self, *a, **k: "Invoice INV-77 Raghad Trading"):
            result = self.run_tool(env, target="vendor_bill")
        self.assertTrue(result.pending, result.content)
        self.assertEqual(function.calls[0][1], None)

    def test_a_file_of_the_record_on_the_screen_is_read(self):
        self.needs("account.move")
        self.use_reading({"INV-77": BILL})
        attachment = self.env["ir.attachment"].create({
            "name": "from-chatter.pdf", "raw": pdf_with(BILL_PDF), "mimetype": "application/pdf",
            "res_model": "res.partner", "res_id": self.supplier.id})
        env = self.env(user=self.accountant, context={})
        screen = {"model": "res.partner", "record": {"id": self.supplier.id}}
        result = tools.base.call(env, "document_to_record", {"target": "vendor_bill"}, screen=screen, mode="agent")
        self.assertTrue(result.pending, result.content)
        self.assertEqual(result.pending["values"]["source"], {"kind": "attachment", "id": attachment.id})
        by_id = self.run_tool(env, target="vendor_bill", attachment_id=attachment.id)
        self.assertTrue(by_id.pending)

    # -- the reading of the answer ---------------------------------------
    def test_what_the_model_wrote_is_read_with_care(self):
        data = document_record.normalise(document_record.parse_json(
            'Here: {"document_type": "vendor_bill", "partner": {"name": "A"}, "date": "01/10/2026", '
            '"lines": [{"description": "x", "quantity": "1,000", "unit_price": "١٢٫٥", "line_total": 12500},'
            '"junk", {"quantity": 2, "line_total": 40}], "totals": {"total": "1,234.50"}}'))
        self.assertEqual(data["date"], "2026-10-01")
        self.assertEqual(data["lines"][0]["quantity"], 1000.0)
        self.assertEqual(data["lines"][0]["unit_price"], 12.5)
        self.assertEqual(len(data["lines"]), 2)
        self.assertEqual(data["lines"][1]["unit_price"], 20.0)
        self.assertEqual(data["totals"]["total"], 1234.5)
        with self.assertRaises(tools.records.Refused):
            document_record.parse_json("I cannot read this.")

    def test_the_provider_is_asked_once_with_no_tools(self):
        adapter = FakeAdapter(script=[[base.text_event('{"a": 1}'), base.stop_event(
            base.STOP_END, usage={"input_tokens": 100, "output_tokens": 10})]])
        self.patch(type(self.env["nudge.provider"]), "_get_adapter", lambda provider: adapter)
        answer = self.provider._complete_once("system", "text")
        self.assertEqual(answer, '{"a": 1}')
        self.assertIsNone(adapter.calls[0]["tools"])
        self.assertTrue(self.env["nudge.usage"].sudo().search([("provider_id", "=", self.provider.id)]))

    def test_a_failing_provider_is_told(self):
        adapter = FakeAdapter(script=[[base.error_event(base.ERROR_RATE_LIMIT, "slow down")]])
        self.patch(type(self.env["nudge.provider"]), "_get_adapter", lambda provider: adapter)
        with self.assertRaises(UserError):
            self.provider._complete_once("system", "text")

    # -- through the conversation -----------------------------------------
    def test_asked_through_the_conversation_one_yes_creates_the_draft(self):
        self.needs("account.move")
        self.use_reading({"INV-77": BILL})
        self.use_adapter(script=[
            asking(call("document_to_record", {"target": "vendor_bill"})),
            saying("Done."),
        ])
        Conversation = self.env["nudge.conversation"].with_user(self.accountant)
        conversation = Conversation._chat_open(None)
        job = conversation._chat_prepare(
            "Make a bill from this", mode="agent", attachment=("bill.pdf", pdf_with(BILL_PDF), "application/pdf"))
        list(loop.run(job, conversation._chat_record))
        step = conversation.sudo().waiting_step_id
        self.assertEqual(step.tool, "document_to_record")
        self.assertEqual(step.kind, "create")
        self.assertIn("vendor bill", step.summary)
        self.assertFalse(self.env["account.move"].search([("ref", "=", "INV-77")]))
        self.decide(conversation)
        bill = self.env["account.move"].search([("ref", "=", "INV-77")])
        self.assertEqual(bill.state, "draft")
        self.assertTrue(self.env["ir.attachment"].search([("res_model", "=", "account.move"), ("res_id", "=", bill.id)]))

    def test_offered_to_the_agent_only_and_in_the_group_of_documents(self):
        agent = [tool.name for tool in tools.base.available(self.env(user=self.accountant), "agent")]
        talk = [tool.name for tool in tools.base.available(self.env(user=self.accountant), "talk")]
        self.assertIn("document_to_record", agent)
        self.assertNotIn("document_to_record", talk)
        self.assertIn("documents", tools.groups.asked("make a bill from this invoice"))
        self.assertIn("documents", tools.groups.asked("حوّل هذه الفاتورة"))
        self.assertIn("document_to_record", tools.groups.tools_of(["documents"]))
