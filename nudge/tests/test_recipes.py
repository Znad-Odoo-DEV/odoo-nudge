import base64
import json

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.models.nudge_recipe import canonical, checksum_of
from odoo.addons.nudge.tools import noai_commands

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestRecipes(NudgeCase, TransactionCase):
    """Shared recipes: one file with a version and a checksum, read by another database."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.boss = new_test_user(
            cls.env, login="nudge_recipe_boss",
            groups="nudge.group_nudge_manager,nudge.group_nudge_agent,base.group_system")
        cls.raghad = new_test_user(cls.env, login="raghad_recipe", groups="nudge.group_nudge_user")
        env = cls.env(user=cls.boss)
        group = env.ref("base.group_user")
        cls.tour = env["nudge.tour"].create({
            "name": "Add a contact for Raghad", "phrases_en": "add raghad", "phrases_ar": "اضافة رغد",
            "action_xmlid": "base.action_partner_form", "group_ids": [(6, 0, group.ids)],
            "step_ids": [
                (0, 0, {"sequence": 10, "kind": "control", "name": "new", "note": "Press New."}),
                (0, 0, {"sequence": 20, "kind": "field", "name": "name", "res_model": "res.partner",
                        "view_type": "form", "note": "Write Raghad."})],
        })
        cls.rule = env["nudge.task"].create({
            "name": "Contacts of Raghad", "engine": "rule", "rule_model_id": env["ir.model"]._get_id("res.partner"),
            "rule_domain": "[('name', 'ilike', 'Raghad')]"})
        cls.preset = env["nudge.task"].create({
            "name": "Morning round", "instruction": "Tell Ebrahim what is late.", "engine": "ai"})
        cls.memory = env["nudge.memory"].create({
            "question": "How many leave days does Raghad get?", "answer": "Thirty days.", "state": "approved"})
        cls.proposed = env["nudge.memory"].create({
            "question": "Who is Ebrahim?", "answer": "A colleague.", "state": "proposed"})
        cls.word = env["nudge.lexicon.entry"].create({
            "synonym": "فرجيني", "intent": "show", "dialect": "levantine"})
        cls.source = env["nudge.source"].create({"name": "Leave policy", "purpose": "Leave rules"})
        cls.boss.company_id.nudge_health_user_id = cls.boss

    def recipes(self):
        return self.env["nudge.recipe"].with_user(self.boss)

    def export(self, kinds=("tour", "rule", "preset", "health", "lexicon", "memory", "file"), **kw):
        return self.recipes()._export(list(kinds), **kw)

    def wizard_import(self, package):
        wizard = self.env["nudge.recipe.import"].with_user(self.boss).create({
            "file": base64.b64encode(canonical(package).encode()), "file_name": "r.json"})
        wizard.action_read()
        return wizard

    def package_of(self, items):
        body = {"format": 1, "nudge_version": "x", "odoo_series": "x", "created": "", "company": "Other",
                "items": items}
        return {"recipe": body, "checksum": checksum_of(body)}

    def test_what_is_exported_and_what_is_not(self):
        package = self.export()
        items = {(item["kind"], item["name"]) for item in package["recipe"]["items"]}
        self.assertIn(("tour", "Add a contact for Raghad"), items)
        self.assertIn(("rule", "Contacts of Raghad"), items)
        self.assertIn(("preset", "Morning round"), items)
        self.assertIn(("memory", "How many leave days does Raghad get?"), items)
        self.assertNotIn(("memory", "Who is Ebrahim?"), items, "Only approved answers are shared")
        self.assertTrue(self.tour.recipe_key, "The key is made on the first export")
        self.assertEqual(package["recipe"]["format"], 1)
        again = self.export()
        self.assertEqual(package["recipe"]["items"], again["recipe"]["items"], "The same keys the second time")

    def test_round_trip_equals(self):
        before = self.export()
        wizard = self.wizard_import(before)
        self.assertTrue(all(line.action == "same" for line in wizard.line_ids), wizard.line_ids.mapped("action"))
        # The records are gone, as on a copy of the database without them: the recipe brings them back
        for record in (self.tour, self.rule, self.preset, self.memory, self.word, self.source):
            record.unlink()
        wizard = self.wizard_import(before)
        self.assertEqual(set(wizard.line_ids.mapped("action")), {"create", "same"})  # the health check stays
        wizard.action_import()
        self.assertEqual(set(wizard.line_ids.mapped("result")), {"done", "skipped"}, wizard.line_ids.mapped("message"))
        self.assertEqual(wizard.line_ids.filtered(lambda l: l.result == "skipped").mapped("kind"), ["health"])
        after = self.export()
        # A company file cannot come back without its file: it is archived, so not in the export. An answer
        # comes back proposed and an instruction switched off: neither is shared again until a manager says yes
        gone = {i["key"] for i in before["recipe"]["items"]} - {i["key"] for i in after["recipe"]["items"]}
        self.assertEqual(len(gone), 3)
        self.assertEqual(self.env["nudge.memory"].search([("recipe_key", "in", list(gone))]).state, "proposed")
        self.assertFalse(self.env["nudge.task"].with_context(active_test=False).search([
            ("recipe_key", "in", list(gone)), ("engine", "!=", "rule")]).active)
        self.assertEqual(
            [i for i in before["recipe"]["items"] if i["key"] not in gone],
            [i for i in after["recipe"]["items"] if i["key"] not in gone])
        source = self.env["nudge.source"].with_context(active_test=False).search([("recipe_key", "in", list(gone))])
        self.assertEqual(len(source), 1)
        self.assertFalse(source.active)

    def test_importing_twice_does_not_double(self):
        before = self.export()
        count = self.env["nudge.tour"].search_count([])
        self.wizard_import(before).action_import()
        self.assertEqual(self.env["nudge.tour"].search_count([]), count)

    def test_a_changed_file_is_refused(self):
        package = self.export()
        package["recipe"]["items"][0]["name"] = "Changed by hand"
        with self.assertRaises(UserError):
            self.wizard_import(package)
        package = self.export()
        package["checksum"] = "0" * 64
        with self.assertRaises(UserError):
            self.recipes()._read(json.dumps(package))
        for text in ("not json", "[]", "{}", json.dumps({"recipe": {}, "checksum": "x"})):
            with self.assertRaises(UserError):
                self.recipes()._read(text)

    def test_a_newer_format_is_refused(self):
        body = self.export()["recipe"]
        body["format"] = 99
        with self.assertRaises(UserError):
            self.recipes()._read(json.dumps({"recipe": body, "checksum": checksum_of(body)}))

    def test_a_tour_about_a_model_this_database_lacks_comes_in_with_the_step_skipped(self):
        data = {"name": "Count for Ebrahim", "sequence": 10, "phrases_en": "", "phrases_ar": "", "action_xmlid": "",
                "module_name": "", "groups": [{"xmlid": "base.group_user", "name": "Internal User"}],
                "steps": [
                    {"sequence": 10, "kind": "field", "name": "x_name", "label": "Name", "note": "",
                     "res_model": "x.model.nobody.has", "view_type": "form", "action_xmlid": ""},
                    {"sequence": 20, "kind": "control", "name": "new", "label": "", "note": "Press New.",
                     "res_model": "", "view_type": "", "action_xmlid": ""}]}
        wizard = self.wizard_import(self.package_of([{"kind": "tour", "key": "k-absent", "name": "Count", "data": data}]))
        self.assertIn("skipped", wizard.line_ids.note)
        wizard.action_import()
        tour = self.env["nudge.tour"].search([("recipe_key", "=", "k-absent")])
        self.assertEqual(tour.company_id, self.boss.company_id, "Company-bound")
        skipped = tour.step_ids.filtered("skip_reason")
        self.assertEqual(skipped.name, "x_name")
        steps, notes = tour._pointer_steps()
        self.assertEqual(len(steps), 1)
        self.assertEqual(len(notes), 1)

    def test_a_missing_group_archives_the_item_and_says_so(self):
        data = {"name": "For a group nobody has", "sequence": 10, "phrases_en": "", "phrases_ar": "",
                "action_xmlid": "", "module_name": "", "steps": [],
                "groups": [{"xmlid": "nowhere.group_x", "name": "Nowhere / Group X"}]}
        wizard = self.wizard_import(self.package_of([{"kind": "tour", "key": "k-group", "name": "G", "data": data}]))
        self.assertIn("Nowhere", wizard.line_ids.note)
        wizard.action_import()
        tour = self.env["nudge.tour"].with_context(active_test=False).search([("recipe_key", "=", "k-group")])
        self.assertFalse(tour.active)

    def test_unticked_items_are_not_made_and_a_rule_on_a_missing_model_is_skipped(self):
        rule = self.export(kinds=["rule"])["recipe"]["items"][0]
        rule["data"]["model_name"] = "x.model.nobody.has"
        gift = {"synonym": "هات", "intent": "show", "dialect": "msa"}
        wizard = self.wizard_import(self.package_of([
            rule, {"kind": "lexicon", "key": "k-lex", "name": "هات", "data": gift}]))
        by_kind = {line.kind: line for line in wizard.line_ids}
        self.assertEqual(by_kind["rule"].action, "skip")
        by_kind["lexicon"].selected = False
        wizard.action_import()
        self.assertFalse(self.env["nudge.lexicon.entry"].search([("recipe_key", "=", "k-lex")]))

    def test_undo_archives_what_the_import_made(self):
        data = {"synonym": "ورجيني", "intent": "show", "dialect": "levantine"}
        wizard = self.wizard_import(self.package_of([{"kind": "lexicon", "key": "k-undo", "name": "w", "data": data}]))
        wizard.action_import()
        entry = self.env["nudge.lexicon.entry"].search([("recipe_key", "=", "k-undo")])
        self.assertTrue(entry.active)
        wizard.action_rollback()
        self.assertFalse(entry.active)
        self.assertTrue(entry.exists(), "Archived, not deleted")

    def test_only_managers(self):
        with self.assertRaises(AccessError):
            self.env["nudge.recipe"].with_user(self.raghad)._export(["tour"])
        with self.assertRaises(AccessError):
            self.env["nudge.recipe"].with_user(self.raghad)._apply("lexicon", "k", {})

    def test_the_words_of_a_company_are_understood(self):
        lexicon = noai_commands.lexicon
        word = "كاشفلي"
        key = noai_commands.normalise.phrase_key(word)
        self.assertNotIn(key, lexicon.PHRASES)
        with lexicon.extended([(word, "show")]):
            self.assertIn("show", lexicon.tags_of(key))
        self.assertEqual(lexicon.tags_of(key), frozenset())
        self.env["nudge.lexicon.entry"].with_user(self.boss).create({"synonym": word, "intent": "show"})
        self.assertIn((word, "show"), self.env["nudge.lexicon.entry"].with_user(self.raghad)._pairs())

    def test_health_settings_travel(self):
        item = self.export(kinds=["health"])["recipe"]["items"][0]
        self.assertEqual(item["data"]["weekly_user"]["login"], "nudge_recipe_boss")
        item["data"]["weekly_user"]["login"] = "nobody_has_this_login"
        wizard = self.wizard_import(self.package_of([item]))
        self.assertIn("nobody_has_this_login", wizard.line_ids.note)
