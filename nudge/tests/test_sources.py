import base64

from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import prompt
from odoo.addons.nudge.models.nudge_source import split_passages

from .common import NudgeCase
from .test_attachments import pdf_with

POLICY = """HR Policy of the company

Annual leave
Every employee has 30 days of annual leave per year, after one year of service.
Leave is requested two weeks ahead and approved by the direct manager.

Working hours
Eight hours a day, six days a week; Friday is off. Overtime is paid at 1.25.

End of service
Twenty one days of wage per year for the first five years, thirty days after.
"""


@tagged("post_install", "-at_install")
class TestSources(NudgeCase, TransactionCase):
    """The company's own files, as a source."""

    def upload(self, name, data, **values):
        return self.env["nudge.source"].with_user(self.manager).create({
            "name": name, "file": base64.b64encode(data), "file_name": name, **values})

    def search(self, query, user=None):
        return tools.base.call(self.env(user=user or self.user_a), "search_company_files", {"query": query})

    def test_a_text_file_is_read_and_searched(self):
        policy = self.upload("HR Policy.txt", POLICY.encode(), purpose="Leave, hours, end of service")
        self.assertEqual(policy.state, "ready")
        self.assertEqual(policy.pages, 1)
        self.assertGreaterEqual(policy.passage_count, 1)
        result = self.search("annual leave days")
        self.assertFalse(result.is_error, result.content)
        self.assertIn("30 days of annual leave", result.content)
        self.assertIn("Source: HR Policy.txt, page 1", result.content)
        nothing = self.search("submarine engines")
        self.assertIn("Nothing in the company files", nothing.content)

    def test_a_pdf_is_read_page_by_page(self):
        policy = self.upload("Purchasing.pdf", pdf_with("Urgent purchases need the approval of the CFO"))
        self.assertEqual(policy.state, "ready")
        self.assertEqual(policy.pages, 1)
        self.assertEqual(policy.mimetype, "application/pdf")
        result = self.search("urgent purchases approval")
        self.assertIn("approval of the CFO", result.content)
        self.assertIn("Purchasing.pdf, page 1", result.content)

    def test_words_written_another_way_are_found(self):
        self.upload("Prices.txt", "Steel pipe 2 inch: 45 AED per metre\nCopper wire: 12 AED".encode())
        result = self.search("pipe metre")
        self.assertIn("45 AED", result.content)
        # A start of a word, which the full-text search does not know, is found by ilike
        result = self.search("cop wir")
        self.assertIn("Copper wire", result.content)

    def test_a_file_for_some_groups(self):
        hr = self.env["res.groups"].create({"name": "Nudge Test HR"})
        self.upload("Payroll Policy.txt", "Salaries are paid on the 28th.".encode(), group_ids=[(4, hr.id)])
        self.upload("Everybody.txt", "The office opens at 8.".encode())
        self.assertIn("The office opens", self.search("office opens").content)
        hidden = self.search("salaries paid")
        self.assertNotIn("28th", hidden.content)
        self.assertNotIn("Payroll", self.env["nudge.source"].with_user(self.user_a)._get_text())
        self.user_a.write({"groups_id": [(4, hr.id)]})
        self.assertIn("28th", self.search("salaries paid").content)
        self.assertIn("Payroll Policy", self.env["nudge.source"].with_user(self.user_a)._get_text())
        # Managers see every file, users cannot change any
        self.assertEqual(len(self.env["nudge.source"].with_user(self.manager).search([])), 2)
        with self.assertRaises(Exception):
            self.env["nudge.source"].with_user(self.user_a).create({"name": "x"})

    def test_what_cannot_be_read_says_so(self):
        broken = self.upload("broken.pdf", b"%PDF-1.4 nothing")
        self.assertEqual(broken.state, "failed")
        self.assertTrue(broken.error)
        odd = self.upload("setup.exe", b"MZ")
        self.assertEqual(odd.state, "failed")
        self.assertIn("Only PDF and text", odd.error)
        empty = self.env["nudge.source"].with_user(self.manager).create({"name": "Later"})
        self.assertEqual(empty.state, "empty")
        self.assertNotIn("Later", self.env["nudge.source"]._get_text())

    def test_the_files_open_the_conversation(self):
        self.assertEqual(self.env["nudge.source"].with_user(self.user_a)._get_text(), "")
        no_files = self.search("anything")
        self.assertIn("No company file", no_files.content)
        self.upload("HR Policy.txt", POLICY.encode(), purpose="Leave and hours")
        text = self.env["nudge.source"].with_user(self.user_a)._get_text()
        self.assertIn("## Company files", text)
        self.assertIn("- HR Policy.txt: Leave and hours", text)
        conversation = self.env["nudge.conversation"].with_user(self.user_a)._chat_open(None)
        job = conversation._chat_prepare("How many leave days do I get?")
        self.assertIn("## Company files", "\n".join(job["system"]))
        self.assertIn("search_company_files", prompt.SYSTEM_PERSONA)
        for mode in ("talk", "agent"):
            names = [tool.name for tool in tools.base.available(self.env(user=self.user_a), mode)]
            self.assertIn("search_company_files", names)

    def test_passages_are_cut_at_paragraphs(self):
        long = "word " * 700
        passages = split_passages([(1, "Short paragraph.\n\n" + long + "\n\nAnother.")])
        self.assertGreaterEqual(len(passages), 3)
        self.assertTrue(all(len(body) <= 1200 for _page, body in passages))
        self.assertEqual(passages[0][1], "Short paragraph.")
        self.assertTrue(passages[-1][1].endswith("Another."), "A short paragraph joins the one before it")
        self.assertEqual(split_passages([(1, "  \n\n ")]), [])
