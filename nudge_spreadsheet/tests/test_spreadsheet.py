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
        self.assertEqual(data["version"], 21)
        sheet = data["sheets"][0]
        self.assertEqual(sheet["name"], "Salaries")
        cells = sheet["cells"]
        self.assertEqual(cells["A1"]["content"], "Department")
        self.assertEqual(sheet["styles"]["A1"], workbook.HEADER_STYLE)
        self.assertEqual(cells["B2"]["content"], "120000")
        self.assertEqual(cells["C2"]["content"], "125500.5")
        self.assertNotIn("C4", cells, "An empty value is an empty cell")
        self.assertEqual(cells["B5"]["content"], "=SUM(B2:B4)")
        self.assertEqual(cells["A5"]["content"], "Total")
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
        self.assertEqual(figure["x"], 5 * workbook.COLUMN_WIDTH)
        self.assertEqual(sheet["tables"][0]["range"], "A1:D4")
        # What the spreadsheet module checks on save
        self.env["nudge.spreadsheet"].create({"name": "t", "spreadsheet_data": json.dumps(data)})

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
        self.assertEqual(data["sheets"][0]["cells"]["B2"]["content"], "120000")
        self.assertIn("3 rows", result.content)
        self.assertTrue(tools.base.TOOLS["make_spreadsheet"].terminal)

        # Theirs: another person does not see it
        with self.assertRaises(AccessError):
            spreadsheet.with_user(self.user_a).read(["name"])
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
