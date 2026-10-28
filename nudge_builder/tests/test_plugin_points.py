from odoo import _
import json
import os
import tempfile
from unittest.mock import patch

from odoo.tests import TransactionCase, new_test_user, tagged

from ..models.nudge_builder_content import ALL_KINDS
from ..tools import checks, content_sections as cs, library

KIND = "test_plugin_kind"


def kind_spec(**values):
    return {"model": "nudge.builder.service", "layouts": ("list", "grid", "showcase"), "snippet": "s_features",
            "item": lambda site, record, item, lang: dict(item, meta=["Open daily"], actions=[
                {"url": "/test-plugin?x=1", "text": "Try it"}]),
            "labels": lambda env: {"try": _("Try it")}, **values}


@tagged("post_install", "-at_install")
class TestPluginPoints(TransactionCase):
    """The registry other modules extend: nothing changes without a plugin, and a plugin's entry is checked"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Plugin = cls.env["nudge.builder.plugin"]
        cls.plugin_class = cls.env.registry["nudge.builder.plugin"]
        cls.designer = new_test_user(
            cls.env, login="plugin_designer", name="Raghad Designer", email="raghad@acme.example",
            groups="website.group_website_designer,nudge.group_nudge_user")
        cls.english = cls.env["res.lang"].search([("code", "=", "en_US")])
        cls.website = cls.env["website"].search([], limit=1)

    def site(self, **values):
        return self.env["nudge.builder.site"].with_user(self.designer).create({
            "website_id": self.website.id, "kind": "company", "direction": "a",
            "lang_ids": [(6, 0, self.english.ids)], **values})

    def with_kinds(self, kinds):
        return patch.object(self.plugin_class, "_plugin_kinds", lambda this: kinds)

    # ------------------------------------------------------------------ nothing changes without a plugin
    def test_without_a_plugin_the_registry_is_empty_and_the_kinds_are_the_cores(self):
        with self.with_kinds({}), patch.object(self.plugin_class, "_plugin_checks", lambda this: []), \
                patch.object(self.plugin_class, "_plugin_templates", lambda this: []):
            self.assertEqual(self.Plugin.plugin_kinds(), {})
            self.assertEqual(self.Plugin.plugin_checks(), [])
            self.assertEqual(self.Plugin.plugin_template_paths(), [])
            site = self.site(template_key="real_estate")
            self.assertEqual(site._all_kinds(), list(ALL_KINDS))
            self.assertEqual(site._template_kinds(),
                             ["project", "service", "member", "testimonial", "faq", "location"])
            self.assertTrue(site.listing_mode)
            self.assertFalse(self.site(template_key="generic").listing_mode)
            self.assertEqual(library.keys(), library.keys(()))
            self.assertEqual(list(checks.group_labels(self.env)), list(checks.GROUPS))

    def test_render_goes_through_render_kind_and_stays_the_same(self):
        items = [{"name": "Villa", "summary": "A villa."}]
        for kind, layouts in cs.LAYOUTS.items():
            self.assertEqual(cs.render(kind, items, None, "b").get("data-nb-layout"), layouts[1], kind)
        self.assertIsNone(cs.render("nonsense", items))
        self.assertEqual(cs.pick_layout("project", "carousel", "a"), "carousel")
        self.assertEqual(cs.pick_layout("unknown", None, "a"), "grid")

    # ------------------------------------------------------------------ a kind
    def test_a_bad_kind_is_left_out(self):
        bad = {"project": kind_spec(), "Bad Key": kind_spec(), "no_model": kind_spec(model="nope.nothing"),
               "bad_layouts": kind_spec(layouts=("grid", "nonsense", "list")), "contact": kind_spec(),
               "two": kind_spec(layouts=("grid",)), KIND: kind_spec()}
        with self.with_kinds(bad):
            self.assertEqual(list(self.Plugin.plugin_kinds()), [KIND])

    def test_a_plugin_kind_is_shown_from_the_records_and_not_placed_without_them(self):
        spec = {"key": "plug", "kind": "services", "name": {"en": "Plug", "ar": "plug"}, "blocks": [
            {"snippet": "s_text_block", "kind": KIND, "copy": {"en": {"title": "Our plug"}, "ar": {}}}]}
        with self.with_kinds({KIND: kind_spec()}):
            site = self.site()
            self.assertEqual(site._all_kinds(), list(ALL_KINDS) + [KIND])
            self.assertFalse(site._build_page(spec))
            self.assertIn("No %s records" % KIND, site.log)
            Service = self.env["nudge.builder.service"].with_user(self.designer)
            Service.create({"site_id": site.id, "name": "Oil change", "summary": "Fresh oil."})
            Service.create({"site_id": site.id, "name": "Hidden", "published": False})
            page = site._build_page(spec)
            self.assertTrue(page)
            arch = page.view_id.with_context(lang="en_US").arch_db
            self.assertIn("Oil change", arch)
            self.assertNotIn("Hidden", arch)
            self.assertIn("Open daily", arch)
            self.assertIn('href="/test-plugin?x=1"', arch)
            self.assertIn('data-snippet="s_features"', arch)
            self.assertIn('data-nb-layout="list"', arch)  # Classic: the first layout the plugin gave
            self.assertEqual([r.name for r in site.content_records(KIND)], ["Oil change"])

    def test_a_plugin_renderer_is_used(self):
        def render(items, layout, direction, title, text, cx, uid):
            return cs.render_kind(KIND, ("grid", "grid", "grid"), items, layout, direction, title + "!", text, cx, uid)
        spec = {"key": "plug", "kind": "services", "name": {"en": "Plug", "ar": "plug"}, "blocks": [
            {"snippet": "s_text_block", "kind": KIND, "layout": "carousel", "copy": {"en": {"title": "Ours"}, "ar": {}}}]}
        with self.with_kinds({KIND: kind_spec(render=render, item=None)}):
            site = self.site()
            self.env["nudge.builder.service"].with_user(self.designer).create({"site_id": site.id, "name": "Brakes"})
            arch = site._build_page(spec).view_id.with_context(lang="en_US").arch_db
            self.assertIn("Ours!", arch)
            self.assertIn("Brakes", arch)

    # ------------------------------------------------------------------ a check
    def test_a_plugin_check_runs_with_its_label_and_text_and_cannot_replace_a_core_group(self):
        def run(env, website, pages, **options):
            return [checks.finding("plug_found", "plugcheck", checks.INFO, 3)]
        entry = {"group": "plugcheck", "label": "Plug check", "function": run,
                 "texts": lambda env, count, extra: {"plug_found": "%s plugs" % count}}
        broken = {"group": "plugbroken", "function": lambda *args, **kwargs: 1 / 0}
        clash = {"group": "seo", "function": run}
        with patch.object(self.plugin_class, "_plugin_checks", lambda this: [entry, broken, clash, {"group": "x"}]):
            self.assertEqual([c["group"] for c in self.Plugin.plugin_checks()], ["plugcheck", "plugbroken"])
            env = self.env(user=self.designer)
            found, skipped = checks.check_site(env, self.website.with_env(env))
            self.assertIn("plug_found", [item["key"] for item in found])
            self.assertEqual([item["group"] for item in skipped], ["plugbroken"])  # one failure stops nothing
            self.assertEqual(checks.group_labels(env)["plugcheck"], "Plug check")
            self.assertEqual(checks.finding_text(env, "plug_found", 3), "3 plugs")
            only, _skipped = checks.check_site(env, self.website.with_env(env), groups=["plugcheck"])
            self.assertEqual([item["key"] for item in only], ["plug_found"])

    # ------------------------------------------------------------------ a template
    def test_a_plugin_template_is_offered_loaded_and_never_replaces_a_core_one(self):
        template = dict(library.load("generic"), key="plugtemplate", suggest=["flux capacitor repair"],
                        listing_mode=True, kinds=["service", KIND])
        template["name"] = {"en": "Plug template", "ar": "plug"}
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "plugtemplate.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(template, handle)
            clash = os.path.join(folder, "generic.json")
            with open(clash, "w", encoding="utf-8") as handle:
                json.dump(dict(template, key="generic"), handle)
            with patch.object(self.plugin_class, "_plugin_templates", lambda this: [path, clash, "/nowhere/x.json"]), \
                    self.with_kinds({KIND: kind_spec()}):
                extra = self.Plugin.plugin_template_paths()
                self.assertEqual(extra, [path, clash])
                self.assertIn("plugtemplate", library.keys(extra))
                self.assertNotIn("plugtemplate", library.keys())
                self.assertEqual(library.pick("flux capacitor repair", extra), "plugtemplate")
                self.assertEqual(library.load("generic", extra)["name"], library.load("generic")["name"])
                self.assertIn(("plugtemplate", "Plug template"), self.env["nudge.builder.site"]._template_selection())
                site = self.site(template_key="plugtemplate")
                self.assertEqual(site._template_kinds(), ["service", KIND])
                self.assertTrue(site.listing_mode)
        self.assertNotIn("plugtemplate", library.keys(self.Plugin.plugin_template_paths()))
