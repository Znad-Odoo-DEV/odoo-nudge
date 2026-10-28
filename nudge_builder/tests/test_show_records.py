import base64
from unittest.mock import patch

from odoo.tests import TransactionCase, new_test_user, tagged

from ..tools import compat, library, sections

PNG = base64.b64encode(
    base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))


@tagged("post_install", "-at_install")
class TestShowRecords(TransactionCase):
    """Records added after the build reach the built pages, in each language, without a new build."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.designer = new_test_user(
            cls.env, login="refresh_designer", name="Raghad Designer",
            groups="website.group_website_designer,nudge.group_nudge_agent")
        cls.arabic = cls.env["res.lang"]._activate_lang("ar_001") or cls.env["res.lang"].search([("code", "=", "ar_001")])
        cls.english = cls.env["res.lang"].search([("code", "=", "en_US")])
        cls.website = cls.env["website"].search([], limit=1)

    def setUp(self):
        super().setUp()
        self.patch(type(self.env["nudge.provider"]), "_get_default", lambda provider: provider.browse())
        self.patch(type(self.env["nudge.provider"]), "_get_for_mode", lambda provider, mode: provider.browse())
        self.site = self.env["nudge.builder.site"].with_user(self.designer).create({
            "website_id": self.website.id, "kind": "company",
            "lang_ids": [(6, 0, (self.english | self.arabic).ids)]})
        self.site.action_preview()
        self.site._apply_template(library.load("contracting"))
        self.home = self.site.page_ids.filtered(lambda page: page.key == "home")
        self.Project = self.env["nudge.builder.project"].with_user(self.designer)

    def arch(self, lang="en_US"):
        return self.home.view_id.with_context(lang=lang).arch_db

    def project_sections(self, lang="en_US"):
        _tree, wrap = compat.wrap_body(self.arch(lang))
        return [node for node in sections.children(wrap) if node.get("data-nb-kind") == "project"]

    def others(self, lang="en_US"):
        _tree, wrap = compat.wrap_body(self.arch(lang))
        return [compat.to_string(node) for node in sections.children(wrap) if node.get("data-nb-kind") != "project"]

    def test_projects_added_later_appear_in_each_language_then_follow_the_records(self):
        self.assertEqual(self.project_sections(), [])
        before = {lang: self.others(lang) for lang in ("en_US", "ar_001")}
        villa = self.Project.create({"site_id": self.site.id, "name": "Villa"})
        villa.with_context(lang="ar_001").name = "فيلا"
        tower = self.Project.create({"site_id": self.site.id, "name": "Tower"})
        published = self.home.page_id.is_published
        self.assertTrue(self.site._render_kind(self.home, "project"))
        for lang, names in (("en_US", ("Villa", "Tower")), ("ar_001", ("فيلا", "Tower"))):
            found = self.project_sections(lang)
            self.assertEqual(len(found), 1, lang)
            text = "".join(found[0].itertext())
            for name in names:
                self.assertIn(name, text, lang)
            self.assertEqual(self.others(lang), before[lang], lang)  # the rest of the page is the same
        self.assertEqual(self.home.page_id.is_published, published)
        self.assertFalse(self.site._render_kind(self.home, "project"))  # nothing changed: nothing written
        tower.published = False
        self.assertTrue(self.site._render_kind(self.home, "project"))
        text = "".join(self.project_sections()[0].itertext())
        self.assertIn("Villa", text)
        self.assertNotIn("Tower", text)
        (villa | tower).unlink()
        self.assertTrue(self.site._render_kind(self.home, "project"))
        for lang in ("en_US", "ar_001"):
            self.assertEqual(self.project_sections(lang), [], lang)
            self.assertEqual(self.others(lang), before[lang], lang)

    def test_the_button_updates_every_kind_and_says_which_pages(self):
        self.Project.create({"site_id": self.site.id, "name": "Villa"})
        result = self.site.with_user(self.designer).action_show_records()
        self.assertEqual(result["params"]["type"], "success")
        self.assertIn(self.home.name, result["params"]["message"])
        self.assertEqual(len(self.project_sections()), 1)
        again = self.site.with_user(self.designer).action_show_records()
        self.assertEqual(again["params"]["type"], "info")

    def test_an_import_refreshes_the_pages(self):
        wizard = self.env["nudge.builder.import"].with_user(self.designer).create({
            "site_id": self.site.id, "source": "file", "kind": "project", "filename": "p.csv",
            "file": base64.b64encode(b"Name,Location\nImported Tower,Dubai\n")})
        wizard.action_preview()
        self.assertEqual(self.project_sections(), [])
        wizard.action_import()
        self.assertIn("Imported Tower", "".join(self.project_sections()[0].itertext()))

    def test_pulling_a_picture_without_the_documents_app_changes_nothing(self):
        project = self.Project.create({"site_id": self.site.id, "name": "Villa"})
        picture = self.env["nudge.builder.picture"].with_user(self.designer).create(
            {"project_id": project.id, "image": PNG, "document_id": 1})
        with patch.object(type(picture), "document_record", lambda this: this.browse()):
            picture.action_pull_document()
        self.assertEqual(self.project_sections(), [])
        self.site._render_kind(self.home, "project")
        self.assertEqual(len(self.project_sections()), 1)
