from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.tools import commands, explainer

from .common import NudgeCase


def form(fields, **extra):
    """A screen as the browser describes it: a contact form"""
    snapshot = {
        "v": 1, "model": "res.partner", "view_type": "form",
        "action": {"name": "Contacts"},
        "record": {"id": None, "is_new": True, "unsaved_changes": True},
        "fields": fields, "buttons": [],
    }
    snapshot.update(extra)
    return snapshot


def field(name, label, type_="char", value=None, **extra):
    return dict({"name": name, "label": label, "type": type_, "value": value, "on_screen": True}, **extra)


@tagged("post_install", "-at_install")
class TestExplainer(NudgeCase, TransactionCase):
    """The screen explained without any AI: why a form is not saved, what a
    field is, what is on screen."""

    def run_command(self, user, text, snapshot):
        return self.env["nudge.quick"].with_user(user).run_command(text, None, snapshot)

    def items(self, answer, kind=None):
        found = []
        for section in answer["explain"]["sections"]:
            found += [item for item in section["items"] if kind in (None, item["kind"])]
        return found

    def names(self, answer, kind):
        return [item["field"] for item in self.items(answer, kind)]

    def administrator(self):
        return new_test_user(self.env, login="nudge_adm", groups="nudge.group_nudge_user,base.group_system")

    def test_the_commands_are_known_in_both_languages(self):
        for words, name in (("/why", "why"), ("/لماذا", "why"), ("/field", "field"), ("/حقل", "field"),
                            ("/here", "here"), ("/هنا", "here")):
            self.assertEqual(commands.parse(words)[0].name, name)
        listed = {command["name"]: command for command in commands.listed(self.env(user=self.user_a))}
        for name in commands.SCREEN_COMMANDS:
            self.assertTrue(listed[name]["at_once"], name)
        self.assertFalse(listed["screen"]["at_once"])

    def test_required_fields_still_empty(self):
        snapshot = form([
            field("name", "Name", required=True),
            field("email", "Email", value="raghad@example.com"),
            field("function", "Job Position", required=True, value="Engineer"),
            field("active", "Active", "boolean", False, required=True),
        ])
        answer = self.run_command(self.user_contacts, "/why", snapshot)
        self.assertEqual(self.names(answer, "missing"), ["name"])
        # Technical names are for administrators and developers
        self.assertNotIn("(name)", self.items(answer, "missing")[0]["text"])

    def test_a_normal_user_is_told_the_label_and_never_the_technical_name(self):
        snapshot = form([field("partner_id", "Customer", "many2one", None, required=True)])
        answer = self.run_command(self.user_contacts, "/why", snapshot)
        self.assertEqual(self.items(answer, "missing")[0]["text"], "Customer must be filled in.")
        self.assertEqual(answer["explain"]["sections"][0]["head"], "Fields still to fill in")
        text = self.items(self.run_command(self.administrator(), "/why", snapshot), "missing")[0]["text"]
        self.assertIn("Customer (partner_id)", text)

    def test_a_required_one2many_with_no_line_is_empty(self):
        snapshot = form([field("child_ids", "Contacts", "one2many", {"count": 0}, required=True)])
        answer = self.run_command(self.user_contacts, "/why", snapshot)
        self.assertEqual(self.names(answer, "missing"), ["child_ids"])

    def test_a_condition_of_the_view_is_said_in_words(self):
        snapshot = form([field("name", "Name", required=True, required_expr="type == 'invoice'")])
        text = self.items(self.run_command(self.user_contacts, "/why", snapshot), "missing")[0]["text"]
        self.assertIn("Invoice", text)
        self.assertNotIn("type ==", text)
        text = self.items(self.run_command(self.administrator(), "/why", snapshot), "missing")[0]["text"]
        self.assertIn("type == 'invoice'", text)
        self.assertIn("(name)", text)

    def test_what_the_web_client_marks_invalid(self):
        snapshot = form([field("email", "Email", value="nope", invalid=True)])
        answer = self.run_command(self.user_contacts, "/why", snapshot)
        self.assertEqual(self.names(answer, "invalid"), ["email"])

    def test_the_last_message_is_given(self):
        snapshot = form([], error="Invalid fields: Name")
        answer = self.run_command(self.user_contacts, "/why", snapshot)
        self.assertEqual(self.items(answer, "message")[0]["text"], "Invalid fields: Name")

    def test_a_required_field_that_is_readonly_is_a_lock(self):
        snapshot = form([field("name", "Name", required=True, readonly=True, readonly_expr="active == False")])
        answer = self.run_command(self.user_contacts, "/why", snapshot)
        self.assertEqual(self.names(answer, "locked"), ["name"])
        self.assertEqual(self.names(answer, "missing"), [])

    def test_a_missing_right_is_told(self):
        # An employee reads contacts but does not create them
        snapshot = form([field("name", "Name", value="Raghad")])
        answer = self.run_command(self.user_a, "/why", snapshot)
        self.assertEqual(len(self.items(answer, "rights")), 1)
        answer = self.run_command(self.user_contacts, "/why", snapshot)
        self.assertEqual(self.items(answer, "rights"), [])

    def test_a_record_that_cannot_be_changed(self):
        partner = self.env["res.partner"].create({"name": "Raghad"})
        snapshot = form([field("name", "Name", value="Raghad")], record={"id": partner.id, "unsaved_changes": True})
        answer = self.run_command(self.user_a, "/why", snapshot)
        self.assertEqual(len(self.items(answer, "rights")), 1)

    def test_a_link_to_another_company(self):
        company = self.env["res.company"].create({"name": "Other company"})
        vendor = self.env["res.partner"].create({"name": "Ebrahim", "company_id": company.id})
        snapshot = form([
            field("company_id", "Company", "many2one", self.env.company.name, rid=self.env.company.id),
            field("partner_id", "Vendor", "many2one", "Ebrahim", rid=vendor.id),
        ], model="product.supplierinfo")
        answer = self.run_command(self.user_a, "/why", snapshot)
        self.assertEqual(self.names(answer, "company"), ["partner_id"])
        # The same company on both sides: nothing to say
        same = self.env["res.partner"].create({"name": "Raghad", "company_id": self.env.company.id})
        snapshot["fields"][1]["rid"] = same.id
        self.assertEqual(self.names(self.run_command(self.user_a, "/why", snapshot), "company"), [])

    def test_a_record_of_a_company_that_is_not_selected(self):
        company = self.env["res.company"].create({"name": "Other company"})
        snapshot = form([field("company_id", "Company", "many2one", "Other company", rid=company.id)])
        answer = self.run_command(self.user_contacts, "/why", snapshot)
        self.assertEqual(self.names(answer, "company"), ["company_id"])

    def test_nothing_in_the_way(self):
        snapshot = form([field("name", "Name", value="Raghad", required=True)])
        answer = self.run_command(self.user_contacts, "/why", snapshot)
        self.assertEqual(len(self.items(answer)), 1)
        self.assertEqual(self.items(answer)[0]["kind"], "info")

    def test_not_a_form(self):
        answer = self.run_command(self.user_contacts, "/why", form([], view_type="list"))
        self.assertIsNone(self.items(answer)[0]["field"])

    def test_no_screen_no_answer(self):
        answer = self.run_command(self.user_contacts, "/why", None)
        self.assertIn("cannot read the screen", answer["content"])

    def test_it_works_without_any_provider(self):
        self.env["nudge.provider"].search([]).active = False
        snapshot = form([field("name", "Name", required=True)])
        answer = self.run_command(self.user_contacts, "/why", snapshot)
        self.assertEqual(self.names(answer, "missing"), ["name"])

    # --- A field

    def explained(self, user, text, snapshot=None):
        return self.items(self.run_command(user, f"/field {text}".strip(), snapshot or form([])))

    def test_a_field_by_its_label_or_its_name(self):
        for text in ("Job Position", "function", "job pos"):
            items = self.explained(self.user_contacts, text)
            self.assertIn("Job Position", items[0]["text"], text)
            self.assertEqual(items[0]["field"], "function")

    def test_a_selection_gives_its_choices(self):
        texts = " ".join(item["text"] for item in self.explained(self.user_contacts, "type"))
        self.assertIn("The choices are:", texts)
        self.assertIn("Invoice", texts)

    def test_computed_and_related_in_words(self):
        related = " ".join(item["text"] for item in self.explained(self.user_contacts, "country_code"))
        self.assertIn("taken from another record", related)
        computed = " ".join(item["text"] for item in self.explained(self.user_contacts, "display_name"))
        self.assertIn("worked out by Odoo", computed)

    def test_required_and_readonly_with_their_reason(self):
        snapshot = form([field("function", "Job Position", required=True, required_expr="type == 'contact'",
                               readonly=True, readonly_expr="active == False")])
        text = " ".join(item["text"] for item in self.explained(self.user_contacts, "function", snapshot))
        self.assertIn("Required", text)
        self.assertIn("Read-only on this screen", text)
        self.assertIn("Contact", text)

    def test_technical_name_for_administrators_only(self):
        plain = " ".join(item["text"] for item in self.explained(self.user_contacts, "function"))
        self.assertNotIn("res.partner", plain)
        technical = " ".join(item["text"] for item in self.explained(self.administrator(), "function"))
        self.assertIn("Technical name: function, on res.partner", technical)

    def test_the_groups_that_may_edit_it(self):
        admin = self.administrator()
        name = next(name for name, fld in self.env["res.partner"].with_user(admin)._fields.items()
                    if fld.groups and fld.type in ("char", "many2one", "boolean", "integer", "float", "selection")
                    and admin.has_group(fld.groups.split(",")[0].strip()))
        texts = " ".join(item["text"] for item in self.explained(admin, name))
        self.assertIn("Only these groups see and edit it:", texts)

    def test_the_field_the_person_was_in(self):
        snapshot = form([field("function", "Job Position")], focused_field="function")
        self.assertEqual(self.explained(self.user_contacts, "", snapshot)[0]["field"], "function")
        nothing = self.explained(self.user_contacts, "", form([field("function", "Job Position")]))
        self.assertIn("Click in a field first", nothing[0]["text"])

    def test_an_unknown_field(self):
        items = self.explained(self.user_contacts, "zzzz qqqq")
        self.assertIn("No field of this screen", items[0]["text"])

    def test_the_arabic_command(self):
        answer = self.run_command(self.user_contacts, "/حقل function", form([]))
        self.assertEqual(self.items(answer)[0]["field"], "function")

    # --- The screen

    def test_what_is_here(self):
        snapshot = form(
            [field("name", "Name", value="Raghad"), field("state", "Status", value="Draft")],
            record={"id": 5, "name": "Raghad", "unsaved_changes": True},
            buttons=[
                {"name": "action_a", "label": "Send", "kind": "object", "on_screen": True, "help": "Sends it"},
                {"name": "action_b", "label": "Hidden", "kind": "object", "on_screen": False},
                {"name": "stat", "label": "Invoices", "kind": "object", "smart": True, "on_screen": True},
                {"name": "save", "label": "Save", "kind": "special", "on_screen": True},
            ],
            tabs=[{"name": "t", "label": "Sales"}])
        answer = self.run_command(self.user_contacts, "/here", snapshot)
        sections = {section["head"]: section["items"] for section in answer["explain"]["sections"]}
        text = " ".join(item["text"] for item in sections[""])
        self.assertIn("Contacts", text)
        self.assertIn("with changes not saved yet", text)
        self.assertIn("Status: Draft", text)
        buttons = [item["text"] for head, items in sections.items() if "Buttons" in head for item in items]
        self.assertEqual(buttons, ["Send: Sends it"])
        smart = [item["text"] for head, items in sections.items() if "Smart" in head for item in items]
        self.assertEqual(smart, ["Invoices"])
        self.assertNotIn("res.partner", text)

    # --- Conditions in words

    def test_conditions_in_words(self):
        model = self.env["res.partner"].with_user(self.user_contacts)
        cases = {
            "type == 'invoice'": "is Invoice",
            "type != 'contact'": "is not Contact",
            "type in ('invoice', 'delivery')": "is one of:",
            "not active": "is not ticked",
            "active": "is ticked",
            "not function": "is empty",
            "active and type == 'contact'": " and ",
        }
        for expression, expected in cases.items():
            self.assertIn(expected, explainer.condition_words(model.env, model, expression) or "", expression)
        # Too involved to be said, or about something unknown
        for expression in ("(active or function) and type", "parent_id.name == 'x'", "foo == 1", "a and b or c"):
            self.assertIsNone(explainer.condition_words(model.env, model, expression), expression)
