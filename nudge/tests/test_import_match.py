import csv
import io
import json

from odoo import http
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged
from odoo.tests.common import new_test_user

from odoo.addons.nudge import tools
from odoo.addons.nudge.tools import import_match

from .common import NudgeCase
from .test_import import XLSX, xlsx

CSV = "text/csv"


def csv_bytes(rows):
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue().encode()


def attach(env, user, name, data):
    """:return: the environment of the tools, in a conversation holding the file"""
    conversation = env["nudge.conversation"].with_user(user)._chat_open(None)
    conversation._chat_prepare("Check this file", attachment=(name, data, CSV))
    conversation.sudo().write({"stream_started_at": False})
    return env(user=user, context={"nudge_conversation_id": conversation.id})


def by_header(columns):
    return {column["column"]: column for column in columns}


@tagged("post_install", "-at_install")
class TestKinds(TransactionCase):
    """What the values of a column look like."""

    def test_the_kind_of_a_column(self):
        self.assertIn("date", import_match.column_kinds(["2026-01-31", "2026-02-01"]))
        self.assertIn("date", import_match.column_kinds(["31/01/2026", "01/02/2026"]))
        self.assertIn("email", import_match.column_kinds(["raghad@nudge.example"]))
        self.assertIn("phone", import_match.column_kinds(["+971 50 123 4567", "0501234567"]))
        self.assertIn("integer", import_match.column_kinds(["12", "1,200"]))
        self.assertNotIn("integer", import_match.column_kinds(["12.5", "3"]))
        self.assertIn("float", import_match.column_kinds(["12.5", "3"]))
        self.assertIn("boolean", import_match.column_kinds(["yes", "No", "نعم"]))
        self.assertIsNone(import_match.column_kinds(["", None]))
        self.assertNotIn("date", import_match.column_kinds(["2026-01-31", "Ebrahim"]))

    def test_a_field_takes_only_the_values_that_fit(self):
        number = {"type": "float"}
        date = {"type": "date"}
        choice = {"type": "selection", "options": [("male", "Male"), ("female", "Female")]}
        place = {"type": "many2one"}
        self.assertTrue(import_match.compatible(number, import_match.column_kinds(["1", "2.5"]), []))
        self.assertFalse(import_match.compatible(number, import_match.column_kinds(["abc"]), []))
        self.assertTrue(import_match.compatible(date, import_match.column_kinds(["2026-01-31"]), []))
        self.assertFalse(import_match.compatible(date, import_match.column_kinds(["Raghad"]), []))
        self.assertTrue(import_match.compatible(choice, None, ["Male", "female"]))
        self.assertFalse(import_match.compatible(choice, None, ["Male", "other"]))
        self.assertTrue(import_match.compatible(place, import_match.column_kinds(["Belgium"]), []))
        self.assertFalse(import_match.compatible(place, import_match.column_kinds(["2026-01-31"]), []))


@tagged("post_install", "-at_install")
class TestMatching(NudgeCase, TransactionCase):
    """The columns of a file matched to fields, then tried; nothing is saved."""

    def setUp(self):
        super().setUp()
        if "base_import.import" not in self.env:
            self.skipTest("base_import is not installed")

    def columns(self, model, headers, rows, user=None):
        env = self.env(user=user or self.user_contacts)
        data = csv_bytes([headers] + rows)
        result = import_match.card(env, model, "file.csv", data, CSV)
        return by_header(result["columns"]), result

    @staticmethod
    def names(result):
        return {field["name"] for field in result["fields"]}

    def test_names_labels_synonyms_and_spellings(self):
        columns, result = self.columns(
            "res.partner",
            ["Customer Name", "E-mail", "Telephone", "Cell", "Town", "Contry", "Weird Column"],
            [["Raghad", "raghad@nudge.example", "+971 4 123 4567", "+971 50 123 4567", "Dubai", "Belgium", "x"]])
        self.assertEqual(columns["Customer Name"]["field"], "name")
        self.assertEqual(columns["E-mail"]["field"], "email")
        self.assertEqual(columns["Telephone"]["field"], "phone")
        # Odoo 19 has no separate mobile on a contact
        self.assertEqual(columns["Cell"]["field"], "mobile" if "mobile" in self.names(result) else False)
        self.assertEqual(columns["Town"]["field"], "city")
        self.assertEqual(columns["Contry"]["field"], "country_id", "a close spelling")
        self.assertFalse(columns["Weird Column"]["field"])
        self.assertEqual(columns["Weird Column"]["level"], "none")
        self.assertEqual(columns["E-mail"]["sample"], ["raghad@nudge.example"])
        self.assertGreater(columns["Customer Name"]["confidence"], columns["Contry"]["confidence"])

    def test_the_exact_name_and_the_label_in_the_language_of_the_person(self):
        columns, _result = self.columns("res.partner", ["name", "Phone", "Website"], [["Ebrahim", "+971 4 123 4567", "https://nudge.example"]])
        self.assertEqual(columns["name"]["confidence"], 100)
        self.assertEqual(columns["Phone"]["field"], "phone")
        self.assertEqual(columns["Website"]["field"], "website")
        self.env["res.lang"]._activate_lang("ar_001")
        arabic = self.user_contacts.copy({"login": "nudge_ar", "lang": "ar_001"})
        columns, _result = self.columns("res.partner", ["الاسم", "البريد الالكتروني", "الهاتف"],
                                       [["رغد", "r@nudge.example", "+971 4 123 4567"]], user=arabic)
        self.assertEqual(columns["الاسم"]["field"], "name")
        self.assertEqual(columns["البريد الالكتروني"]["field"], "email")
        self.assertEqual(columns["الهاتف"]["field"], "phone")

    def test_the_values_decide_when_the_header_says_little(self):
        columns, _result = self.columns(
            "res.partner", ["Name", "Contact"], [["Raghad", "raghad@nudge.example"], ["Ebrahim", "ebrahim@nudge.example"]])
        self.assertEqual(columns["Contact"]["field"], "email")
        self.assertIn("emails", columns["Contact"]["reason"])
        # A column of names does not go to a date field: the header "Birth" is close to "birthday"
        hr_officer = (new_test_user(self.env, login="nudge_hr", groups="nudge.group_nudge_user,hr.group_hr_manager")
                      if "hr.employee" in self.env else None)
        employees, _result = self.columns(
            "hr.employee", ["Employee", "Birth", "Date of Birth"],
            [["Raghad", "Ebrahim", "1990-05-01"]], user=hr_officer) if hr_officer else ({}, None)
        if employees:
            self.assertEqual(employees["Date of Birth"]["field"], "birthday")
            self.assertFalse(employees["Birth"]["field"] == "birthday")

    def test_no_two_columns_take_the_same_field(self):
        columns, _result = self.columns(
            "res.partner", ["Email", "E-mail"], [["a@nudge.example", "b@nudge.example"]])
        fields = [column["field"] for column in columns.values() if column["field"]]
        self.assertEqual(len(fields), len(set(fields)))
        self.assertEqual(columns["Email"]["field"], "email")

    def test_the_common_models_have_their_words(self):
        samples = {
            "product.template": (["Product Name", "SKU", "Sales Price", "Cost", "Barcode"],
                                 {"Product Name": "name", "SKU": "default_code", "Sales Price": "list_price",
                                  "Cost": "standard_price", "Barcode": "barcode"}),
            "sale.order": (["Customer", "Order Date"], {"Customer": "partner_id", "Order Date": "date_order"}),
            "purchase.order": (["Vendor", "Vendor Reference"], {"Vendor": "partner_id", "Vendor Reference": "partner_ref"}),
            "account.move": (["Partner", "Invoice Date", "Due Date"],
                             {"Partner": "partner_id", "Invoice Date": "invoice_date", "Due Date": "invoice_date_due"}),
            "hr.employee": (["Employee Name", "Department", "Mobile"],
                            {"Employee Name": "name", "Department": "department_id", "Mobile": "mobile_phone"}),
        }
        for model, (headers, expected) in samples.items():
            if model not in self.env or not self.env["ir.model.access"].check(model, "create", False):
                continue
            row = {"Product Name": "Bolt", "SKU": "B1", "Sales Price": "1.5", "Cost": "1", "Barcode": "123456",
                   "Customer": "Raghad", "Order Date": "2026-01-31", "Vendor": "Ebrahim", "Vendor Reference": "V1",
                   "Partner": "Raghad", "Invoice Date": "2026-01-31", "Due Date": "2026-02-28",
                   "Employee Name": "Ebrahim", "Department": "Admin", "Mobile": "+971 50 123 4567"}
            columns, _result = self.columns(model, headers, [[row[header] for header in headers]], user=self.env.ref("base.user_admin"))
            for header, field in expected.items():
                if field in {entry["name"] for entry in _result["fields"]}:
                    self.assertEqual(columns[header]["field"], field, f"{model}: {header}")

    def test_the_card_changes_and_tries_the_file_and_saves_nothing(self):
        env = self.env(user=self.user_contacts)
        data = csv_bytes([["Name", "Email", "Country", "Zzz"],
                          ["Nudge Match One", "one@nudge.example", "Belgium", "a"],
                          ["Nudge Match Two", "two@nudge.example", "Nowhere", "b"]])
        before = self.env["res.partner"].search_count([])
        result = import_match.card(env, "res.partner", "contacts.csv", data, CSV, check=True)
        self.assertEqual(result["rows"], 2)
        self.assertIn("Nowhere", "\n".join(result["check"]["problems"]))
        self.assertIn("column 'Country'", "\n".join(result["check"]["problems"]))
        self.assertGreaterEqual(result["check"]["errors"], 1)
        # The person says what the last column is: it is part of the trial
        index = next(column["index"] for column in result["columns"] if column["column"] == "Zzz")
        changed = import_match.card(env, "res.partner", "contacts.csv", data, CSV,
                                    [{"index": index, "field": "comment"}], check=True)
        column = by_header(changed["columns"])["Zzz"]
        self.assertEqual((column["field"], column["level"]), ("comment", "chosen"))
        self.assertEqual(self.env["res.partner"].search_count([]), before)
        with self.assertRaises(import_match.Refused):
            import_match.card(env, "res.partner", "contacts.csv", data, CSV, [{"index": 0, "field": "nope"}])
        clean = import_match.card(env, "res.partner", "c.csv", csv_bytes([["Name"], ["Nudge Match Clean"]]), CSV, check=True)
        self.assertEqual(clean["check"]["problems"], [])
        self.assertEqual(clean["check"]["records"], 1)
        self.assertFalse(self.env["res.partner"].search([("name", "like", "Nudge Match")]))

    def test_an_excel_file_is_matched_too(self):
        env = self.env(user=self.user_contacts)
        data = xlsx([["Customer Name", "Mail"], ["Raghad", "raghad@nudge.example"]])
        result = import_match.card(env, "res.partner", "contacts.xlsx", data, XLSX, check=True)
        self.assertEqual([column["field"] for column in result["columns"]], ["name", "email"])
        self.assertEqual(result["check"]["problems"], [])

    def test_the_model_is_asked_for_when_it_is_not_known(self):
        env = self.env(user=self.env.ref("base.user_admin"))
        data = csv_bytes([["Customer Name", "Email", "Phone"], ["Raghad", "r@nudge.example", "+971 4 123 4567"]])
        result = import_match.card(env, "", "contacts.csv", data, CSV)
        self.assertEqual(result["choose_model"][0]["model"], "res.partner")
        self.assertTrue(len(result["choose_model"]) <= import_match.MAX_CHOICES)
        self.assertNotIn("columns", result)

    def test_the_corrected_file_is_mapped_by_the_standard_importer(self):
        env = self.env(user=self.user_contacts)
        data = csv_bytes([["Customer Name", "Town", "Telephone"], ["Raghad", "Dubai", "+971 4 123 4567"]])
        result = import_match.card(env, "res.partner", "contacts.csv", data, CSV)
        mapping = [{"index": column["index"], "field": column["field"] or ""} for column in result["columns"]]
        fixed = import_match.corrected_csv(env, env["res.partner"], "contacts.csv", data, CSV, mapping)
        rows = list(csv.reader(io.StringIO(fixed.decode())))
        self.assertEqual(rows[0], ["Name", "City", "Phone"])
        self.assertEqual(rows[1], ["Raghad", "Dubai", "+971 4 123 4567"])
        # What the importer of Odoo itself makes of the new header row
        again = tools.importing.read_columns(env, env["res.partner"], "fixed.csv", fixed, CSV)
        self.assertEqual(again["fields"], ["name", "city", "phone"])

    def test_the_tool_gives_a_first_mapping_to_the_ai(self):
        env = attach(self.env, self.user_contacts, "contacts.csv", csv_bytes([["Customer Name", "Mail"], ["Raghad", "r@nudge.example"]]))
        result = tools.base.call(env, "match_import_columns", {"model": "res.partner"}, None, "talk")
        self.assertFalse(result.is_error, result.content)
        self.assertIn("'Customer Name' → name", result.content)
        self.assertIn("'Mail' → email", result.content)
        self.assertIn("test_import", result.content)
        self.assertIn("match_import_columns", tools.base.TALK_TOOLS)
        refused = tools.base.call(env, "match_import_columns", {"model": "res.users"}, None, "talk")
        self.assertTrue(refused.is_error)


@tagged("post_install", "-at_install")
class TestToolAndCommand(NudgeCase, TransactionCase):
    """The /import command, and the tool the AI calls."""

    def test_the_command_is_listed_in_both_languages(self):
        quick = self.env["nudge.quick"].with_user(self.user_a)
        listed = {command["name"]: command for command in quick.list_commands()}
        self.assertIn("import", listed)
        self.assertEqual(set(listed["import"]["words"]), {"/import", "/استيراد"})
        for word in ("/import", "/استيراد"):
            answer = quick.run_command(word)
            self.assertEqual(answer["command"], "import")
            self.assertIn("Attach", answer["content"])


@tagged("post_install", "-at_install")
class TestImportRoute(NudgeCase, HttpCase):
    """The route the panel posts the file to."""

    def post(self, user, step, **fields):
        self.authenticate(user.login, user.login)
        token = http.Request.csrf_token(self)
        data = {"csrf_token": token, "step": step, **{key: value for key, value in fields.items() if key != "file"}}
        files = {"file": ("people.csv", fields["file"], CSV)} if fields.get("file") is not None else None
        return self.url_open("/nudge/import/file", data=data, files=files)

    def test_match_check_and_csv(self):
        if "base_import.import" not in self.env:
            self.skipTest("base_import is not installed")
        data = csv_bytes([["Customer Name", "Telephone", "Country"], ["Raghad", "+971 4 123 4567", "Nowhere"]])
        before = self.env["res.partner"].search_count([])
        answer = self.post(self.user_contacts, "match", file=data, model="res.partner").json()
        self.assertEqual([column["field"] for column in answer["columns"]], ["name", "phone", "country_id"])
        answer = self.post(self.user_contacts, "check", file=data, model="res.partner").json()
        self.assertIn("Nowhere", "\n".join(answer["check"]["problems"]))
        mapping = json.dumps([{"index": 2, "field": ""}])
        answer = self.post(self.user_contacts, "check", file=data, model="res.partner", mapping=mapping).json()
        self.assertEqual(answer["check"]["problems"], [])
        response = self.post(self.user_contacts, "csv", file=data, model="res.partner", mapping=mapping)
        self.assertIn("text/csv", response.headers["Content-Type"])
        self.assertIn(b"Name,Phone,Country", response.content)
        self.assertEqual(self.env["res.partner"].search_count([]), before)
        # Nothing to match without a file, nothing for the one who may not create
        self.assertIn("error", self.post(self.user_contacts, "match", model="res.partner").json())
        self.assertIn("error", self.post(self.user_a, "match", file=data, model="res.partner").json())
        self.assertEqual(self.post(self.user_contacts, "match", file=b"x" * 10, model="res.partner").status_code, 200)


@tagged("post_install", "-at_install")
class TestImportDenied(NudgeCase, HttpCase):
    """A plain user is told what the file would break; nothing is written."""

    def test_the_route_refuses_a_user_who_may_not_create_and_writes_nothing(self):
        if "base_import.import" not in self.env:
            self.skipTest("base_import is not installed")
        data = csv_bytes([["Customer Name", "Telephone"], ["Raghad", "+971 4 123 4567"]])
        before = self.env["res.partner"].search_count([])
        for user in (self.outsider, self.user_a):
            for step in ("match", "check", "csv"):
                response = TestImportRoute.post(self, user, step, file=data, model="res.partner")
                self.assertNotEqual(response.status_code, 500, (user.login, step))
                if "json" in response.headers.get("Content-Type", ""):
                    self.assertIn("error", response.json(), (user.login, step))
        self.assertEqual(self.env["res.partner"].search_count([]), before)

    def test_the_tool_refuses_a_user_who_may_not_create_the_model(self):
        before = self.env["res.partner"].search_count([])
        env = attach(self.env, self.user_a, "people.csv", csv_bytes([["Customer Name"], ["Ebrahim"]]))
        result = tools.base.call(env, "match_import_columns", {"model": "res.partner"}, None, "talk")
        self.assertTrue(result.is_error, result.content)
        self.assertEqual(self.env["res.partner"].search_count([]), before)
