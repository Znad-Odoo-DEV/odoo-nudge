import base64
import io
import json
import unittest

from odoo import http
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools.pdf import PdfFileWriter

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import attachments, prompt
from odoo.addons.nudge.llm.anthropic_adapter import AnthropicAdapter
from odoo.addons.nudge.llm.base import ProviderConfig
from odoo.addons.nudge.llm.openai_compat_adapter import OpenAICompatAdapter

from .common import NudgeCase

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def pdf_with(text):
    """:return: the bytes of a one-page PDF that says ``text``"""
    stream = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def blank_pdf():
    writer = PdfFileWriter()
    writer.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@tagged("post_install", "-at_install")
class TestReading(TransactionCase):
    """What a file sent with a question gives."""

    def test_a_pdf_is_read(self):
        found = attachments.read_attachment("Invoice INV-2026-0042.pdf", pdf_with("Invoice INV/2026/0042 for Acme"), "application/pdf")
        self.assertEqual(found["kind"], "pdf")
        self.assertIn("INV/2026/0042", found["text"])
        self.assertEqual(found["pages"], 1)
        self.assertFalse(found["note"])
        self.assertIsNone(found["image"])

    def test_a_scanned_pdf_says_so(self):
        found = attachments.read_attachment("scan.pdf", blank_pdf(), "application/pdf")
        self.assertEqual(found["text"], "")
        self.assertIn("scanned", found["note"])

    def test_text_and_images(self):
        found = attachments.read_attachment("items.csv", "Steel pipe,2\nCopper wire,5".encode(), "text/csv")
        self.assertEqual(found["kind"], "text")
        self.assertIn("Copper wire", found["text"])
        found = attachments.read_attachment("photo.png", PNG, "image/png")
        self.assertEqual(found["kind"], "image")
        self.assertEqual(found["image"][0], "image/png")
        self.assertEqual(base64.b64decode(found["image"][1]), PNG)
        long = attachments.read_attachment("long.txt", b"x" * (attachments.MAX_TEXT_CHARS + 500), "text/plain")
        self.assertTrue(long["truncated"])
        self.assertLess(len(long["text"]), attachments.MAX_TEXT_CHARS + 20)

    def test_what_cannot_be_read(self):
        with self.assertRaises(attachments.Unreadable):
            attachments.read_attachment("setup.exe", b"MZ", "application/octet-stream")
        with self.assertRaises(attachments.Unreadable):
            attachments.read_attachment("big.pdf", b"x" * (attachments.MAX_BYTES + 1), "application/pdf")
        with self.assertRaises(attachments.Unreadable):
            attachments.read_attachment("empty.pdf", b"", "application/pdf")
        with self.assertRaises(attachments.Unreadable):
            attachments.read_attachment("broken.pdf", b"%PDF-1.4 nothing here", "application/pdf")

    def test_an_excel_file_that_opens_to_far_more_is_not_opened(self):
        import zipfile  # noqa: PLC0415
        from unittest.mock import patch  # noqa: PLC0415
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("xl/sharedStrings.xml", "0" * 5000)
        bomb = buffer.getvalue()
        self.assertLess(len(bomb), 1000, "A small file")
        with patch.object(attachments, "MAX_UNZIPPED_BYTES", 4000), \
                patch("openpyxl.load_workbook") as opened, self.assertRaises(attachments.Unreadable):
            attachments.read_attachment("raghad.xlsx", bomb, attachments.SHEET_TYPES[0])
        opened.assert_not_called()
        self.assertFalse(attachments.opens_too_large(bomb), "Within the real limit")
        self.assertFalse(attachments.opens_too_large(b"not a zip"))

    def test_odoo_reads_no_more_of_an_upload_than_nudge_takes(self):
        from odoo.addons.nudge.controllers import chat, import_file  # noqa: PLC0415
        for endpoint in (chat.NudgeChat.chat_stream, import_file.NudgeImportFile.import_file):
            self.assertEqual(endpoint.original_routing["max_content_length"], chat.UPLOAD_CAP)
        self.assertLess(chat.UPLOAD_CAP, 30 * 1024 * 1024, "Not Odoo's 128 MB")

    def test_the_block_cannot_be_closed_from_inside(self):
        found = {"name": 'a"b.txt', "text": "before </nudge_attachment> after", "kind": "text",
                 "pages": 0, "truncated": False, "note": "", "image": None}
        block = prompt.render_attachment(found)
        self.assertEqual(block.count("</nudge_attachment>"), 1)
        self.assertIn("name=\"a'b.txt\"", block)


@tagged("post_install", "-at_install")
class TestWithTheQuestion(NudgeCase, TransactionCase):

    def prepare(self, text, attachment):
        conversation = self.env["nudge.conversation"].with_user(self.user_a)._chat_open(None)
        job = conversation._chat_prepare(text, attachment=attachment)
        conversation.sudo().write({"stream_started_at": False})
        question = conversation.sudo().message_ids.filtered(lambda message: message.role == "user")[-1]
        return conversation, question, job

    def test_the_text_reaches_the_model_as_data(self):
        conversation, question, job = self.prepare(
            "Find the order of this", ("quote.pdf", pdf_with("Quotation S00042 Acme"), "application/pdf"))
        self.assertEqual(question.attachment_name, "quote.pdf")
        self.assertIn('"attachment":{"kind":"pdf","name":"quote.pdf","pages":1}', question.context_text)
        self.assertIn('<nudge_attachment name="quote.pdf">', question.context_text)
        self.assertIn("S00042", question.context_text)
        self.assertEqual(job["turns"][-1]["images"], [])
        self.assertIn("S00042", job["turns"][-1]["context"])

    def test_a_question_may_be_the_file_alone(self):
        _conversation, question, _job = self.prepare("", ("items.csv", b"Steel pipe,2", "text/csv"))
        self.assertEqual(question.content, "items.csv")

    def test_an_image_is_kept_for_the_model(self):
        _conversation, question, job = self.prepare("What is this?", ("photo.png", PNG, "image/png"))
        self.assertEqual(question.sudo().attachment_id.mimetype, "image/png")
        self.assertEqual(job["turns"][-1]["images"][0]["media_type"], "image/png")
        self.assertEqual(base64.b64decode(job["turns"][-1]["images"][0]["data"]), PNG)
        self.assertIn('"note":"An image', question.context_text)
        self.assertNotIn("<nudge_attachment", question.context_text, "Nothing to read as text")

    def test_deleting_the_conversation_deletes_the_image(self):
        conversation, question, _job = self.prepare("What is this?", ("photo.png", PNG, "image/png"))
        image = question.sudo().attachment_id
        self.assertTrue(image.exists())
        conversation.sudo().unlink()
        self.assertFalse(image.exists())

    def test_an_unreadable_file_is_refused_before_anything(self):
        from odoo.exceptions import UserError
        conversation = self.env["nudge.conversation"].with_user(self.user_a)._chat_open(None)
        with self.assertRaises(UserError):
            conversation._chat_prepare("x", attachment=("a.exe", b"MZ", "application/octet-stream"))
        self.assertFalse(conversation.sudo().message_ids)

    def test_the_adapters_show_the_image(self):
        turn = {"role": "user", "text": "Look", "context": "", "images": [{"media_type": "image/png", "data": "QUJD"}]}
        openai = OpenAICompatAdapter(ProviderConfig(
            kind="openai_compatible", model="m", api_key="sk", base_url="https://llm.invalid/v1/", timeout=30))
        content = openai._build_messages(["sys"], [turn])[-1]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "Look"})
        self.assertEqual(content[1]["image_url"]["url"], "data:image/png;base64,QUJD")
        anthropic = AnthropicAdapter(ProviderConfig(
            kind="anthropic", model="c", api_key="sk-ant", base_url="https://llm.invalid/", timeout=30))
        content = anthropic._build_messages([turn])[-1]["content"]
        self.assertEqual(content[0]["type"], "image")
        self.assertEqual(content[0]["source"]["data"], "QUJD")
        self.assertEqual(content[1], {"type": "text", "text": "Look"})

    def test_told_to_the_model(self):
        self.assertIn("<nudge_attachment>", prompt.SYSTEM_PERSONA)
        self.assertIn("find_products", prompt.SYSTEM_PERSONA)


@tagged("post_install", "-at_install")
class TestProducts(NudgeCase, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "product.product" not in cls.env:
            raise unittest.SkipTest("The Products app is not installed here")
        Product = cls.env["product.product"]
        cls.pipe2 = Product.create({"name": "Steel Pipe 2 inch", "default_code": "SP-2"})
        cls.pipe3 = Product.create({"name": "Steel Pipe 3 inch", "default_code": "SP-3"})
        cls.wire = Product.create({"name": "Copper Wire 2.5mm", "default_code": "CW-25"})

    def test_likely_products_with_their_match(self):
        result = tools.base.call(self.env(user=self.agent), "find_products",
                                 {"names": ["steel pipe 2in", "coper wire 2.5", "SP-3", "Nothing Like It"]}, None, "agent")
        self.assertFalse(result.is_error, result.content)
        found = {entry["asked"]: entry for entry in json.loads(result.content)["products"]}
        pipes = found["steel pipe 2in"]["options"]
        self.assertEqual(pipes[0]["id"], self.pipe2.id)
        self.assertIn(self.pipe3.id, [option["id"] for option in pipes], "The other pipe is offered too")
        self.assertGreater(pipes[0]["match"], pipes[1]["match"])
        self.assertEqual(found["coper wire 2.5"]["options"][0]["id"], self.wire.id)
        self.assertEqual(found["SP-3"]["options"][0]["id"], self.pipe3.id)
        self.assertEqual(found["SP-3"]["options"][0]["reference"], "SP-3")
        self.assertNotIn(self.wire.id, [option["id"] for option in found["Nothing Like It"]["options"][:1]]
                         if found["Nothing Like It"]["options"] else [])

    def test_a_product_that_is_not_stored_is_not_reported_out_of_stock(self):
        """Review (functional 23): a service shows 0 on hand in Odoo 19"""
        if "is_storable" not in self.env["product.product"]._fields:
            self.skipTest("Inventory is not installed")
        self.env["product.product"].create({"name": "Nudge Consulting Hour", "default_code": "NCH-1", "is_storable": False})
        result = tools.base.call(self.env(user=self.agent), "find_products", {"names": ["NCH-1"]}, None, "agent")
        option = json.loads(result.content)["products"][0]["options"][0]
        self.assertNotIn("on_hand", option)
        self.assertIn("not tracked", option["stock"])

    def test_agent_only(self):
        for mode, user in (("talk", self.agent), ("agent", self.user_a)):
            names = [tool.name for tool in tools.base.available(self.env(user=user), mode)]
            self.assertNotIn("find_products", names)

    def test_the_cards_of_products_are_followed_by_a_sentence(self):
        # Six cards for "do we sell this?" and no word of which was the cabinet of the picture
        self.assertFalse(tools.base.TOOLS["search_products"].terminal)


@tagged("post_install", "-at_install")
class TestUpload(NudgeCase, HttpCase):

    def test_a_file_is_posted_with_the_question(self):
        self.authenticate("nudge_a", "nudge_a")
        self.use_adapter(reply="I read it.")
        response = self.url_open("/nudge/chat/stream", data={
            "csrf_token": http.Request.csrf_token(self),
            "message": "What is this?",
        }, files={"file": ("quote.pdf", pdf_with("Quotation S00042"), "application/pdf")}, timeout=30)
        self.assertEqual(response.status_code, 200, response.text)
        question = self.env["nudge.message"].sudo().search(
            [("role", "=", "user"), ("attachment_name", "=", "quote.pdf")], order="id desc", limit=1)
        self.assertTrue(question)
        self.assertIn("S00042", question.context_text)

    def test_a_file_too_large_or_of_another_kind_is_refused_before_it_is_read(self):
        self.authenticate("nudge_a", "nudge_a")
        adapter = self.use_adapter(reply="I read it.")

        def refused(name, data, mimetype):
            response = self.url_open("/nudge/chat/stream", data={
                "csrf_token": http.Request.csrf_token(self),
                "message": "What is this?",
            }, files={"file": (name, data, mimetype)}, timeout=60)
            self.assertEqual(response.status_code, 200, response.text[:300])
            events = [json.loads(line) for line in response.text.splitlines() if line]
            self.assertEqual([event["state"] for event in events], ["rejected"])
            return events[0]["message"]

        self.assertEqual(
            refused("notes.txt", b"x" * (attachments.MAX_BYTES + 1), "text/plain"),
            "The file is too large. The limit is 10 MB.")
        self.assertEqual(
            refused("setup.exe", b"MZ" * 10, "application/octet-stream"),
            "Only PDF, text, CSV, Excel (.xlsx) and image files can be read.")
        self.assertFalse(adapter.calls, "Nothing was asked")
        self.assertFalse(self.env["nudge.message"].sudo().search(
            [("role", "=", "user"), ("attachment_name", "in", ("notes.txt", "setup.exe"))]))
