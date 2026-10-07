import io

from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import attachments

from .common import NudgeCase

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def xlsx(rows, title="Contacts"):
    """:return: the bytes of an Excel file with one sheet holding ``rows``"""
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = title
    for row in rows:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


@tagged("post_install", "-at_install")
class TestImport(NudgeCase, TransactionCase):
    """A file is tried before it is imported; nothing is saved."""

    def send(self, user, name, data, mimetype):
        """Send a file with a question, as the controller does.
        :return: (environment of the tools, question)"""
        conversation = self.env["nudge.conversation"].with_user(user)._chat_open(None)
        job = conversation._chat_prepare("Check this file", attachment=(name, data, mimetype))
        conversation.sudo().write({"stream_started_at": False})
        question = conversation.sudo().message_ids.filtered(lambda message: message.role == "user")[-1]
        env = self.env(user=user, context={"nudge_conversation_id": conversation.id})
        return env, question, job

    def try_import(self, env, arguments, mode="talk"):
        return tools.base.call(env, "test_import", arguments, None, mode)

    def test_an_excel_file_is_read_as_text(self):
        data = xlsx([["Name", "Email"], ["Nudge One", "one@nudge.example"], ["Nudge Two", None]])
        self.assertEqual(attachments.kind_of("contacts.xlsx", XLSX), "sheet")
        self.assertEqual(attachments.kind_of("contacts.xlsx", ""), "sheet")
        found = attachments.read_attachment("contacts.xlsx", data, XLSX)
        self.assertEqual(found["kind"], "sheet")
        self.assertIn("## Sheet 'Contacts': 3 row(s), 2 column(s)", found["text"])
        self.assertIn("Name | Email", found["text"])
        self.assertIn("Nudge Two |", found["text"])
        self.assertIn("An Excel file", found["note"])
        with self.assertRaises(attachments.Unreadable):
            attachments.read_attachment("broken.xlsx", b"not a workbook", XLSX)

    def test_the_file_is_kept_with_the_question_but_not_as_an_image(self):
        data = xlsx([["Name"], ["Nudge One"]])
        env, question, job = self.send(self.user_contacts, "contacts.xlsx", data, XLSX)
        self.assertEqual(question.attachment_id.mimetype, XLSX)
        self.assertEqual(job["turns"][-1]["images"], [], "A spreadsheet is not looked at as an image")
        self.assertIn("<nudge_attachment name=\"contacts.xlsx\">", question.context_text)
        self.assertIn("Sheet 'Contacts'", question.context_text)
        env, question, _job = self.send(self.user_contacts, "items.csv", b"Name,Email\nNudge,n@nudge.example\n", "text/csv")
        self.assertEqual(question.attachment_id.mimetype, "text/csv")

    def test_the_file_is_tried_and_nothing_is_saved(self):
        if "base_import.import" not in self.env:
            self.skipTest("base_import is not installed")
        data = xlsx([
            ["Name", "Email", "Country", "Zzz Column"],
            ["Nudge Import One", "one@nudge.example", "Belgium", "a"],
            ["Nudge Import Two", "two@nudge.example", "Nowhere", "b"],
        ])
        env, _question, _job = self.send(self.user_contacts, "contacts.xlsx", data, XLSX)
        Partner = self.env["res.partner"]
        before = Partner.search_count([])
        result = self.try_import(env, {"model": "res.partner"})
        self.assertFalse(result.is_error, result.content)
        self.assertIn("Tried 'contacts.xlsx', sheet 'Contacts' into Contact (res.partner): 2 data row(s).", result.content)
        self.assertIn("Columns matched: Name → name; Email → email; Country → country_id", result.content)
        self.assertIn("ignored: Zzz Column", result.content)
        self.assertIn("Nowhere", result.content)
        self.assertIn("row 3", result.content)
        self.assertIn("column 'Country'", result.content)
        self.assertIn("Nothing was saved.", result.content)
        self.assertEqual(Partner.search_count([]), before)
        self.assertFalse(Partner.search([("name", "like", "Nudge Import")]))

        # A column Odoo could not match is named
        result = self.try_import(env, {"model": "res.partner", "mapping": [{"column": "zzz column", "field": "comment"}]})
        self.assertIn("Zzz Column → comment", result.content)
        self.assertNotIn("ignored", result.content)
        refused = self.try_import(env, {"model": "res.partner", "mapping": [{"column": "Nope", "field": "comment"}]})
        self.assertTrue(refused.is_error)
        self.assertIn("No column 'Nope'", refused.content)
        refused = self.try_import(env, {"model": "res.partner", "mapping": [{"column": "Name", "field": "nowhere"}]})
        self.assertTrue(refused.is_error)
        self.assertIn("describe_model", refused.content)

    def test_a_clean_file_says_so(self):
        if "base_import.import" not in self.env:
            self.skipTest("base_import is not installed")
        data = xlsx([["Name", "Email"], ["Nudge Clean One", "clean@nudge.example"]])
        env, _question, _job = self.send(self.user_contacts, "contacts.xlsx", data, XLSX)
        result = self.try_import(env, {"model": "res.partner"})
        self.assertFalse(result.is_error, result.content)
        self.assertIn("No problem: 1 record(s) would be imported.", result.content)
        self.assertFalse(self.env["res.partner"].search([("name", "=", "Nudge Clean One")]))
        # A CSV file too, in Agent mode as well
        env, _question, _job = self.send(self.agent, "items.csv", b"Name,Email\nNudge Csv,csv@nudge.example\n", "text/csv")
        result = self.try_import(env, {"model": "res.partner"}, mode="agent")
        self.assertFalse(result.is_error, result.content)
        self.assertIn("No problem", result.content)

    def test_without_a_file_or_a_right(self):
        if "base_import.import" not in self.env:
            self.skipTest("base_import is not installed")
        env = self.env(user=self.user_contacts)
        refused = self.try_import(env, {"model": "res.partner"})
        self.assertTrue(refused.is_error)
        self.assertIn("attach", refused.content)
        data = xlsx([["Name"], ["Nudge One"]])
        env, _question, _job = self.send(self.user_a, "contacts.xlsx", data, XLSX)
        refused = self.try_import(env, {"model": "res.partner"})
        self.assertTrue(refused.is_error, "No right to create contacts: no import to try")
        # An image is not a file to import
        env, _question, _job = self.send(self.user_contacts, "photo.png", b"\x89PNG\r\n\x1a\n" + b"0" * 40, "image/png")
        refused = self.try_import(env, {"model": "res.partner"})
        self.assertTrue(refused.is_error)
        self.assertIn("attach", refused.content)
