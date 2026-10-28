import base64
import io
import json
import os

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..models.nudge_builder_content import (
    ALL_KINDS, normalise_whatsapp, safe_url, strip_metadata, valid_whatsapp)

TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "templates")


def png(color=(200, 30, 30), exif=False):
    from PIL import Image
    out = io.BytesIO()
    image = Image.new("RGB", (40, 30), color)
    if exif:
        data = Image.Exif()
        data[0x010F] = "Raghad Camera"
        image.save(out, format="JPEG", exif=data)
    else:
        image.save(out, format="PNG")
    return base64.b64encode(out.getvalue())


@tagged("post_install", "-at_install")
class TestContent(TransactionCase):
    """The content records of a site: access, WhatsApp, maps, kinds per template, translations"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env["website"].search([], limit=1)
        cls.arabic = cls.env["res.lang"]._activate_lang("ar_001") or cls.env["res.lang"].search([("code", "=", "ar_001")])
        cls.english = cls.env["res.lang"].search([("code", "=", "en_US")])
        cls.designer = new_test_user(
            cls.env, login="content_designer", name="Raghad Designer", groups="website.group_website_designer,nudge.group_nudge_user" + (",product.group_product_manager" if cls.env.ref("product.group_product_manager", False) else ""))
        cls.manager = new_test_user(
            cls.env, login="content_manager", name="Ebrahim Manager", groups="base.group_system")
        cls.plain = new_test_user(cls.env, login="content_plain", name="Plain User", groups="base.group_user")
        cls.other_company = cls.env["res.company"].create({"name": "Other Contracting"})
        cls.other = new_test_user(
            cls.env, login="content_other", name="Other Designer", groups="website.group_website_designer,nudge.group_nudge_user",
            company_id=cls.other_company.id, company_ids=[(6, 0, cls.other_company.ids)])

    def site(self, **values):
        values = {"website_id": self.website.id, "kind": "company", "lang_ids": [(6, 0, (self.english | self.arabic).ids)],
                  **values}
        return self.env["nudge.builder.site"].with_user(self.designer).create(values)

    # ---------------------------------------------------------------- the pure helpers
    def test_normalise_whatsapp(self):
        self.assertEqual(normalise_whatsapp("00971 50-123 4567"), "+971501234567")
        self.assertEqual(normalise_whatsapp("+971 (50) 123 4567"), "+971501234567")
        self.assertEqual(normalise_whatsapp("  "), "")
        self.assertEqual(normalise_whatsapp("call me"), "call me")
        self.assertTrue(valid_whatsapp("+971501234567"))
        self.assertFalse(valid_whatsapp("971501234567"))
        self.assertFalse(valid_whatsapp("+0123456789"))
        self.assertFalse(valid_whatsapp("+12345"))

    def test_safe_url(self):
        self.assertTrue(safe_url(""))
        self.assertTrue(safe_url("https://wa.me/971501234567"))
        self.assertFalse(safe_url("javascript:alert(1)"))
        self.assertFalse(safe_url("https://x.example/a b"))
        self.assertFalse(safe_url('https://x.example/"onload="x'))

    def test_strip_metadata_removes_exif(self):
        from PIL import Image
        raw = base64.b64decode(png(exif=True))
        self.assertTrue(Image.open(io.BytesIO(raw)).getexif())
        self.assertFalse(Image.open(io.BytesIO(strip_metadata(raw))).getexif())
        self.assertEqual(strip_metadata(b"not an image"), b"not an image")

    # ---------------------------------------------------------------- access
    def test_designer_and_manager_only(self):
        site = self.site()
        for kind in ALL_KINDS:
            values = {"site_id": site.id}
            if kind == "testimonial":
                values.update(author="Ebrahim Client", quote="Good work")
            else:
                values["name"] = f"Raghad {kind}"
            record = self.env[f"nudge.builder.{kind}"].with_user(self.designer).create(values)
            self.assertEqual(record.company_id, site.company_id)
            self.assertTrue(record.published)
            with self.assertRaises(AccessError):
                self.env[f"nudge.builder.{kind}"].with_user(self.plain).search([])
            self.assertTrue(self.env[f"nudge.builder.{kind}"].with_user(self.manager).search([("id", "=", record.id)]))
        with self.assertRaises(AccessError):
            self.env["nudge.builder.picture"].with_user(self.plain).search([])
        with self.assertRaises(AccessError):
            self.env["nudge.builder.social"].with_user(self.plain).search([])

    def test_other_company_sees_nothing(self):
        site = self.site()
        project = self.env["nudge.builder.project"].with_user(self.designer).create({"site_id": site.id, "name": "Villa"})
        picture = self.env["nudge.builder.picture"].with_user(self.designer).create(
            {"project_id": project.id, "image": png(), "name": "front"})
        social = self.env["nudge.builder.social"].with_user(self.designer).create(
            {"site_id": site.id, "network": "instagram", "url": "https://instagram.com/raghad"})
        for record in (project, picture, social):
            seen = self.env[record._name].with_user(self.other).search([("id", "=", record.id)])
            self.assertFalse(seen, record._name)
        self.assertFalse(self.env["nudge.builder.site"].with_user(self.other).search([("id", "=", site.id)]))

    def test_content_goes_with_its_site(self):
        site = self.site()
        project = self.env["nudge.builder.project"].create({"site_id": site.id, "name": "Villa"})
        picture = self.env["nudge.builder.picture"].create({"project_id": project.id, "image": png()})
        site.sudo().unlink()  # designers cannot delete a site; the records go with it
        self.assertFalse(project.exists())
        self.assertFalse(picture.exists())

    # ---------------------------------------------------------------- WhatsApp
    def test_whatsapp_is_validated_and_normalised(self):
        site = self.site(whatsapp="00971 50 123 4567")
        self.assertEqual(site.whatsapp, "+971501234567")
        site.whatsapp = "+971 (4) 555 6666"
        self.assertEqual(site.whatsapp, "+97145556666")
        for bad in ("12345", "+0501234567", "hello", "+97150123456789012345"):
            with self.assertRaises(ValidationError, msg=bad):
                site.whatsapp = bad
        site.whatsapp = False
        self.assertFalse(site.whatsapp)

    def test_whatsapp_of_a_location_is_validated(self):
        site = self.site()
        place = self.env["nudge.builder.location"].create({"site_id": site.id, "name": "HQ", "whatsapp": "0097150 123 4567"})
        self.assertEqual(place.whatsapp, "+971501234567")
        with self.assertRaises(ValidationError):
            place.whatsapp = "123"

    def test_whatsapp_link_per_language(self):
        site = self.site(whatsapp="+971501234567", whatsapp_text="Hello Raghad & co")
        site.with_context(lang="ar_001").whatsapp_text = "مرحبا"
        self.assertEqual(site.whatsapp_link("en_US"), "https://wa.me/971501234567?text=Hello%20Raghad%20%26%20co")
        self.assertEqual(site.whatsapp_link("ar_001"), "https://wa.me/971501234567?text=%D9%85%D8%B1%D8%AD%D8%A8%D8%A7")
        site.whatsapp_text = False
        self.assertEqual(site.whatsapp_link("en_US"), "https://wa.me/971501234567")
        site.whatsapp = False
        self.assertFalse(site.whatsapp_link("en_US"))

    def test_links_must_be_web_addresses(self):
        site = self.site()
        with self.assertRaises(ValidationError):
            site.booking_url = "javascript:alert(1)"
        with self.assertRaises(ValidationError):
            self.env["nudge.builder.social"].create({"site_id": site.id, "network": "x", "url": "javascript:alert(1)"})
        with self.assertRaises(ValidationError):
            site.email = "not an email"
        site.write({"booking_url": "https://book.example/raghad", "email": "info@raghad.example"})

    # ---------------------------------------------------------------- maps
    def location(self, **values):
        return self.env["nudge.builder.location"].create({"site_id": self.site().id, "name": "HQ", **values})

    def test_maps_embed_from_the_address_is_escaped(self):
        place = self.location(street='1 "Quote" <script>alert(1)</script> St & Co', city="Dubai")
        src = place.maps_embed_src
        self.assertTrue(src.startswith("https://www.google.com/maps?q="))
        self.assertTrue(src.endswith("&output=embed"))
        body = src[len("https://www.google.com/maps?q="):-len("&output=embed")]
        for char in '"<>& \'':
            self.assertNotIn(char, body)
        self.assertIn("%3Cscript%3E", body)

    def test_maps_embed_from_coordinates(self):
        place = self.location(latitude=25.2048, longitude=55.2708, street="ignored")
        self.assertEqual(place.maps_embed_src, "https://www.google.com/maps?q=25.2048000,55.2708000&output=embed")
        south = self.location(latitude=-33.8688, longitude=151.2093)
        self.assertIn("-33.8688000,151.2093000", south.maps_embed_src)

    def test_maps_embed_from_a_google_link(self):
        embed = "https://www.google.com/maps/embed?pb=!1m18!1m12"
        self.assertEqual(self.location(maps_url=embed).maps_embed_src, embed)
        share = self.location(maps_url="https://www.google.com/maps/place/Burj/@25.1972,55.2744,17z")
        self.assertEqual(share.maps_embed_src, "https://www.google.com/maps?q=25.1972,55.2744&output=embed")
        named = self.location(maps_url="https://www.google.com/maps/place/Burj+Khalifa/data=1")
        self.assertIn("Burj%20Khalifa", named.maps_embed_src)

    def test_maps_embed_empty_and_bad_links(self):
        self.assertTrue(self.location(name="Only a name").maps_embed_src)
        with self.assertRaises(ValidationError):
            self.location(maps_url="https://evil.example/maps")
        with self.assertRaises(ValidationError):
            self.location(maps_url="javascript:alert(1)")
        with self.assertRaises(ValidationError):
            self.location(latitude=120.0)

    def test_one_main_location(self):
        site = self.site()
        Location = self.env["nudge.builder.location"]
        Location.create({"site_id": site.id, "name": "HQ", "is_main": True})
        Location.create({"site_id": site.id, "name": "Branch"})
        with self.assertRaises(ValidationError):
            Location.create({"site_id": site.id, "name": "Second HQ", "is_main": True})

    # ---------------------------------------------------------------- kinds
    def test_every_template_declares_its_kinds(self):
        files = [name for name in os.listdir(TEMPLATES) if name.endswith(".json")]
        self.assertEqual(len(files), 13)
        for name in files:
            with open(os.path.join(TEMPLATES, name), encoding="utf8") as handle:
                template = json.load(handle)
            kinds = template.get("kinds")
            self.assertTrue(kinds, name)
            self.assertTrue(set(kinds) <= set(ALL_KINDS), name)
            self.assertEqual(len(set(kinds)), len(kinds), name)
            self.assertIn("location", kinds, name)

    def test_kinds_used_follows_the_template(self):
        expected = {
            "contracting": {"project", "service", "member", "testimonial", "faq", "location"},
            "clinic": {"service", "member", "testimonial", "faq", "location"},
            "restaurant": {"product", "testimonial", "faq", "location"},
            "fashion_shop": {"product", "testimonial", "faq", "location"},
            "school": {"service", "member", "testimonial", "faq", "location"},
        }
        for key, kinds in expected.items():
            site = self.site(template_key=key)
            self.assertEqual(set(site.kinds_used), kinds, key)
            self.assertEqual(site.kinds_used, [kind for kind in ALL_KINDS if kind in kinds], key)

    def test_kinds_used_changes_with_the_template_and_without_kinds_is_everything(self):
        site = self.site(template_key="restaurant")
        self.assertNotIn("project", site.kinds_used)
        site.template_key = "contracting"
        self.assertIn("project", site.kinds_used)
        site.template_json = {"schema": 1, "key": "mine", "name": {"en": "Mine"},
                              "palette": {"primary": "#112233", "secondary": "#112233", "accent": "#112233",
                                          "light": "#ffffff", "dark": "#000000"},
                              "fonts": {"heading": "Montserrat", "body": "Montserrat"},
                              "pages": [{"key": "home", "kind": "home", "name": {"en": "Home"},
                                         "blocks": [{"snippet": "s_banner", "copy": {"en": {"title": "Hi"}}}]}]}
        self.assertEqual(set(site.kinds_used), set(ALL_KINDS))

    def test_listing_mode_is_real_estate_only(self):
        self.assertTrue(self.site(template_key="real_estate").listing_mode)
        self.assertFalse(self.site(template_key="contracting").listing_mode)

    def test_counts_and_published_records(self):
        site = self.site(template_key="contracting")
        Project = self.env["nudge.builder.project"]
        Project.create({"site_id": site.id, "name": "Villa", "sequence": 20})
        Project.create({"site_id": site.id, "name": "Tower", "featured": True, "sequence": 30})
        Project.create({"site_id": site.id, "name": "Hidden", "published": False})
        self.assertEqual(site.project_count, 3)
        self.assertEqual(site.content_records("project").mapped("name"), ["Tower", "Villa"])
        self.assertEqual(len(site.content_records("project", published_only=False)), 3)
        self.assertFalse(site.content_records("nonsense"))

    # ---------------------------------------------------------------- translations
    def test_name_and_description_are_translated(self):
        site = self.site()
        project = self.env["nudge.builder.project"].create({
            "site_id": site.id, "name": "Marina Tower", "summary": "A tower", "description": "<p>Thirty floors.</p>",
            "location": "Dubai Marina"})
        project.flush_recordset()  # a translated html field reads the stored terms: flush first
        project.with_context(lang="ar_001").write({
            "name": "برج المرينا", "summary": "برج", "description": "<p>ثلاثون طابقا.</p>", "location": "مرينا دبي"})
        self.assertEqual(project.with_context(lang="en_US").name, "Marina Tower")
        self.assertEqual(project.with_context(lang="ar_001").name, "برج المرينا")
        self.assertEqual(project.with_context(lang="ar_001").summary, "برج")
        self.assertIn("ثلاثون", project.with_context(lang="ar_001").description)
        self.assertIn("Thirty", project.with_context(lang="en_US").description)
        self.assertEqual(project.with_context(lang="ar_001").location, "مرينا دبي")

    def test_faq_question_and_answer_are_aliases_and_translated(self):
        site = self.site()
        faq = self.env["nudge.builder.faq"].create({"site_id": site.id, "question": "How long?", "answer": "<p>Two weeks.</p>"})
        self.assertEqual(faq.name, "How long?")
        faq.flush_recordset()
        faq.with_context(lang="ar_001").write({"question": "كم المدة؟", "answer": "<p>أسبوعان.</p>"})
        self.assertEqual(faq.with_context(lang="ar_001").name, "كم المدة؟")
        self.assertEqual(faq.with_context(lang="en_US").question, "How long?")
        self.assertIn("أسبوعان", faq.with_context(lang="ar_001").answer)

    def test_picture_alt_is_translated(self):
        site = self.site()
        project = self.env["nudge.builder.project"].create({"site_id": site.id, "name": "Villa"})
        picture = self.env["nudge.builder.picture"].create({"project_id": project.id, "image": png(), "alt": "Front of the villa"})
        picture.with_context(lang="ar_001").alt = "واجهة الفيلا"
        self.assertEqual(picture.with_context(lang="en_US").alt, "Front of the villa")
        self.assertEqual(picture.with_context(lang="ar_001").alt, "واجهة الفيلا")
        self.assertEqual(picture.site_id, site)
        self.assertTrue(project.cover_image)

    # ---------------------------------------------------------------- pictures and other rules
    def test_a_picture_has_exactly_one_owner(self):
        site = self.site()
        project = self.env["nudge.builder.project"].create({"site_id": site.id, "name": "Villa"})
        service = self.env["nudge.builder.service"].create({"site_id": site.id, "name": "Painting"})
        Picture = self.env["nudge.builder.picture"]
        with self.assertRaises(ValidationError):
            Picture.create({"image": png()})
        with self.assertRaises(ValidationError):
            Picture.create({"image": png(), "project_id": project.id, "service_id": service.id})

    def test_uploaded_pictures_lose_their_metadata(self):
        from PIL import Image
        site = self.site()
        project = self.env["nudge.builder.project"].create({"site_id": site.id, "name": "Villa"})
        picture = self.env["nudge.builder.picture"].create({"project_id": project.id, "image": png(exif=True)})
        stored = Image.open(io.BytesIO(base64.b64decode(picture.image)))
        self.assertFalse(stored.getexif())

    def test_project_rules(self):
        site = self.site(template_key="real_estate")
        Project = self.env["nudge.builder.project"]
        with self.assertRaises(ValidationError):
            Project.create({"site_id": site.id, "name": "Villa", "year": "25"})
        with self.assertRaises(ValidationError):
            Project.create({"site_id": site.id, "name": "Villa", "price": -1.0})
        listing = Project.create({"site_id": site.id, "name": "Flat", "year": "2025", "price": 900000.0, "area": 120.5,
                                  "bedrooms": 2, "listing_status": "sale"})
        self.assertEqual(listing.currency_id, site.company_id.currency_id)

    def test_testimonial_and_service_rules(self):
        site = self.site()
        with self.assertRaises(ValidationError):
            self.env["nudge.builder.testimonial"].create({"site_id": site.id, "author": "Raghad", "quote": "Great", "rating": 6})
        with self.assertRaises(ValidationError):
            self.env["nudge.builder.service"].create({"site_id": site.id, "name": "Paint", "icon": "x\" onclick=\"y"})
        ok = self.env["nudge.builder.service"].create({"site_id": site.id, "name": "Paint", "icon": "fa-paint-brush", "quote": True})
        self.assertTrue(ok.quote)

    def test_fill_from_company(self):
        company = self.env.company
        company.write({"phone": "+971 4 111 2222", "email": "info@raghad.example", "street": "1 Sheikh Zayed Rd",
                       "city": "Dubai", "country_id": self.env.ref("base.ae").id})
        site = self.site(phone="+971 4 999 0000")
        site.action_fill_from_company()
        self.assertEqual(site.phone, "+971 4 999 0000")  # what was typed stays
        self.assertEqual(site.email, "info@raghad.example")
        main = site.location_ids
        self.assertEqual(len(main), 1)
        self.assertTrue(main.is_main)
        self.assertEqual(main.city, "Dubai")
        site.action_fill_from_company()
        self.assertEqual(len(site.location_ids), 1)  # not twice
        with self.assertRaises(AccessError):
            site.with_user(self.plain).action_fill_from_company()

    def test_the_document_link_is_optional(self):
        site = self.site()
        project = self.env["nudge.builder.project"].create({"site_id": site.id, "name": "Villa"})
        picture = self.env["nudge.builder.picture"].create({"project_id": project.id, "image": png(), "document_id": 987654})
        self.assertFalse(picture.document_record())  # no Documents app, or no such document
        picture.action_pull_document()  # nothing to pull, no error
        self.assertTrue(picture.image)

    def test_shop_products_need_website_sale(self):
        site = self.site(template_key="fashion_shop")
        if "product.template" not in self.env:
            with self.assertRaises(Exception):
                site.create_shop_product({"name": "Raghad Shirt"})
            self.assertFalse(site.shop_products())
            return
        Product = site.env["product.template"]
        if not (Product.has_access("create") if hasattr(Product, "has_access")
                else Product.check_access_rights("create", raise_exception=False)):
            # Odoo 17 has no product manager group: only Settings and the managers of other apps create products
            self.skipTest("The designer may not create products on this version")
        product = site.create_shop_product({"name": "Raghad Shirt", "list_price": 35.0, "bogus": "dropped"})
        self.assertFalse(product.is_published if "is_published" in product._fields else False)
        self.assertEqual(site.shop_products(), product)
        site.add_shop_products(product)
        self.assertEqual(site.shop_product_ids, [product.id])
