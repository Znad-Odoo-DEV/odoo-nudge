import base64
import io
import json
from unittest import SkipTest

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..tools import importer

PNG = base64.b64encode(
    base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))


@tagged("post_install", "-at_install")
class TestImporterTools(TransactionCase):
    """The columns, the reader and the matching, which need no record."""

    def test_headers_are_english_and_arabic_and_every_kind_has_a_template(self):
        for kind in importer.MODELS:
            data, name, _mime = importer.build_template(kind, "csv")
            self.assertTrue(name.endswith(".csv"))
            head = data.decode("utf-8-sig").splitlines()[0]
            self.assertIn(" / ", head, kind)
            self.assertTrue(any("؀" <= ch <= "ۿ" for ch in head), kind)
            xlsx, xname, _m = importer.build_template(kind, "xlsx")
            self.assertTrue(xname.endswith((".xlsx", ".csv")))
            self.assertTrue(xlsx)

    def test_xlsx_template_reads_back_with_its_headers(self):
        data, name, _mime = importer.build_template("project", "xlsx")
        if not name.endswith(".xlsx"):
            raise SkipTest("xlsxwriter is not installed: the CSV template is served")
        rows = importer.read_file(data, name)
        mapping = importer.header_map("project", rows[0])
        self.assertEqual(set(mapping.values()), {c[0] for c in importer.columns("project")})

    def test_a_header_is_found_by_english_arabic_key_or_both(self):
        mapping = importer.header_map("project", ["Name", "الموقع", "year", "Client / العميل", "unknown"])
        self.assertEqual(mapping, {0: "name", 1: "location", 2: "year", 3: "client"})

    def test_csv_rows_become_typed_records(self):
        text = "Name / الاسم;Year / السنة;Status (planned/ongoing/done);Description\nVilla;2025;DONE;=cmd|x\nBad;abc;late;\n;;;\n"
        rows = importer.read_file(text.encode("utf-8"), "p.csv")
        records, warnings = importer.rows_to_records("project", rows)
        self.assertEqual(records[0], {"name": "Villa", "year": 2025, "status": "done", "description": "cmd|x"})
        self.assertEqual(records[1], {"name": "Bad"})
        self.assertEqual(len(records), 2)
        self.assertFalse(warnings)

    def test_a_file_too_big_or_empty_is_refused(self):
        with self.assertRaises(ValueError):
            importer.read_file(b"", "x.csv")
        with self.assertRaises(ValueError):
            importer.read_file(b"a" * (importer.MAX_FILE + 1), "x.csv")

    def test_unknown_headers_give_a_warning_not_a_record(self):
        records, warnings = importer.rows_to_records("faq", [["foo", "bar"], ["a", "b"]])
        self.assertFalse(records)
        self.assertTrue(warnings)

    def test_pictures_match_by_file_name_then_by_record_name(self):
        index = {importer.stem("Villa Jumeirah.JPG"): "a", importer.stem("tower-1.png"): "b"}
        self.assertEqual(importer.match_picture(index, "tower_1.PNG", "Other"), "b")
        self.assertEqual(importer.match_picture(index, "", "villa jumeirah"), "a")
        self.assertIsNone(importer.match_picture(index, "", "Nothing"))

    def test_html_in_a_cell_is_escaped(self):
        self.assertEqual(importer.plain_to_html("a <b>x</b>\nsecond"), "<p>a &lt;b&gt;x&lt;/b&gt;</p><p>second</p>")


@tagged("post_install", "-at_install")
class TestImportWizard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "nudge.builder.project" not in cls.env.registry or "nudge.builder.member" not in cls.env.registry:
            raise SkipTest("the content records of the builder are not installed")
        cls.website = cls.env["website"].search([], limit=1)
        cls.designer = new_test_user(
            cls.env, login="raghad_import",
            groups="base.group_user,website.group_website_designer" + (",product.group_product_manager" if cls.env.ref("product.group_product_manager", False) else ""))
        cls.plain = new_test_user(cls.env, login="ebrahim_import", groups="base.group_user")

    def setUp(self):
        super().setUp()
        self.env_d = self.env(user=self.designer)
        self.site = self.env_d["nudge.builder.site"].create({"name": "Raghad Import", "website_id": self.website.id})

    def wizard(self, **values):
        values.setdefault("site_id", self.site.id)
        return self.env_d["nudge.builder.import"].create(values)

    def csv_file(self, text):
        return base64.b64encode(text.encode("utf-8"))

    def test_sheet_rows_are_previewed_then_created_with_arabic_and_picture(self):
        attachment = self.env_d["ir.attachment"].create(
            {"name": "villa-jumeirah.png", "datas": PNG, "mimetype": "image/png"})
        wizard = self.wizard(
            source="file", kind="project", filename="p.csv", picture_attachment_ids=[(6, 0, attachment.ids)],
            file=self.csv_file("Name,Name (Arabic),Location,Year,Status (planned/ongoing/done)\n"
                               "Villa Jumeirah,فيلا جميرا,Dubai,2025,done\nTower,,Abu Dhabi,2024,ongoing\n"))
        wizard.action_preview()
        self.assertEqual(len(wizard.line_ids), 2)
        self.assertEqual(set(wizard.line_ids.mapped("action")), {"create"})
        self.assertEqual(wizard.line_ids[0].has_picture, True)
        self.assertFalse(wizard.line_ids[1].has_picture)
        wizard.line_ids[1].action = "skip"
        wizard.action_import()
        projects = self.env_d["nudge.builder.project"].search([("site_id", "=", self.site.id)])
        self.assertEqual(projects.mapped("name"), ["Villa Jumeirah"])
        self.assertEqual(projects.year, "2025")
        self.assertEqual(len(projects.picture_ids), 1)
        arabic = self.env["res.lang"].search([("code", "=like", "ar%")], limit=1)
        if arabic:
            self.assertEqual(projects.with_context(lang=arabic.code).name, "فيلا جميرا")

    def test_a_second_import_offers_an_update_of_the_same_name(self):
        text = "Name,Location\nTower,Dubai\n"
        first = self.wizard(source="file", kind="project", filename="p.csv", file=self.csv_file(text))
        first.action_preview()
        first.action_import()
        again = self.wizard(source="file", kind="project", filename="p.csv", file=self.csv_file("Name,Location\nTower,Sharjah\n"))
        again.action_preview()
        self.assertEqual(again.line_ids.action, "update")
        again.action_import()
        projects = self.env_d["nudge.builder.project"].search([("site_id", "=", self.site.id)])
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects.location, "Sharjah")

    def test_products_come_from_odoo_with_price_and_picture(self):
        if "nudge.builder.product" not in self.env.registry:
            raise SkipTest("no product record")
        if "product.template" not in self.env:
            raise SkipTest("Product is not installed")
        tmpl = self.env["product.template"].create(
            {"name": "Raghad Marble Tile", "list_price": 55.0, "sale_ok": True, "image_1920": PNG})
        wizard = self.wizard(source="odoo", kind="product")
        wizard.action_preview()
        line = wizard.line_ids.filtered(lambda l: l.name == "Raghad Marble Tile")
        self.assertTrue(line)
        self.assertTrue(line.has_picture)
        self.assertEqual(json.loads(line.values_json)["price"], 55.0)
        (wizard.line_ids - line).write({"action": "skip"})
        wizard.action_import()
        product = self.env_d["nudge.builder.product"].search(
            [("site_id", "=", self.site.id), ("name", "=", "Raghad Marble Tile")])
        self.assertEqual(product.price, 55.0)
        self.assertTrue(product.picture_ids)
        self.assertTrue(tmpl)

    def test_members_come_from_employees_and_locations_from_company_addresses(self):
        if "hr.employee" not in self.env.registry:
            raise SkipTest("hr is not installed")
        self.env["hr.employee"].create({"name": "Ebrahim Engineer", "job_title": "Site engineer"})
        wizard = self.wizard(source="odoo", kind="member")
        try:
            wizard.action_preview()
        except UserError:
            raise SkipTest("the designer may not read employees here")
        line = wizard.line_ids.filtered(lambda l: l.name == "Ebrahim Engineer")
        self.assertEqual(json.loads(line.values_json)["role"], "Site engineer")
        company = self.site.company_id
        company.partner_id.write({"street": "Sheikh Zayed Road", "city": "Dubai"})
        place = self.wizard(source="odoo", kind="location")
        place.action_preview()
        self.assertEqual(json.loads(place.line_ids[0].values_json)["street"], "Sheikh Zayed Road")

    def test_contact_fills_phone_and_email_of_the_site(self):
        self.site.company_id.sudo().write({"phone": "+971 4 000 0000", "email": "raghad@example.com"})
        wizard = self.wizard(source="odoo", kind="contact")
        wizard.action_preview()
        wizard.action_import()
        if "phone" in self.site._fields:
            self.assertEqual(self.site.phone, "+971 4 000 0000")

    def test_a_person_without_the_designer_right_cannot_open_or_import(self):
        with self.assertRaises(AccessError):
            self.env["nudge.builder.import"].with_user(self.plain)._open_for(self.site.with_user(self.plain))

    def test_nothing_is_read_that_the_person_may_not_read(self):
        # the designer has no right on employees unless hr gives it: the source is refused, not read with sudo
        if "hr.employee" not in self.env.registry:
            raise SkipTest("hr is not installed")
        limited = new_test_user(self.env, login="limited_import", groups="base.group_user,website.group_website_designer")
        env = self.env(user=limited)
        site = env["nudge.builder.site"].create({"name": "Limited", "website_id": self.website.id})
        wizard = env["nudge.builder.import"].create({"site_id": site.id, "source": "odoo", "kind": "member"})
        try:
            env["hr.employee"].search_count([])
        except AccessError:
            pass
        else:
            raise SkipTest("this user may read employees")
        with self.assertRaises((UserError, AccessError)):
            wizard.action_preview()

    def test_template_download_gives_a_file_with_the_headers(self):
        wizard = self.wizard(source="file", kind="service")
        action = wizard.action_download_template()
        self.assertIn("template_file", action["url"])
        self.assertTrue(wizard.template_file)
        self.assertTrue(wizard.template_name.startswith("nudge_import_service"))
        buffer = io.BytesIO(base64.b64decode(wizard.template_file))
        rows = importer.read_file(buffer.getvalue(), wizard.template_name)
        self.assertEqual(rows[0][0], "Name / الاسم")

    def test_a_broken_file_is_a_clear_message(self):
        wizard = self.wizard(source="file", kind="project", filename="p.xlsx", file=base64.b64encode(b"PK not a workbook"))
        with self.assertRaises(UserError):
            wizard.action_preview()
