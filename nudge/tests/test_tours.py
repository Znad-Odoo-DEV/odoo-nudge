from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestTours(NudgeCase, TransactionCase):
    """Recorded tours: the pointer, replayed without any AI."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Menu = cls.env["ir.ui.menu"]
        cls.app = Menu.create({"name": "Nudge Tour App"})
        cls.leaf = Menu.create({
            "name": "Nudge Tour Partners",
            "parent_id": cls.app.id,
            "action": f"ir.actions.act_window,{cls.env.ref('base.action_partner_form').id}",
        })
        # Recorded by a manager, about contacts: a screen every database has
        cls.tour = cls.env["nudge.tour"].with_user(cls.manager).create({
            "name": "Add a contact for Raghad",
            "phrases_en": "add raghad\nnew contact for raghad",
            "phrases_ar": "اضافة رغد\nجهة اتصال لرغد",
            "action_xmlid": "base.action_partner_form",
            "step_ids": [
                (0, 0, {"sequence": 10, "kind": "control", "name": "new", "note": "Press New."}),
                (0, 0, {"sequence": 20, "kind": "field", "name": "name", "res_model": "res.partner",
                        "view_type": "form", "note": "Write Raghad."}),
                (0, 0, {"sequence": 30, "kind": "field", "name": "x_field_nobody_has", "label": "Ghost",
                        "res_model": "res.partner", "view_type": "form"}),
                (0, 0, {"sequence": 40, "kind": "button", "name": "action_nobody_has", "label": "Ghost Button",
                        "res_model": "res.partner", "view_type": "form"}),
                (0, 0, {"sequence": 50, "kind": "field", "name": "name", "res_model": "x.model.nobody.has",
                        "view_type": "form"}),
                (0, 0, {"sequence": 60, "kind": "menu", "name": "base.menu_administration"}),
            ],
        })

    def tours(self, user):
        return self.env["nudge.tour"].with_user(user)

    # ----------------------------------------------------------------- finding

    def test_found_by_name_or_phrase_in_both_languages(self):
        for words in ("add a contact for raghad", "new contact for Raghad", "add raghad", "اضافة رغد",
                      "جهة اتصال لرغد", "الاضافة رغد", "contact"):
            found = self.tours(self.user_a).find_tours(words)
            self.assertIn(self.tour.id, [tour["id"] for tour in found], words)
        self.assertNotIn(self.tour.id, [tour["id"] for tour in self.tours(self.user_a).find_tours("warehouse")])
        self.assertEqual(self.tours(self.user_a).find_tours("zzzz qqqq"), [])

    def test_the_phrase_said_whole_comes_first(self):
        other = self.tours(self.manager).create({"name": "Another way to contact Raghad", "sequence": 1})
        found = self.tours(self.user_a).find_tours("add raghad")
        self.assertEqual(found[0]["id"], self.tour.id)
        self.assertIn(other.id, [tour["id"] for tour in self.tours(self.user_a).find_tours("contact raghad")])

    def test_nobody_without_nudge_finds_any(self):
        with self.assertRaises(AccessError):
            self.tours(self.outsider).find_tours("contact")

    def test_a_tour_is_only_offered_where_its_app_is_installed(self):
        self.tour.module_name = "an_app_nobody_installed"
        self.assertNotIn(self.tour.id, [tour["id"] for tour in self.tours(self.user_a).find_tours("raghad")])
        self.assertEqual(self.tours(self.user_a).play(self.tour.id), {})
        self.tour.module_name = "base"
        self.assertIn(self.tour.id, [tour["id"] for tour in self.tours(self.user_a).find_tours("raghad")])

    def test_the_library_is_there_for_the_apps_installed(self):
        library = self.env["nudge.tour"].sudo().search([("is_library", "=", True)])
        self.assertEqual(len(library), 8)
        installed = self.env["nudge.tour"]._installed_modules()
        offered = {tour.name for tour in self.tours(self.user_a)._replayable() if tour.is_library}
        self.assertEqual(offered, {tour.name for tour in library if tour.module_name in installed})
        self.assertIn("Create a contact", offered)
        for tour in library:
            self.assertTrue(tour.action_xmlid and tour.phrases_en and tour.phrases_ar, tour.name)

    def test_the_library_steps_exist_in_this_database(self):
        installed = self.env["nudge.tour"]._installed_modules()
        for tour in self.env["nudge.tour"].sudo().search([("is_library", "=", True)]):
            if tour.module_name not in installed:
                continue
            data = self.tours(self.manager).play(tour.id)
            self.assertTrue(data["steps"], tour.name)
            self.assertEqual(data["skipped"], [], tour.name)
            self.assertTrue(data["action"], tour.name)

    # ------------------------------------------------------------------ rights

    def test_a_tour_for_some_groups_is_for_them(self):
        self.tour.group_ids = self.env.ref("base.group_system")
        self.assertEqual(self.tours(self.user_a).find_tours("raghad"), [])
        self.assertEqual(self.tours(self.user_a).play(self.tour.id), {})
        self.assertTrue(self.tours(self.manager).find_tours("raghad"), "Managers see all")

    def test_a_tour_of_a_company_is_for_that_company(self):
        other = self.env["res.company"].create({"name": "Raghad and Ebrahim Trading"})
        self.tour.company_id = other
        self.assertEqual(self.tours(self.user_a).find_tours("raghad"), [])
        self.user_a.company_ids = [(4, other.id)]
        self.assertTrue(self.tours(self.user_a).find_tours("raghad"))

    def test_users_replay_and_managers_edit(self):
        with self.assertRaises(AccessError):
            self.tour.with_user(self.user_a).write({"name": "Mine"})
        with self.assertRaises(AccessError):
            self.tours(self.user_a).create({"name": "Mine"})
        with self.assertRaises(AccessError):
            self.tour.step_ids[:1].with_user(self.user_a).unlink()
        self.tour.with_user(self.manager).write({"name": "Add a contact for Ebrahim"})

    # ------------------------------------------------------------------ replay

    def test_replay_opens_the_start_screen_and_gives_steps(self):
        data = self.tours(self.user_a).play(self.tour.id)
        self.assertEqual(data["action"], "base.action_partner_form")
        targets = [(step["target"]["type"], step["target"]["name"]) for step in data["steps"][:2]]
        self.assertEqual(targets, [("control", "new"), ("field", "name")])
        self.assertEqual(data["steps"][0]["text"], "Press New.")
        self.assertEqual(data["steps"][1]["text"], "Write Raghad.")
        self.assertTrue(all(step["advance"] == "auto" for step in data["steps"]))

    def test_what_this_database_does_not_have_is_skipped_with_a_note(self):
        data = self.tours(self.user_a).play(self.tour.id)
        self.assertEqual(len(data["skipped"]), 3 + 1, data["skipped"])
        notes = " ".join(data["skipped"])
        self.assertIn("Ghost", notes)
        self.assertIn("Ghost Button", notes)
        names = [step["target"]["name"] for step in data["steps"]]
        self.assertNotIn("x_field_nobody_has", names)
        self.assertNotIn("action_nobody_has", names)

    def test_a_start_screen_that_is_gone_is_not_opened(self):
        self.tour.action_xmlid = "base.action_nobody_has"
        self.assertFalse(self.tours(self.user_a).play(self.tour.id)["action"])
        self.tour.action_xmlid = "x; drop table res_users"
        self.assertFalse(self.tours(self.user_a).play(self.tour.id)["action"])

    def test_a_menu_step_walks_from_the_app_down(self):
        step = self.tour.step_ids.filtered(lambda candidate: candidate.kind == "menu")
        step.write({"name": self.leaf.with_context(lang="en_US").complete_name, "note": "Open the partners."})
        data = self.tours(self.manager).play(self.tour.id)
        menus = [item for item in data["steps"] if item["target"]["type"] == "menu"]
        self.assertEqual([item["target"]["name"] for item in menus], [str(self.app.id), str(self.leaf.id)])
        self.assertEqual(menus[-1]["text"], "Open the partners.")
        # Not their menu: skipped, not an error
        self.leaf.groups_id = self.env.ref("base.group_system")
        data = self.tours(self.user_a).play(self.tour.id)
        self.assertFalse([item for item in data["steps"] if item["target"]["type"] == "menu"])
        self.assertTrue(any("Nudge Tour Partners" in note or "menu" in note for note in data["skipped"]))

    def test_the_step_without_a_note_says_what_to_do(self):
        step = self.tour.step_ids.filtered(lambda candidate: candidate.name == "name" and candidate.res_model == "res.partner")
        step.note = False
        step.label = "Name"
        data = self.tours(self.user_a).play(self.tour.id)
        self.assertIn("Fill in “Name”.", [item["text"] for item in data["steps"]])

    # --------------------------------------------------------------- recording

    def test_a_manager_records_a_tour(self):
        steps = [
            {"kind": "menu", "menu_id": str(self.leaf.id), "label": "Partners", "note": "Open the contacts."},
            {"kind": "control", "name": "new", "label": "New", "note": "Press New.", "res_model": "res.partner",
             "view_type": "list"},
            {"kind": "field", "name": "name", "label": "Name", "note": "  Write   Raghad.  ",
             "res_model": "res.partner", "view_type": "form", "action": "base.action_partner_form"},
            {"kind": "tab", "name": "sales_purchases", "label": "Sales & Purchase", "res_model": "res.partner",
             "view_type": "form"},
            {"kind": "button", "name": "action_ok", "label": "OK"},
        ]
        action = self.env.ref("base.action_partner_form")
        tour_id = self.tours(self.manager).record(
            "Contacts for Ebrahim", steps, "contacts for ebrahim\n\n", "جهات اتصال ابراهيم",
            {"xml_id": "base.action_partner_form", "id": action.id})
        tour = self.env["nudge.tour"].browse(tour_id)
        self.assertEqual(tour.action_xmlid, "base.action_partner_form")
        self.assertEqual(tour.phrases_en, "contacts for ebrahim")
        self.assertEqual(tour.company_id, self.manager.company_id)
        self.assertEqual([step.kind for step in tour.step_ids], ["menu", "control", "field", "tab", "button"])
        self.assertEqual(tour.step_ids[0].name, self.leaf.get_external_id().get(self.leaf.id) or
                         self.leaf.with_context(lang="en_US").complete_name)
        self.assertEqual(tour.step_ids[2].note, "Write Raghad.")
        self.assertTrue(self.tours(self.user_a).find_tours("ebrahim"))
        self.assertTrue(self.tours(self.user_a).find_tours("جهات اتصال"))

    def test_the_start_screen_is_found_from_its_id(self):
        action = self.env.ref("base.action_partner_form")
        tour_id = self.tours(self.manager).record(
            "From an id", [{"kind": "control", "name": "new"}], start={"xml_id": "", "id": action.id})
        self.assertEqual(self.env["nudge.tour"].browse(tour_id).action_xmlid, "base.action_partner_form")

    def test_nothing_the_browser_sends_is_trusted(self):
        steps = [
            {"kind": "css", "name": "div > a"},
            {"kind": "button", "name": "a[href^=javascript]"},
            {"kind": "control", "name": "not_a_control"},
            {"kind": "menu", "menu_id": "999999999"},
            "nonsense",
            {"kind": "field", "name": "name", "res_model": "res.partner; DROP", "view_type": "<script>",
             "action": "a b"},
        ]
        tour = self.env["nudge.tour"].browse(self.tours(self.manager).record("Odd", steps))
        self.assertEqual(len(tour.step_ids), 1)
        self.assertEqual(tour.step_ids.res_model, "")
        self.assertEqual(tour.step_ids.view_type, "")
        self.assertEqual(tour.step_ids.action_xmlid, "")
        with self.assertRaises(UserError):
            self.tours(self.manager).record("Odd", steps[:2])
        with self.assertRaises(UserError):
            self.tours(self.manager).record("  ", [{"kind": "control", "name": "new"}])
        with self.assertRaises(UserError):
            self.tours(self.manager).record("Empty", [])

    def test_only_managers_record(self):
        with self.assertRaises(AccessError):
            self.tours(self.user_a).record("Mine", [{"kind": "control", "name": "new"}])

    # ------------------------------------------------------------- AI and search

    def test_the_slash_command_finds_tours_without_any_ai(self):
        quick = self.env["nudge.quick"]
        answer = quick.with_user(self.manager).run_command("/tour add raghad")
        self.assertEqual(answer["command"], "tour")
        self.assertEqual([tour["id"] for tour in answer["tours"]][:1], [self.tour.id])
        self.assertTrue(answer["can_record"])
        arabic = quick.with_user(self.user_a).run_command("/جولة اضافة رغد")
        self.assertEqual(arabic["command"], "tour")
        self.assertIn(self.tour.id, [tour["id"] for tour in arabic["tours"]])
        self.assertFalse(arabic["can_record"])
        everything = quick.with_user(self.user_a).run_command("/tour")
        self.assertIn(self.tour.id, [tour["id"] for tour in everything["tours"]])
        nothing = quick.with_user(self.user_a).run_command("/tour zzzz qqqq")
        self.assertIn("No tour is called", nothing["content"])
        self.assertIn("tour", [command["name"] for command in quick.with_user(self.user_a).list_commands()])

    def test_the_search_of_the_panel_finds_tours(self):
        found = self.env["nudge.quick"].with_user(self.user_a).find("add raghad")
        self.assertEqual([tour["id"] for tour in found["tours"]][:1], [self.tour.id])
        self.assertEqual(self.env["nudge.quick"].with_user(self.user_a).find("x")["tours"], [])

    def test_the_ai_may_show_a_stored_tour(self):
        for mode in ("talk", "agent"):
            names = [tool.name for tool in tools.base.available(self.env(user=self.user_a), mode)]
            self.assertIn("show_tour", names)
        self.assertNotIn("show_tour", [tool.name for tool in tools.base.available(self.env(user=self.outsider))])
        call = lambda arguments: tools.base.call(self.env(user=self.user_a), "show_tour", arguments)  # noqa: E731
        listed = call({})
        self.assertIn("Add a contact for Raghad", listed.content)
        self.assertIsNone(listed.event)
        result = call({"words": "add raghad"})
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.event["type"], "guidance")
        # The way to the start screen through the menus comes first, then the steps
        shown = [step["target"] for step in result.event["steps"] if step["target"]["type"] != "menu"]
        self.assertEqual(shown[0], {"type": "control", "name": "new"})
        self.assertIn("do not exist", result.content)
        by_id = call({"tour_id": self.tour.id})
        self.assertEqual(by_id.event["steps"], result.event["steps"])
        self.assertTrue(call({"tour_id": 999999999}).is_error)
        self.assertIsNone(call({"words": "zzzz qqqq"}).event)

    def test_the_ai_walks_to_the_start_screen_through_the_menus(self):
        tour = self.tours(self.manager).create({
            "name": "From the menus", "phrases_en": "walk to partners",
            # the partner action has its own menu on a bare database; Contacts has another one
            "action_xmlid": "contacts.action_contacts" if self.env.ref("contacts.action_contacts", raise_if_not_found=False)
            else "base.action_partner_form",
            "step_ids": [(0, 0, {"kind": "control", "name": "new"})],
        })
        result = tools.base.call(self.env(user=self.user_a), "show_tour", {"tour_id": tour.id})
        steps = result.event["steps"]
        self.assertEqual(steps[-1]["target"], {"type": "control", "name": "new"})
        self.assertEqual({step["target"]["type"] for step in steps[:-1]}, {"menu"})
        self.assertTrue(len(steps) > 1)

    def test_the_library_speaks_arabic(self):
        self.env["res.lang"]._activate_lang("ar_001")
        self.env["ir.module.module"]._load_module_terms(["nudge"], ["ar_001"])
        tour = self.env.ref("nudge.tour_create_contact").with_context(lang="ar_001")
        self.assertEqual(tour.name, "أنشئ جهة اتصال")
        notes = tour.step_ids.with_context(lang="ar_001").mapped("note")
        self.assertIn("احفظ جهة الاتصال.", notes)
        # The replay is in the language of the person
        user = new_test_user(self.env, login="nudge_ar", groups="nudge.group_nudge_user", lang="ar_001")
        data = self.tours(user).with_context(lang="ar_001").play(tour.id)
        self.assertEqual(data["steps"][0]["text"], "اضغط جديد لإضافة جهة اتصال.")

    def test_a_plain_user_cannot_find_replay_record_or_edit(self):
        for call in (lambda: self.tours(self.outsider).find_tours("raghad"),
                     lambda: self.tours(self.outsider).play(self.tour.id),
                     lambda: self.tours(self.outsider).record("Ebrahim", [{"kind": "control", "name": "new"}]),
                     lambda: self.tour.with_user(self.outsider).write({"name": "Mine"}),
                     lambda: self.tour.step_ids[:1].with_user(self.outsider).write({"note": "Mine"})):
            with self.assertRaises(AccessError):
                call()
        self.assertEqual(self.tour.name, "Add a contact for Raghad")

    def test_a_user_of_another_company_does_not_see_the_tours_of_this_one(self):
        company_a = self.env["res.company"].create({"name": "Raghad Tours Co"})
        company_b = self.env["res.company"].create({"name": "Ebrahim Tours Co"})
        self.tour.company_id = company_a
        reader_b = new_test_user(
            self.env, login="nudge_tour_b", groups="nudge.group_nudge_user", company_id=company_b.id,
            company_ids=[(6, 0, company_b.ids)])
        self.assertEqual(self.tours(reader_b).find_tours("raghad"), [])
        self.assertEqual(self.tours(reader_b).play(self.tour.id), {})
        self.assertFalse(self.tours(reader_b).search([("id", "=", self.tour.id)]))
        self.assertFalse(self.env["nudge.tour.step"].with_user(reader_b).search([("tour_id", "=", self.tour.id)]))
