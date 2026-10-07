import json

from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.tests.common import NudgeCase
from odoo.addons.nudge_spreadsheet.tests.test_spreadsheet import SHEET


@tagged("post_install", "-at_install")
class TestDocuments(NudgeCase, TransactionCase):
    """A spreadsheet Nudge makes lands in Documents."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent.write({"group_ids": [(4, cls.env.ref("documents.group_documents_user").id)]})

    def make(self, user):
        return tools.base.call(self.env(user=user), "make_spreadsheet", {
            "title": "Salaries: September vs October",
            "sheets": [SHEET],
            "text": "Here is the comparison.",
        }, None, "agent")

    def test_the_spreadsheet_goes_to_documents(self):
        before = self.env["nudge.spreadsheet"].sudo().search_count([])
        result = self.make(self.agent)
        self.assertFalse(result.is_error, result.content)
        self.assertIn("in Documents", result.content)
        action = result.event["action"]
        self.assertEqual(result.event["type"], "open_action")
        self.assertEqual(action.get("type"), "ir.actions.client")
        self.assertIn("spreadsheet", json.dumps(action).lower())
        document = self.env["documents.document"].sudo().search(
            [("handler", "=", "spreadsheet"), ("name", "=", "Salaries: September vs October")],
            order="id desc", limit=1)
        self.assertTrue(document)
        data = json.loads(document.spreadsheet_data)
        self.assertEqual(data["sheets"][0]["cells"]["B2"], "120000")
        self.assertEqual(data["sheets"][0]["figures"][0]["tag"], "chart")
        self.assertEqual(document.create_uid, self.agent)
        self.assertEqual(self.env["nudge.spreadsheet"].sudo().search_count([]), before,
                         "Nothing is kept under Nudge when Documents takes it")
        # The person reaches their own document
        self.assertTrue(document.with_user(self.agent).has_access("read"))

    def test_without_documents_access_it_stays_under_nudge(self):
        user = self.agent.copy({"login": "nudge_g2", "name": "No documents"})
        user.write({"group_ids": [(3, self.env.ref("documents.group_documents_user").id)]})
        if self.env["documents.document"].with_user(user).has_access("create"):
            self.skipTest("Every internal user may create documents here")
        result = self.make(user)
        self.assertFalse(result.is_error, result.content)
        self.assertIn("Nudge", result.content)
        self.assertEqual(result.event["action"]["tag"], "nudge_spreadsheet.open")
