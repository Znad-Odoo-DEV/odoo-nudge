import time
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.tools import validator

from .common import NudgeCase, require_modules

ALL_ON = {key: True for key in validator.DEFAULTS}


def form(model, values=None, lines=None, **extra):
    """A payload as the browser sends it for a form of ``model``"""
    return dict({"model": model, "id": None, "is_new": True, "changed": True,
                 "fields": values or {}, "lines": lines or {}}, **extra)


def one(value):
    return {"id": value.id, "name": value.display_name}


@tagged("post_install", "-at_install")
class TestValidator(NudgeCase, TransactionCase):
    """Warnings at entry time, from the unsaved values of a form; never blocking, no AI."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        require_modules(cls, "account", "purchase", "stock")
        groups = ["nudge.group_nudge_user", "base.group_partner_manager"] + [
            xmlid for xmlid in (
                "sales_team.group_sale_manager", "purchase.group_purchase_manager",
                "account.group_account_manager", "stock.group_stock_manager")
            if cls.env.ref(xmlid, raise_if_not_found=False)]
        cls.typist = new_test_user(cls.env, login="validator_typist", groups=",".join(groups))
        cls.Validator = cls.env["nudge.validator"].with_user(cls.typist)
        # The name is compared by similarity whatever the size of the table the test runs on
        cls.env["ir.config_parameter"].sudo().set_param(validator.NAME_ROWS_PARAM, "1000000000")
        cls.twin = cls.env["res.partner"].create({
            "name": "Raghad Trading Company", "email": "Raghad.Trading@example.com", "phone": "+971 50 123 4567",
            "vat": "100123456789003", "company_type": "company"})

    def warnings(self, payload, user=None, on=ALL_ON):
        self.env.company.nudge_validator_switches = on
        return (self.Validator if user is None else user).check(payload)["warnings"]

    def keys(self, payload, **kw):
        return {item["key"] for item in self.warnings(payload, **kw)}

    # ------------------------------------------------------------ what is on, who may

    def test_only_duplicates_vat_and_bill_numbers_are_on_from_the_start(self):
        self.env.company.nudge_validator_switches = False
        on = {key for key, default in validator.DEFAULTS.items() if default}
        self.assertEqual(on - {"policy_required"}, {
            "partner_duplicate", "product_duplicate", "vat_format", "bill_number_reuse"})
        spec = self.Validator.get_spec("res.partner")
        self.assertEqual(set(spec["checks"]), {"partner_duplicate", "vat_format"})
        self.assertNotIn("email_syntax", spec["checks"])
        self.assertEqual(set(spec["fields"]), {"name", "email", "phone", "mobile", "vat", "parent_id", "country_id"})

    def test_the_company_switches_a_check_on_and_off(self):
        self.env.company.nudge_validator_switches = {"email_syntax": True, "partner_duplicate": False}
        spec = self.Validator.get_spec("res.partner")
        self.assertIn("email_syntax", spec["checks"])
        self.assertNotIn("partner_duplicate", spec["checks"])

    def test_a_person_mutes_a_check_for_themselves_only(self):
        self.env.company.nudge_validator_switches = ALL_ON
        other = new_test_user(self.env, login="validator_other", groups="nudge.group_nudge_user,base.group_partner_manager")
        payload = form("res.partner", {"name": "Raghad Trading Company"})
        self.assertIn("partner_duplicate.name", self.keys(payload))
        self.assertEqual(self.Validator.mute("partner_duplicate"), ["partner_duplicate"])
        self.assertNotIn("partner_duplicate", self.Validator.get_spec("res.partner")["checks"])
        self.assertNotIn("partner_duplicate.name", self.keys(payload))
        self.assertIn("partner_duplicate.name", self.keys(payload, user=self.env["nudge.validator"].with_user(other)))
        self.Validator.unmute_all()
        self.assertIn("partner_duplicate.name", self.keys(payload))
        self.assertEqual(self.Validator.mute("email_syntax"), ["email_syntax"])
        self.assertEqual(self.Validator.mute("email_syntax", False), [])

    def test_only_those_who_use_nudge(self):
        outsider = self.env["nudge.validator"].with_user(self.outsider)
        with self.assertRaises(AccessError):
            outsider.get_spec("res.partner")
        with self.assertRaises(AccessError):
            outsider.check(form("res.partner"))

    def test_nothing_unknown_is_looked_at_and_nothing_is_written(self):
        self.assertEqual(self.warnings(form("not.a.model")), [])
        self.assertEqual(self.warnings(None), [])
        self.assertEqual(self.warnings("x"), [])
        self.assertEqual(self.warnings(form("res.partner", {"name": 5, "email": ["a"], "vat": {"x": 1}})), [])
        before = self.twin.write_date, self.env["res.partner"].search_count([])
        self.warnings(form("res.partner", {"name": "Raghad Trading Company"}))
        self.assertEqual((self.twin.write_date, self.env["res.partner"].search_count([])), before)

    def test_the_input_of_a_client_is_cut_down(self):
        cleaned = validator.clean(form("res.partner", {"name": "x" * 5000, "a": object()},
                                       lines={"order_line": [{"price_unit": 1}] * 500}))
        self.assertEqual(len(cleaned["fields"]["name"]), validator.MAX_TEXT)
        self.assertIsNone(cleaned["fields"]["a"])
        self.assertEqual(len(cleaned["lines"]["order_line"]), validator.MAX_LINES)

    # ------------------------------------------------------------ duplicate contacts and products

    def test_a_duplicate_contact_by_email_phone_vat_and_name(self):
        payload = form("res.partner", {
            "name": "Raghad  Trading-Company!", "email": " raghad.trading@EXAMPLE.com", "phone": "050 123 4567",
            "vat": "100 123 456 789 003"})
        found = {item["key"]: item for item in self.warnings(payload)}
        for kind in ("email", "phone", "vat", "name"):
            self.assertIn(f"partner_duplicate.{kind}", found)
        item = found["partner_duplicate.email"]
        self.assertEqual((item["model"], item["ids"], item["field"]), ("res.partner", [self.twin.id], "email"))
        self.assertIn("Raghad Trading Company", item["message"])
        self.assertEqual(item["severity"], "warning")

    def test_the_duplicate_questions_are_served_by_indexes_not_a_scan(self):
        """Release-2 review B1: email by email_normalized, phone/vat/name by an expression index"""
        names = {row[0] for row in self._rows("SELECT indexname FROM pg_indexes WHERE tablename = 'res_partner'")}
        for index in ("nudge_partner_email_normalized_key_idx", "nudge_partner_phone_key_idx",
                      "nudge_partner_vat_key_idx", "nudge_partner_name_key_idx"):
            self.assertIn(index, names)
        statements = []
        original = self.env.cr.execute

        payload = form("res.partner", {"name": "Raghad Trading Company", "email": "Raghad.Trading@example.com"})
        with patch.object(type(self.env.cr), "execute", lambda cr, q, *a, **k: (statements.append(str(q)), original(q, *a, **k))[1]):
            found = self.keys(payload)
        self.assertIn("partner_duplicate.email", found)
        self.assertTrue(any("t.email_normalized" in q for q in statements))
        self.assertFalse(any("lower(btrim(coalesce(t.email" in q for q in statements))

    def _rows(self, query):
        self.env.cr.execute(query)
        return self.env.cr.fetchall()

    def test_a_big_contact_table_compares_the_name_exactly_and_a_small_one_by_similarity(self):
        payload = form("res.partner", {"name": "Raghad  Trading-Company!"})
        param = self.env["ir.config_parameter"].sudo()
        param.set_param(validator.NAME_ROWS_PARAM, "1")  # every table is "big"
        self.assertNotIn("partner_duplicate.name", self.keys(payload))
        exact = form("res.partner", {"name": "Raghad Trading Company"})
        self.assertIn("partner_duplicate.name", self.keys(exact))
        param.set_param(validator.NAME_ROWS_PARAM, "100000000")
        self.assertIn("partner_duplicate.name", self.keys(payload))

    def test_the_checks_of_one_form_stop_after_the_whole_budget(self):
        payload = form("res.partner", {"name": "Raghad Trading Company", "email": "raghad.trading@example.com"})
        with patch.object(validator, "WHOLE_SECONDS", -1):
            self.assertEqual(self.keys(payload), set())

    def test_the_record_itself_a_contact_below_a_company_and_other_people_are_not_duplicates(self):
        payload = form("res.partner", {"name": "Raghad Trading Company", "email": "raghad.trading@example.com"},
                       id=self.twin.id, is_new=False)
        self.assertEqual(self.keys(payload), set())
        below = dict(payload, id=None, is_new=True)
        below["fields"] = dict(below["fields"], parent_id={"id": self.twin.id, "name": "x"})
        self.assertEqual(self.keys(below), set())
        self.assertEqual(self.keys(form("res.partner", {"name": "Ebrahim Stores", "email": "ebrahim@example.com"})), set())

    def test_a_duplicate_the_person_may_not_read_is_not_told(self):
        self.env["ir.rule"].create({
            "name": "Raghad hides her company", "model_id": self.env.ref("base.model_res_partner").id,
            "domain_force": f"[('id', '!=', {self.twin.id})]", "global": True})
        self.assertEqual(self.keys(form("res.partner", {"email": "raghad.trading@example.com"})), set())

    def test_a_duplicate_product_by_code_barcode_and_name(self):
        categ = self.env["product.category"].search([], limit=1)
        product = self.env["product.template"].create({
            "name": "Raghad Cement", "default_code": "CEM-01", "barcode": "6291041500213", "categ_id": categ.id})
        payload = form("product.template", {
            "name": "raghad cement", "default_code": "CEM-01", "barcode": "6291041500213", "categ_id": one(categ)})
        found = {item["key"]: item for item in self.warnings(payload)}
        for kind in ("default_code", "barcode", "name"):
            self.assertIn(f"product_duplicate.{kind}", found)
        self.assertEqual(found["product_duplicate.name"]["ids"], [product.id])
        # Its own form is not a duplicate of itself
        own = dict(payload, id=product.id, is_new=False)
        self.assertEqual(self.keys(own), set())
        # The same name in another category is another product
        other = self.env["product.category"].create({"name": "Ebrahim Category"})
        payload["fields"] = dict(payload["fields"], default_code="", barcode="", categ_id=one(other))
        self.assertEqual(self.keys(payload), set())

    # ------------------------------------------------------------ formats

    def test_the_tax_number_follows_the_country(self):
        uae = self.env.ref("base.ae")
        ksa = self.env.ref("base.sa")

        def keys(vat, country):
            return self.keys(form("res.partner", {"vat": vat, "country_id": one(country)}))

        self.assertEqual(keys("100987654321003", uae), set())
        self.assertEqual(keys("100 9876 5432 1003", uae), set())
        self.assertEqual(keys("10012345678900", uae), {"vat_format"})
        self.assertEqual(keys("30012345678900A", uae), {"vat_format"})
        self.assertEqual(keys("300123456789003", ksa), set())
        self.assertEqual(keys("400123456789003", ksa), {"vat_format"})
        self.assertEqual(keys("", ksa), set())
        message = self.warnings(form("res.partner", {"vat": "400123456789003", "country_id": one(ksa)}))[0]["message"]
        self.assertIn("15 digits, the first one a 3", message)
        self.assertIn("Saudi Arabia", message)

    def test_the_european_tax_number_is_checked_by_base_vat_when_it_is_there(self):
        if "_check_vat_number" not in dir(self.env["res.partner"]) and not hasattr(self.env["res.partner"], "simple_vat_check"):
            self.skipTest("base_vat is not installed")
        belgium = self.env.ref("base.be")
        self.assertEqual(self.keys(form("res.partner", {"vat": "BE0477472701", "country_id": one(belgium)})), set())
        self.assertEqual(self.keys(form("res.partner", {"vat": "BE0477472702", "country_id": one(belgium)})), {"vat_format"})

    def test_the_phone_is_checked_only_where_it_can_be_told(self):
        payload = form("res.partner", {"phone": "12", "country_id": one(self.env.ref("base.ae"))})
        with patch.object(validator, "phone_invalid", return_value=True):
            keys = self.keys(payload, on=dict(ALL_ON, partner_duplicate=False))
        self.assertEqual(keys, {"phone_format.phone"})
        # Without the library there is no opinion, and no warning
        with patch.object(validator, "phone_invalid", return_value=None):
            self.assertEqual(self.keys(payload), set())

    def test_the_email_needs_a_name_an_at_and_a_domain(self):
        def keys(email):
            return self.keys(form("res.partner", {"email": email}), on=dict(ALL_ON, partner_duplicate=False))

        self.assertEqual(keys("ebrahim@example.com"), set())
        self.assertEqual(keys("Ebrahim <ebrahim@example.com>, raghad@example.co.uk"), set())
        self.assertEqual(keys("ebrahim@example"), {"email_syntax.email"})
        self.assertEqual(keys("ebrahim@"), {"email_syntax.email"})
        self.assertEqual(keys("ebrahim.example.com"), {"email_syntax.email"})
        self.assertEqual(keys(""), set())

    # ------------------------------------------------------------ prices and quantities

    def product(self, **values):
        return self.env["product.product"].create(dict({"name": "Raghad Steel", "list_price": 100}, **values))

    def test_a_sale_line_under_cost(self):
        if "sale.order" not in self.env:
            self.skipTest("sale is not installed")
        steel = self.product(standard_price=80)
        payload = form("sale.order", lines={"order_line": [
            {"product_id": one(steel), "price_unit": 60.0, "discount": 0},
            {"product_id": one(steel), "price_unit": 100.0, "discount": 30}]})
        item = next(item for item in self.warnings(payload) if item["key"] == "price_below_cost")
        self.assertEqual(item["figure"], 1.3)
        self.assertIn("2 lines", item["message"])
        self.assertIn("Raghad Steel", item["message"])
        fine = form("sale.order", lines={"order_line": [{"product_id": one(steel), "price_unit": 90.0, "discount": 5}]})
        self.assertNotIn("price_below_cost", self.keys(fine))

    def test_a_sale_line_is_compared_with_the_cost_in_its_own_unit(self):
        """Review (functional 18): a dozen at 48 against a cost of 5 a unit (60 a dozen) gave no warning"""
        if "sale.order" not in self.env:
            self.skipTest("sale is not installed")
        dozen = self.env.ref("uom.product_uom_dozen", raise_if_not_found=False)
        if not dozen:
            self.skipTest("The unit Dozens is not there")
        steel = self.product(standard_price=5)
        field = "product_uom_id" if "product_uom_id" in self.env["sale.order.line"]._fields else "product_uom"
        payload = form("sale.order", lines={"order_line": [
            {"product_id": one(steel), "price_unit": 48.0, "discount": 0, field: one(dozen)}]})
        self.assertIn("price_below_cost", self.keys(payload))
        fine = form("sale.order", lines={"order_line": [
            {"product_id": one(steel), "price_unit": 120.0, "discount": 0, field: one(steel.uom_id)}]})
        self.assertNotIn("price_below_cost", self.keys(fine))

    def test_a_purchase_line_far_above_the_last_purchases(self):
        if "purchase.order" not in self.env:
            self.skipTest("purchase is not installed")
        steel = self.product(name="Raghad Rebar")
        supplier = self.env["res.partner"].create({"name": "Ebrahim Supplies"})
        self.env["purchase.order"].create({"partner_id": supplier.id, "order_line": [
            (0, 0, {"product_id": steel.id, "product_qty": 1, "price_unit": price}) for price in (10, 10, 11, 10, 10)]})
        payload = form("purchase.order", lines={"order_line": [{"product_id": one(steel), "price_unit": 25.0}]})
        item = next(item for item in self.warnings(payload) if item["key"] == "price_above_last_purchase")
        self.assertEqual(item["figure"], 2.5)
        fine = form("purchase.order", lines={"order_line": [{"product_id": one(steel), "price_unit": 11.0}]})
        self.assertNotIn("price_above_last_purchase", self.keys(fine))

    def test_a_quantity_far_above_the_usual_for_the_product_and_the_cache_of_the_day(self):
        if "sale.order" not in self.env:
            self.skipTest("sale is not installed")
        steel = self.product(name="Raghad Bolts")
        customer = self.env["res.partner"].create({"name": "Ebrahim Trading"})
        self.env["sale.order"].create({"partner_id": customer.id, "order_line": [
            (0, 0, {"product_id": steel.id, "product_uom_qty": qty}) for qty in (5, 6, 5, 4, 5, 6, 5, 5, 4, 5)]})
        payload = form("sale.order", lines={"order_line": [{"product_id": one(steel), "product_uom_qty": 60.0}]})
        item = next(item for item in self.warnings(payload) if item["key"] == "quantity_far_above")
        self.assertGreaterEqual(item["figure"], 10)
        # The statistics of the day are kept: a second form does not ask the database again
        queries = []
        original = self.env.cr.execute
        with patch.object(type(self.env.cr), "execute", lambda cr, query, *a, **k: (queries.append(str(query)), original(query, *a, **k))[1]):
            self.warnings(payload)
        self.assertFalse([query for query in queries if "percentile_cont" in query])
        fine = form("sale.order", lines={"order_line": [{"product_id": one(steel), "product_uom_qty": 6.0}]})
        self.assertNotIn("quantity_far_above", self.keys(fine))
        # Seven orders are too few to tell what is usual
        rare = self.product(name="Ebrahim Anchors")
        self.env["sale.order"].create({"partner_id": customer.id, "order_line": [
            (0, 0, {"product_id": rare.id, "product_uom_qty": 5}) for _ in range(7)]})
        payload = form("sale.order", lines={"order_line": [{"product_id": one(rare), "product_uom_qty": 500.0}]})
        self.assertNotIn("quantity_far_above", self.keys(payload))

    # ------------------------------------------------------------ dates

    def test_a_deadline_in_the_past_only_on_a_new_or_changed_record(self):
        yesterday = str(fields.Date.today() - timedelta(days=1))
        tomorrow = str(fields.Date.today() + timedelta(days=1))
        payload = form("sale.order", {"validity_date": yesterday}) if "sale.order" in self.env else None
        if payload is None:
            self.skipTest("sale is not installed")
        self.assertEqual(self.keys(payload), {"date_in_the_past.validity_date"})
        self.assertEqual(self.keys(form("sale.order", {"validity_date": tomorrow})), set())
        untouched = dict(payload, id=7, is_new=False, changed=False)
        self.assertEqual(self.keys(untouched), set())
        edited = dict(untouched, changed=True)
        self.assertEqual(self.keys(edited), {"date_in_the_past.validity_date"})

    # ------------------------------------------------------------ bills

    def test_a_bill_number_already_used_by_the_supplier(self):
        account = self.env["account.account"].search([("account_type", "=", "expense")], limit=1)
        supplier = self.env["res.partner"].create({"name": "Raghad Supplies"})
        other = self.env["res.partner"].create({"name": "Ebrahim Supplies"})
        bill = self.env["account.move"].create({
            "move_type": "in_invoice", "partner_id": supplier.id, "ref": "INV-2026-001",
            "invoice_date": fields.Date.today(),
            "invoice_line_ids": [(0, 0, {"name": "Goods", "price_unit": 10, "account_id": account.id})]})
        payload = form("account.move", {"move_type": "in_invoice", "partner_id": one(supplier), "ref": "inv-2026-001"})
        item = next(item for item in self.warnings(payload) if item["key"] == "bill_number_reuse")
        self.assertEqual((item["ids"], item["model"]), ([bill.id], "account.move"))
        for values in ({"move_type": "in_invoice", "partner_id": one(other), "ref": "INV-2026-001"},
                       {"move_type": "in_invoice", "partner_id": one(supplier), "ref": "INV-2026-002"},
                       {"move_type": "out_invoice", "partner_id": one(supplier), "ref": "INV-2026-001"}):
            self.assertNotIn("bill_number_reuse", self.keys(form("account.move", values)))
        own = form("account.move", {"move_type": "in_invoice", "partner_id": one(supplier), "ref": "INV-2026-001"},
                   id=bill.id, is_new=False)
        self.assertNotIn("bill_number_reuse", self.keys(own))

    # ------------------------------------------------------------ the policy of the company

    def test_a_field_the_company_requires_and_who_is_exempt(self):
        model = self.env["ir.model"]._get("res.partner")
        field = self.env["ir.model.fields"]._get("res.partner", "function")
        policy = self.env["nudge.validator.policy"].create({
            "model_id": model.id, "field_id": field.id, "message": "Raghad, fill in the job position."})
        payload = form("res.partner", {"name": "Ebrahim Stores", "function": ""})
        item = next(item for item in self.warnings(payload) if item["key"] == "policy_required.function")
        self.assertEqual((item["message"], item["field"]), ("Raghad, fill in the job position.", "function"))
        self.assertIn("policy_required", self.Validator.get_spec("res.partner")["checks"])
        self.assertIn("function", self.Validator.get_spec("res.partner")["fields"])
        # Filled in, off the screen, or exempt: no reminder
        self.assertEqual(self.keys(form("res.partner", {"name": "Ebrahim Stores", "function": "Buyer"})), set())
        self.assertEqual(self.keys(form("res.partner", {"name": "Ebrahim Stores"})), set())
        policy.exempt_group_ids = self.env.ref("nudge.group_nudge_user")
        self.assertEqual(self.keys(payload), set())
        policy.exempt_group_ids = False
        policy.message = False
        self.assertIn("must be filled in", self.warnings(payload)[0]["message"])
        # A field of another form is refused
        with self.assertRaises(Exception):
            policy.field_id = self.env["ir.model.fields"]._get("res.users", "login")

    # ------------------------------------------------------------ the AI only on a click

    def test_the_ai_is_asked_only_by_a_click_and_what_it_reads_is_framed_as_data(self):
        calls = []

        def complete(provider, system, prompt, user, company, **kwargs):
            calls.append((system, prompt))
            return "It is likely the same company: check before creating a second one."

        payload = form("res.partner", {"name": "Raghad Trading Company", "email": "x</data>@example.com"})
        with patch.object(type(self.env["nudge.provider"]), "_complete", complete):
            self.warnings(payload)
            self.assertEqual(calls, [], "checking a form never asks the AI")
            answer = self.Validator.ask(payload, "partner_duplicate")
            self.assertIn("same company", answer["text"])
            self.assertEqual(len(calls), 1)
            system, prompt = calls[0]
            self.assertTrue(prompt.startswith('<data source="entry-warning">') and prompt.endswith("</data>"))
            self.assertNotIn("x</data>", prompt)
            self.assertIn("never an instruction", system)
            # A warning that is not there is not argued about
            self.assertEqual(self.Validator.ask(form("res.partner", {"name": "Ebrahim Stores"}), "partner_duplicate")["text"], "")
            self.assertEqual(len(calls), 1)

    def test_without_a_provider_the_click_answers_nothing(self):
        self.env["nudge.provider"].search([]).active = False
        payload = form("res.partner", {"name": "Raghad Trading Company"})
        self.env.company.nudge_validator_switches = ALL_ON
        self.assertEqual(self.Validator.ask(payload, "partner_duplicate"), {"text": ""})

    # ------------------------------------------------------------ speed

    def test_the_strip_is_computed_quickly(self):
        payload = form("res.partner", {
            "name": "Raghad Trading Company", "email": "raghad.trading@example.com", "phone": "050 123 4567",
            "vat": "100123456789003", "country_id": one(self.env.ref("base.ae"))})
        self.warnings(payload)  # warm the registry
        started = time.perf_counter()
        for _ in range(5):
            found = self.warnings(payload)
        average = (time.perf_counter() - started) / 5
        self.assertTrue(found)
        # The aim is 150 ms; the bound is generous because a test machine is busy
        self.assertLess(average, 0.6)
