import re
from types import SimpleNamespace
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.tools import agent_plan, base
from odoo.addons.nudge_builder.tools import compat, library, sections, site_edit

MEAN = "<script>alert(1)</script><img src=x onerror=alert(1)>Hello {{ 1 }} ${x} t-esc=\"x\" onclick=boom()"


@tagged("post_install", "-at_install")
class TestSiteEdit(TransactionCase):
    """SEO written at the build and changed from the chat, publish revisions, section tools, rights, injection"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.designer = new_test_user(
            cls.env, login="edit_designer", name="Raghad Designer", email="raghad@acme.example",
            groups="website.group_website_designer,nudge.group_nudge_agent")
        cls.plain = new_test_user(cls.env, login="edit_plain", name="Ebrahim Plain", groups="nudge.group_nudge_agent")
        cls.arabic = cls.env["res.lang"]._activate_lang("ar_001") or cls.env["res.lang"].search([("code", "=", "ar_001")])
        cls.english = cls.env["res.lang"].search([("code", "=", "en_US")])
        cls.website = cls.env["website"].search([], limit=1)
        cls.env.company.write({"name": "Raghad Contracting", "email": "info@raghad.example"})

    def setUp(self):
        super().setUp()
        self.patch(type(self.env["nudge.provider"]), "_get_default", lambda provider: provider.browse())
        self.patch(type(self.env["nudge.provider"]), "_get_for_mode", lambda provider, mode: provider.browse())
        self.env_d = self.env(user=self.designer)

    def make(self, website=None, template="generic"):
        website = website or self.website
        site = self.env["nudge.builder.site"].with_user(self.designer).create({
            "website_id": website.id, "kind": "company", "lang_ids": [(6, 0, (self.english | self.arabic).ids)]})
        site.action_preview()
        site._apply_template(library.load(template))
        return site

    def page(self, site, key):
        return site.page_ids.filtered(lambda page: page.key == key)

    def tool(self, name, arguments, env=None):
        env = env or self.env_d
        return base.TOOLS[name].run(env, arguments, None)

    def confirm(self, name, result, env=None):
        self.assertTrue(result.pending, result.content)
        return base.TOOLS[name].execute(env or self.env_d, SimpleNamespace(values=result.pending["values"]))

    def latest(self, site):
        return self.env["nudge.builder.revision"].search([("site_id", "=", site.id)], limit=1)

    def arch(self, page, lang="en_US"):
        return page.view_id.with_context(lang=lang).arch_db

    def kids(self, page, lang="en_US"):
        _tree, wrap = compat.wrap_body(self.arch(page, lang))
        return [compat.to_string(node) for node in sections.children(wrap)]

    # ------------------------------------------------------------------ SEO at the build
    def test_a_template_without_seo_gets_a_title_and_a_description(self):
        site = self.make()
        about = self.page(site, "about")
        self.assertEqual(about.seo_title, "%s | Raghad Contracting" % about.name)
        self.assertTrue(about.seo_description)
        self.assertLessEqual(len(about.seo_description), 165)
        self.assertIn("Raghad Contracting", about.view_id.with_context(lang="ar_001").website_meta_title)

    def test_the_build_writes_the_seo_of_every_template_page(self):
        site = self.make(template="contracting")
        template = {page["key"]: page for page in library.load("contracting")["pages"]}
        checked = 0
        for record in site.page_ids.filtered(lambda p: p.key in template and not p.key.startswith("legal")):
            seo = template[record.key]["seo"]
            checked += 1
            view = record.view_id
            self.assertEqual(view.with_context(lang="en_US").website_meta_title, seo["title"]["en"], record.key)
            self.assertEqual(view.with_context(lang="en_US").website_meta_description, seo["description"]["en"])
            self.assertEqual(view.with_context(lang="ar_001").website_meta_title, seo["title"]["ar"])
            self.assertEqual(record.seo_title, seo["title"]["en"])
        self.assertGreaterEqual(checked, 4)

    # ------------------------------------------------------------------ SEO from the chat
    def test_the_seo_title_is_changed_after_a_card_and_undone(self):
        site = self.make(template="contracting")
        about = self.page(site, "about")
        arabic = self.arch(about, "ar_001")
        before = about.view_id.with_context(lang="en_US").website_meta_title
        result = self.tool("change_page_seo", {"page": "About", "title": "About Raghad Contracting"})
        values = {line["label"]: line["value"] for line in result.pending["preview"]["lines"]}
        self.assertEqual(values["SEO title: before"], before)
        self.assertEqual(values["SEO title: after"], "About Raghad Contracting")
        self.assertEqual(about.view_id.with_context(lang="en_US").website_meta_title, before, "a card writes nothing")
        done = self.confirm("change_page_seo", result)
        self.assertFalse(done.is_error, done.content)
        self.assertEqual(about.view_id.with_context(lang="en_US").website_meta_title, "About Raghad Contracting")
        self.assertNotEqual(about.view_id.with_context(lang="ar_001").website_meta_title, "About Raghad Contracting")
        self.assertEqual(self.arch(about, "ar_001"), arabic, "the sections are not touched")
        revision = site.revision_ids.filtered(lambda r: r.kind == "seo")
        self.assertEqual(len(revision), 1)
        undone = self.confirm("undo_site_change", self.tool("undo_site_change", {}))
        self.assertFalse(undone.is_error, undone.content)
        self.assertEqual(about.view_id.with_context(lang="en_US").website_meta_title, before)
        self.assertEqual(revision.state, "undone")

    def test_the_description_card_shows_the_new_text_after(self):
        site = self.make()
        about = self.page(site, "about")
        text = "We build villas and towers across the Emirates since 1998."
        result = self.tool("change_page_seo", {"page": "about", "meta_description": text})
        values = {line["label"]: line["value"] for line in result.pending["preview"]["lines"]}
        self.assertEqual(values["SEO description: after"], text)
        self.assertEqual(result.pending["values"]["changes"]["website_meta_description"], text)
        self.assertFalse(self.confirm("change_page_seo", result).is_error)
        self.assertEqual(about.view_id.with_context(lang="en_US").website_meta_description, text)

    def test_a_description_of_the_call_is_never_taken_for_the_seo_description(self):
        # Trial (L5): a model put "Update SEO title and meta description for the Services page" in the
        # argument named description; the card showed it as the new description and it was written
        site = self.make()
        about = self.page(site, "about")
        before = about.view_id.with_context(lang="en_US").website_meta_description
        self.assertNotIn("description", base.TOOLS["change_page_seo"].input_schema["properties"])
        refused = base.call(self.env_d, "change_page_seo", {
            "page": "About", "title": "About Raghad Contracting",
            "description": "Update SEO title and meta description for the About page"}, None, "agent")
        self.assertTrue(refused.is_error)
        self.assertIn("meta_description", refused.content)
        self.assertFalse(refused.pending)

        text = "Villas, fit-out and renovation by Raghad Contracting."
        result = base.call(self.env_d, "change_page_seo", {
            "page": "About", "title": "About Raghad Contracting", "meta_description": text}, None, "agent")
        lines = {line["label"]: line["value"] for line in result.pending["preview"]["lines"]}
        self.assertEqual(lines["SEO description: before"], before)
        self.assertEqual(lines["SEO description: after"], text)
        done = self.confirm("change_page_seo", result)
        self.assertIn(text, done.content, "The model reads what was written")
        self.assertEqual(about.view_id.with_context(lang="en_US").website_meta_description, text)
        # Never a step of a plan: two SEO changes in one answer are two cards, each with its text
        calls = [{"id": str(n), "name": "change_page_seo", "input": {"page": "About", "meta_description": text}}
                 for n in (1, 2)]
        self.assertEqual(agent_plan.plan_calls(self.env_d, calls, "agent"), [])

    def test_the_id_of_the_site_given_as_website_id_finds_the_site(self):
        site = self.make()
        found = site_edit.find_site(self.env_d, {"website_id": site.id})
        self.assertEqual(found, site)
        self.assertEqual(site_edit.find_site(self.env_d, {"website_id": site.website_id.id}), site)
        result = self.tool("read_site_page", {"website_id": site.id})
        self.assertFalse(result.is_error, result.content)
        with self.assertRaises(UserError) as caught:
            site_edit.find_site(self.env_d, {"site_id": 999999})
        self.assertIn("site_id %s" % site.id, str(caught.exception))

    def test_the_seo_description_in_arabic_and_the_limits(self):
        site = self.make()
        about = self.page(site, "about")
        result = self.tool("change_page_seo", {"page": "/about", "meta_description": "وصف جديد للصفحة", "lang": "ar"})
        self.confirm("change_page_seo", result)
        self.assertEqual(about.view_id.with_context(lang="ar_001").website_meta_description, "وصف جديد للصفحة")
        self.assertNotEqual(about.view_id.with_context(lang="en_US").website_meta_description, "وصف جديد للصفحة")
        self.assertTrue(self.tool("change_page_seo", {"page": "about", "title": "x" * 80}).content.startswith("The SEO title is up to 70"))
        self.assertTrue(self.tool("change_page_seo", {"page": "about", "title": "T", "lang": "fr"}).is_error)
        self.assertTrue(self.tool("change_page_seo", {"page": "nowhere", "title": "T"}).is_error)

    def test_the_seo_is_refused_when_it_changed_after_the_card(self):
        site = self.make()
        about = self.page(site, "about")
        result = self.tool("change_page_seo", {"page": "about", "title": "Raghad About"})
        about.view_id.with_context(lang="en_US").website_meta_title = "Ebrahim was here"
        self.assertTrue(self.confirm("change_page_seo", result).is_error)
        self.assertEqual(about.view_id.with_context(lang="en_US").website_meta_title, "Ebrahim was here")

    def test_the_seo_undo_is_refused_after_a_later_edit(self):
        site = self.make()
        about = self.page(site, "about")
        self.confirm("change_page_seo", self.tool("change_page_seo", {"page": "about", "title": "Raghad About"}))
        about.view_id.with_context(lang="en_US").website_meta_title = "Ebrahim was here"
        with self.assertRaises(UserError):
            self.latest(site).action_undo()
        self.assertEqual(about.view_id.with_context(lang="en_US").website_meta_title, "Ebrahim was here")

    def test_the_seo_text_is_data(self):
        site = self.make()
        about = self.page(site, "about")
        self.confirm("change_page_seo", self.tool("change_page_seo", {"page": "about", "title": MEAN[:60]}))
        title = about.view_id.with_context(lang="en_US").website_meta_title
        self.assertNotIn("<", title)
        self.assertNotIn("{{", title)
        self.assertNotIn("${", title)

    # ------------------------------------------------------------------ publish and undo
    def test_publish_keeps_a_revision_and_undo_restores_everything(self):
        site = self.make()
        self.website.homepage_url = "/contactus"
        menus = {menu.id: (menu.sequence, menu.parent_id.id) for menu in site.page_ids.menu_id}
        site.action_publish()
        self.assertEqual(site.state, "published")
        self.assertEqual(self.website.homepage_url, "/home")
        revision = site.last_publish_id
        self.assertEqual(site.revision_ids, revision)
        self.assertEqual(revision.before["home_before"], "/contactus")
        site.action_undo_publish()
        self.assertFalse(any(site.page_ids.mapped("is_published")))
        self.assertEqual(self.website.homepage_url, "/contactus")
        self.assertEqual(site.state, "built")
        self.assertEqual({menu.id: (menu.sequence, menu.parent_id.id) for menu in site.page_ids.menu_id}, menus)
        self.assertEqual(revision.state, "undone")
        self.assertFalse(site.last_publish_id)
        with self.assertRaises(UserError):
            site.action_undo_publish()

    def test_a_second_publish_makes_a_new_revision(self):
        site = self.make()
        site.action_publish()
        first = site.last_publish_id
        self.confirm("edit_page_section", self.tool("edit_page_section", {
            "page": "about", "op": "add", "snippet": "s_title", "content": {"title": "Raghad Added"}}))
        site.action_publish()
        self.assertEqual(len(site.revision_ids.filtered(lambda r: r.kind == "publish")), 2)
        self.assertNotEqual(site.last_publish_id, first)
        site.action_undo_publish()  # the second: everything was already published, so it stays so
        self.assertTrue(all(site.page_ids.mapped("is_published")))
        self.assertEqual(site.last_publish_id, first)
        site.action_undo_publish()
        self.assertFalse(any(site.page_ids.mapped("is_published")))

    def test_publish_undo_leaves_what_changed_since(self):
        site = self.make()
        self.website.homepage_url = "/contactus"
        site.action_publish()
        self.website.homepage_url = "/about"
        about = self.page(site, "about")
        about.page_id.is_published = False
        site.action_undo_publish()
        self.assertEqual(self.website.homepage_url, "/about", "a home page changed since is not overwritten")
        self.assertFalse(about.page_id.is_published)
        self.assertFalse(any(site.page_ids.filtered(lambda p: p != about).mapped("is_published")))
        self.assertIn("changed since", site.log)

    def test_undo_publish_from_the_chat_and_the_rights(self):
        site = self.make()
        site.action_publish()
        with self.assertRaises(AccessError):
            site.with_user(self.plain).action_undo_publish()
        result = self.tool("undo_site_change", {})
        self.assertIn("Published", result.pending["summary"])
        self.assertTrue(site.page_ids.mapped("is_published")[0])
        done = self.confirm("undo_site_change", result)
        self.assertFalse(done.is_error, done.content)
        self.assertFalse(any(site.page_ids.mapped("is_published")))
        self.assertTrue(self.tool("undo_site_change", {}).is_error, "nothing is left to undo")

    # ------------------------------------------------------------------ sections
    def test_read_site_page_numbers_the_sections(self):
        site = self.make()
        text = self.tool("read_site_page", {}).content
        self.assertIn("/about", text)
        self.assertIn("SEO title", text)
        text = self.tool("read_site_page", {"page": "home"}).content
        self.assertIn("Section 1 (s_cover)", text)
        self.assertTrue(text.startswith("<data"))
        self.assertTrue(self.tool("read_site_page", {"page": "home"}, env=self.env(user=self.plain)).is_error)

    def test_add_a_section_shows_the_card_and_keeps_the_others(self):
        site = self.make()
        about = self.page(site, "about")
        before = self.kids(about)
        arabic = self.kids(about, "ar_001")
        result = self.tool("edit_page_section", {
            "page": "about", "op": "add", "snippet": "s_call_to_action", "position": 1,
            "content": {"en": {"title": "Visit Raghad", "button": "Call"}, "ar": {"title": "زر رغد", "button": "اتصل"}}})
        lines = {line["label"]: line["value"] for line in result.pending["preview"]["lines"]}
        self.assertIn("Visit Raghad", lines["After"])
        self.assertIn("nothing", lines["Before"])
        self.assertEqual(self.kids(about), before, "the card writes nothing")
        done = self.confirm("edit_page_section", result)
        self.assertFalse(done.is_error, done.content)
        after = self.kids(about)
        self.assertEqual(after[1:], before)
        self.assertIn("Visit Raghad", after[0])
        self.assertEqual(self.kids(about, "ar_001")[1:], arabic)
        self.assertIn("زر رغد", self.kids(about, "ar_001")[0])
        self.assertTrue(about.snippet_keys.startswith("s_call_to_action"))
        self.latest(site).action_undo()
        self.assertEqual(self.kids(about), before)
        self.assertEqual(self.kids(about, "ar_001"), arabic)
        self.assertEqual(about.snippet_keys, ",".join(sections.keys_of(compat.wrap_body(self.arch(about))[1])))

    def test_replace_move_remove_and_edit_each_undo(self):
        site = self.make()
        home = self.page(site, "home")
        original = self.kids(home)
        arabic = self.kids(home, "ar_001")
        count = len(original)

        def run(**arguments):
            result = self.tool("edit_page_section", {"page": "home", **arguments})
            done = self.confirm("edit_page_section", result)
            self.assertFalse(done.is_error, done.content)
            return result, self.latest(site)

        result, revision = run(op="move_down", position=1)
        self.assertEqual(self.kids(home), [original[1], original[0]] + original[2:])
        self.assertIn("1.", result.pending["preview"]["lines"][1]["value"])
        revision.action_undo()
        self.assertEqual(self.kids(home), original)
        result, revision = run(op="move_up", position=2)
        self.assertEqual(self.kids(home)[:2], [original[1], original[0]])
        revision.action_undo()
        self.assertEqual(self.kids(home), original)

        result, revision = run(op="remove", position=2)
        self.assertEqual(self.kids(home), original[:1] + original[2:])
        self.assertEqual(self.kids(home, "ar_001"), arabic[:1] + arabic[2:])
        revision.action_undo()
        self.assertEqual(self.kids(home), original)
        self.assertEqual(self.kids(home, "ar_001"), arabic)

        result, revision = run(op="replace", position=2, snippet="s_title", content={"title": "Raghad Replaced"})
        self.assertEqual(len(self.kids(home)), count)
        self.assertIn("Raghad Replaced", self.kids(home)[1])
        self.assertEqual(self.kids(home)[2:], original[2:])
        self.assertEqual(self.kids(home)[:1], original[:1])
        revision.action_undo()
        self.assertEqual(self.kids(home), original)

        result, revision = run(op="edit", position=1, content={"title": "Raghad Edited", "button": "Go"}, image="office")
        lines = {line["label"]: line["value"] for line in result.pending["preview"]["lines"]}
        self.assertIn("Raghad Edited", lines["After"])
        self.assertNotIn("Raghad Edited", lines["Before"])
        edited = self.kids(home)
        self.assertIn("Raghad Edited", edited[0])
        self.assertIn("/web/image/", edited[0])
        self.assertEqual(edited[1:], original[1:])
        self.assertEqual(self.kids(home, "ar_001")[1:], arabic[1:])
        revision.action_undo()
        self.assertEqual(self.kids(home), original)
        self.assertEqual(self.kids(home, "ar_001"), arabic)

    def test_an_edit_keeps_the_button_address(self):
        site = self.make()
        home = self.page(site, "home")
        before = re.findall(r'href="([^"]*)"[^>]*btn', self.kids(home)[0])
        self.confirm("edit_page_section", self.tool("edit_page_section", {
            "page": "home", "op": "edit", "position": 1, "content": {"title": "Raghad Only Title"}}))
        self.assertEqual(re.findall(r'href="([^"]*)"[^>]*btn', self.kids(home)[0]), before)

    def test_bad_positions_and_snippets_are_refused_before_a_card(self):
        site = self.make()
        for arguments in (
            {"op": "remove", "position": 99}, {"op": "move_up", "position": 1}, {"op": "edit", "position": 1},
            {"op": "add", "snippet": "s_not_a_snippet", "content": {"title": "T"}},
            {"op": "replace", "position": 1, "snippet": "s_title"},
            {"op": "edit", "position": 1, "image": "javascript:alert(1)"},
        ):
            result = self.tool("edit_page_section", {"page": "home", **arguments})
            self.assertTrue(result.is_error and not result.pending, arguments)
        self.assertFalse(site.revision_ids)

    def test_undo_is_refused_when_the_page_changed_after_the_edit(self):
        site = self.make()
        about = self.page(site, "about")
        self.confirm("edit_page_section", self.tool("edit_page_section", {
            "page": "about", "op": "add", "snippet": "s_title", "content": {"title": "Raghad First"}}))
        first = self.latest(site)
        self.confirm("edit_page_section", self.tool("edit_page_section", {
            "page": "about", "op": "add", "snippet": "s_title", "content": {"title": "Raghad Second"}}))
        with self.assertRaises(UserError):
            first.action_undo()
        self.assertIn("Raghad First", self.arch(about))
        self.latest(site).action_undo()
        first.action_undo()
        self.assertNotIn("Raghad First", self.arch(about))

    def test_a_card_older_than_the_page_is_refused(self):
        site = self.make()
        about = self.page(site, "about")
        card = self.tool("edit_page_section", {"page": "about", "op": "remove", "position": 1})
        self.confirm("edit_page_section", self.tool("edit_page_section", {
            "page": "about", "op": "add", "snippet": "s_title", "content": {"title": "Raghad Newer"}}))
        before = self.kids(about)
        self.assertTrue(self.confirm("edit_page_section", card).is_error)
        self.assertEqual(self.kids(about), before)

    # ------------------------------------------------------------------ rights
    def test_nothing_is_written_without_the_designer_right(self):
        site = self.make()
        about = self.page(site, "about")
        before, seo = self.kids(about), about.seo_title
        env = self.env(user=self.plain)
        arguments = {"page": "about", "op": "remove", "position": 1}
        card = self.tool("edit_page_section", arguments)
        self.assertTrue(self.tool("edit_page_section", arguments, env=env).is_error)
        self.assertTrue(self.tool("change_page_seo", {"page": "about", "title": "Ebrahim"}, env=env).is_error)
        self.assertTrue(self.confirm("edit_page_section", card, env=env).is_error, "the right is checked again at the yes")
        self.assertEqual(self.kids(about), before)
        with self.assertRaises(AccessError):
            site.with_user(self.plain)._section_apply(about.with_user(self.plain), "remove", 1)
        with self.assertRaises(AccessError):
            site.with_user(self.plain)._seo_apply(about.with_user(self.plain), {"website_meta_title": "Ebrahim"})
        self.assertEqual(about.seo_title, seo)

    def test_a_page_the_person_may_not_write_is_refused(self):
        site = self.make()
        about = self.page(site, "about")
        before = self.kids(about)

        def refuse(records):
            raise AccessError("no")
        with patch("odoo.addons.nudge_builder.tools.compat.check_write", refuse):
            self.assertTrue(self.tool("edit_page_section", {"page": "about", "op": "remove", "position": 1}).is_error)
            self.assertTrue(self.tool("change_page_seo", {"page": "about", "title": "Raghad"}).is_error)
        self.assertEqual(self.kids(about), before)

    # ------------------------------------------------------------------ what a person types is data
    def test_typed_content_cannot_inject_markup(self):
        site = self.make()
        about = self.page(site, "about")
        content = {"title": MEAN, "text": MEAN, "button": MEAN, "button_url": "javascript:alert(1)",
                   "items": [{"title": MEAN, "text": MEAN}]}
        for op, extra in (("add", {"snippet": "s_features"}), ("add", {"snippet": "s_call_to_action"}),
                          ("edit", {"position": 1})):
            result = self.tool("edit_page_section", {"page": "about", "op": op, "content": content, **extra})
            for line in result.pending["preview"]["lines"]:
                self.assertNotIn("<script", line["value"])
            self.confirm("edit_page_section", result)
        for lang in ("en_US", "ar_001"):
            arch = self.arch(about, lang)
            self.assertNotIn("<script", arch)
            self.assertNotIn("javascript:", arch)
            self.assertNotIn("{{", arch)
            self.assertNotIn("${", arch)
            self.assertNotRegex(arch, r"<[^>]*\son(click|error)\s*=")
            self.assertNotRegex(arch, r"<[^>]*\st-esc\s*=\s*\"x\"")
        self.assertTrue(about.view_id.with_context(lang="en_US").arch_db)
        self.assertTrue(self.tool("edit_page_section", {
            "page": "about", "op": "edit", "position": 1, "content": {"title": "T"}, "image": "https://x.example/a.png\" onerror=\"x"}).is_error)

    # ------------------------------------------------------------------ several websites
    def test_the_site_of_the_website_is_the_one_edited(self):
        other = self.env["website"].create({"name": "Ebrahim Second Website"})
        one = self.make()
        two = self.make(other)
        about_one, about_two = self.page(one, "about"), self.page(two, "about")
        self.assertNotEqual(about_one.page_id, about_two.page_id)
        before_two = self.kids(about_two)
        self.confirm("edit_page_section", self.tool("edit_page_section", {
            "page": "about", "op": "add", "snippet": "s_title", "content": {"title": "Raghad One"},
            "website_id": self.website.id}))
        self.assertIn("Raghad One", self.arch(about_one))
        self.assertEqual(self.kids(about_two), before_two)
        self.confirm("change_page_seo", self.tool("change_page_seo", {
            "page": "about", "title": "Second About", "website_id": other.id}))
        self.assertEqual(about_two.seo_title, "Second About")
        self.assertNotEqual(about_one.seo_title, "Second About")
        two.action_publish()
        self.assertFalse(any(one.page_ids.mapped("is_published")))
        self.assertEqual(other.homepage_url, "/home")
        self.assertNotEqual(self.website.homepage_url, "/home")
        one.action_publish()
        two.action_undo_publish()
        self.assertTrue(all(one.page_ids.mapped("is_published")))
        self.assertFalse(any(two.page_ids.mapped("is_published")))

    def test_the_tools_belong_to_the_website_skill(self):
        skill = self.env.ref("nudge_builder.skill_website_builder")
        names = set(skill.tool_names.split(","))
        self.assertLessEqual({"read_site_page", "edit_page_section", "change_page_seo", "undo_site_change"}, names)


@tagged("post_install", "-at_install")
class TestSiteContentEdit(TransactionCase):
    """Chat edits of Website Builder 2.0: add_site_item, add_location and set_contact, with their cards and undo"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.designer = new_test_user(
            cls.env, login="content_designer", name="Raghad Designer", email="raghad@acme.example",
            groups="website.group_website_designer,nudge.group_nudge_agent")
        cls.plain = new_test_user(cls.env, login="content_plain", name="Ebrahim Plain", groups="nudge.group_nudge_agent")
        cls.arabic = cls.env["res.lang"]._activate_lang("ar_001") or cls.env["res.lang"].search([("code", "=", "ar_001")])
        cls.english = cls.env["res.lang"].search([("code", "=", "en_US")])
        cls.website = cls.env["website"].search([], limit=1)

    def setUp(self):
        super().setUp()
        self.patch(type(self.env["nudge.provider"]), "_get_default", lambda provider: provider.browse())
        self.patch(type(self.env["nudge.provider"]), "_get_for_mode", lambda provider, mode: provider.browse())
        self.env_d = self.env(user=self.designer)

    make = TestSiteEdit.make
    tool = TestSiteEdit.tool
    confirm = TestSiteEdit.confirm
    latest = TestSiteEdit.latest

    def site(self):
        if "nudge.builder.faq" not in self.env or "whatsapp" not in self.env["nudge.builder.site"]._fields:
            self.skipTest("the content records are not installed")
        return self.make()

    def test_the_new_tools_are_registered_in_the_website_group(self):
        from odoo.addons.nudge.tools import groups
        names = set(groups.GROUPS["website"]["tools"])
        self.assertLessEqual({"add_site_item", "set_contact", "add_location"}, names)

    def test_a_faq_is_shown_then_added_then_taken_back(self):
        site = self.site()
        result = self.tool("add_site_item", {
            "kind": "faq", "site_id": site.id,
            "fields": {"question": "Do you work on weekends?", "answer": "<script>x</script>Yes, by appointment."}})
        self.assertTrue(result.pending, result.content)
        shown = " ".join(str(line["value"]) for line in result.pending["preview"]["lines"])
        self.assertIn("Do you work on weekends?", shown)
        self.assertNotIn("<script>", shown)
        self.assertFalse(self.env["nudge.builder.faq"].search([("site_id", "=", site.id)]), "a card writes nothing")
        self.confirm("add_site_item", result)
        faq = self.env["nudge.builder.faq"].search([("site_id", "=", site.id)])
        self.assertEqual(len(faq), 1)
        self.assertNotIn("script", faq.answer)
        self.assertEqual(self.latest(site).kind, "item")
        self.confirm("undo_site_change", self.tool("undo_site_change", {"site_id": site.id}))
        self.assertFalse(faq.exists())

    def test_an_edited_record_is_not_removed_by_the_undo(self):
        site = self.site()
        self.confirm("add_site_item", self.tool("add_site_item", {
            "kind": "faq", "site_id": site.id, "fields": {"question": "Q?", "answer": "A."}}))
        faq = self.env["nudge.builder.faq"].search([("site_id", "=", site.id)])
        faq.write({"question": "Changed by someone"})
        faq.flush_recordset()
        with self.assertRaises(UserError):
            self.latest(site).with_user(self.designer).action_undo()
        self.assertTrue(faq.exists())

    def test_unknown_kinds_and_fields_are_refused(self):
        site = self.site()
        self.assertTrue(self.tool("add_site_item", {"kind": "spaceship", "site_id": site.id, "fields": {"name": "x"}}).is_error)
        refused = self.tool("add_site_item", {"kind": "faq", "site_id": site.id, "fields": {"site_id": 99, "question": "x"}})
        self.assertTrue(refused.is_error)
        self.assertTrue(self.tool("add_site_item", {"kind": "faq", "site_id": site.id, "fields": {}}).is_error)

    def test_a_person_without_the_designer_right_adds_nothing(self):
        site = self.site()
        self.assertTrue(self.tool("add_site_item", {
            "kind": "faq", "site_id": site.id, "fields": {"question": "Q?", "answer": "A."}},
            env=self.env(user=self.plain)).is_error)

    def test_the_other_language_is_written_when_given(self):
        site = self.site()
        self.confirm("add_site_item", self.tool("add_site_item", {
            "kind": "faq", "site_id": site.id,
            "fields": {"question": "Where are you?", "answer": "Dubai."},
            "translations": {"ar": {"question": "أين أنتم؟"}}}))
        faq = self.env["nudge.builder.faq"].search([("site_id", "=", site.id)])
        self.assertEqual(faq.with_context(lang=self.arabic.code).question, "أين أنتم؟")
        self.assertEqual(faq.with_context(lang="en_US").question, "Where are you?")

    def test_add_location_checks_whatsapp_and_the_map_link(self):
        site = self.site()
        bad = self.tool("add_location", {"site_id": site.id, "name": "Branch", "whatsapp": "call me"})
        self.assertTrue(bad.is_error)
        bad_link = self.tool("add_location", {"site_id": site.id, "name": "Branch", "maps_url": "javascript:alert(1)"})
        self.assertTrue(bad_link.is_error)
        good = self.tool("add_location", {
            "site_id": site.id, "name": "Dubai branch", "city": "Dubai", "whatsapp": "00971 50 123 4567",
            "maps_url": "https://maps.google.com/?q=Dubai"})
        self.confirm("add_location", good)
        location = self.env["nudge.builder.location"].search([("site_id", "=", site.id), ("name", "=", "Dubai branch")])
        self.assertEqual(location.whatsapp, "+971501234567")

    def test_set_contact_card_apply_and_undo(self):
        site = self.site()
        site.write({"phone": "+971 4 000 0000", "whatsapp": False})
        result = self.tool("set_contact", {"site_id": site.id, "whatsapp": "+971 50 123 4567", "phone": "+971 4 111 1111"})
        self.assertTrue(result.pending, result.content)
        self.assertEqual(site.phone, "+971 4 000 0000", "a card writes nothing")
        self.confirm("set_contact", result)
        self.assertEqual(site.whatsapp, "+971501234567")
        self.assertEqual(site.phone, "+971 4 111 1111")
        self.assertEqual(self.latest(site).kind, "contact")
        self.confirm("undo_site_change", self.tool("undo_site_change", {"site_id": site.id}))
        self.assertEqual(site.phone, "+971 4 000 0000")
        self.assertFalse(site.whatsapp)

    def test_set_contact_refuses_what_is_not_valid(self):
        site = self.site()
        for arguments in ({"whatsapp": "12"}, {"email": "not an email"}, {"phone": "<b>x</b>"},
                          {"booking_url": "http://insecure.example"}, {}):
            self.assertTrue(self.tool("set_contact", dict(arguments, site_id=site.id)).is_error, arguments)

    def test_set_contact_is_refused_when_the_details_moved_since_the_card(self):
        site = self.site()
        result = self.tool("set_contact", {"site_id": site.id, "phone": "+971 4 222 2222"})
        site.phone = "+971 4 999 9999"
        done = base.TOOLS["set_contact"].execute(self.env_d, SimpleNamespace(values=result.pending["values"]))
        self.assertTrue(done.is_error)
        self.assertEqual(site.phone, "+971 4 999 9999")

    def test_social_links_are_https_only_and_merged(self):
        site = self.site()
        if "social_ids" not in site._fields:
            self.skipTest("no social links on the site")
        self.assertTrue(self.tool("set_contact", {
            "site_id": site.id, "social": [{"network": "instagram", "url": "http://x.example"}]}).is_error)
        self.assertTrue(self.tool("set_contact", {
            "site_id": site.id, "social": [{"network": "instagram", "url": "javascript:alert(1)"}]}).is_error)
