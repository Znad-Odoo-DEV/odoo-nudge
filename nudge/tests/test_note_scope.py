import glob
import importlib.util
import os

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.models import nudge_profile

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestNoteScope(NudgeCase, TransactionCase):
    """A note reaches the conversations of its company and its groups, and no others."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env.company
        cls.company_b = cls.env["res.company"].create({"name": "Other company"})
        cls.raghad = new_test_user(
            cls.env, login="raghad_a", groups="nudge.group_nudge_user", company_id=cls.company_a.id,
            company_ids=[(6, 0, cls.company_a.ids)])
        cls.ebrahim = new_test_user(
            cls.env, login="ebrahim_b", groups="nudge.group_nudge_user,account.group_account_invoice",
            company_id=cls.company_b.id, company_ids=[(6, 0, cls.company_b.ids)])
        cls.manager_a = new_test_user(
            cls.env, login="manager_a", groups="nudge.group_nudge_manager", company_id=cls.company_a.id,
            company_ids=[(6, 0, cls.company_a.ids)])
        cls.Note = cls.env["nudge.note"]
        cls.accountants = cls.env.ref("account.group_account_invoice")

    def overview(self, user):
        return self.env["nudge.profile"].with_user(user).with_context(
            allowed_company_ids=user.company_ids.ids)._get_overview()

    def test_a_note_of_company_b_is_not_in_the_prompt_of_company_a(self):
        self.Note.create({"name": "A", "body": "Only-in-A wording.", "company_id": self.company_a.id})
        self.Note.create({"name": "B", "body": "Only-in-B wording.", "company_id": self.company_b.id})
        self.Note.create({"name": "All", "body": "Everywhere wording.", "company_id": False})
        for_raghad, for_ebrahim = self.overview(self.raghad), self.overview(self.ebrahim)
        self.assertIn("Only-in-A", for_raghad)
        self.assertNotIn("Only-in-B", for_raghad)
        self.assertIn("Only-in-B", for_ebrahim)
        self.assertNotIn("Only-in-A", for_ebrahim)
        self.assertIn("Everywhere wording", for_raghad)
        self.assertIn("Everywhere wording", for_ebrahim)

    def test_the_system_prompt_of_a_conversation_keeps_to_the_company(self):
        self.Note.create({"name": "B", "body": "Only-in-B wording.", "company_id": self.company_b.id})
        self.Note.create({"name": "A", "body": "Only-in-A wording.", "company_id": self.company_a.id})
        adapter = self.use_adapter()
        self.ask(self.raghad, "Hello")
        system = adapter.calls[0]["system"][1]
        self.assertIn("Only-in-A", system)
        self.assertNotIn("Only-in-B", system)

    def test_a_note_for_accountants_is_not_for_everybody(self):
        self.Note.create({"name": "Books", "body": "Accountants-only wording.", "company_id": False,
                          "group_ids": [(6, 0, self.accountants.ids)]})
        self.assertNotIn("Accountants-only", self.overview(self.raghad))
        self.assertIn("Accountants-only", self.overview(self.ebrahim))

    def test_a_note_not_approved_is_not_told(self):
        self.Note.create({"name": "Maybe", "body": "Proposed wording.", "state": "proposed"})
        self.assertNotIn("Proposed wording", self.overview(self.raghad))

    def test_managers_see_the_notes_of_their_companies_only(self):
        mine = self.Note.create({"name": "A", "body": "x", "company_id": self.company_a.id})
        theirs = self.Note.create({"name": "B", "body": "y", "company_id": self.company_b.id})
        everyone = self.Note.create({"name": "All", "body": "z", "company_id": False})
        seen = self.Note.with_user(self.manager_a).search([("id", "in", (mine | theirs | everyone).ids)])
        self.assertEqual(seen, mine | everyone)
        with self.assertRaises(AccessError):
            theirs.with_user(self.manager_a).write({"body": "changed"})
        with self.assertRaises(AccessError):
            self.Note.with_user(self.raghad).search([])

    def test_a_new_note_and_a_new_lesson_take_the_company_of_the_writer(self):
        note = self.Note.with_user(self.manager_a).create({"name": "Words", "body": "We say offers."})
        self.assertEqual(note.company_id, self.company_a)
        from odoo.addons.nudge import tools  # noqa: PLC0415
        tools.base.call(self.env(user=self.ebrahim), "remember_lesson",
                        {"lesson": "Invoices are called bills here.", "why": "They said so"})
        lesson = self.Note.sudo().search([("kind", "=", "lesson")], order="id desc", limit=1)
        self.assertEqual(lesson.company_id, self.company_b)

    def test_the_cap_keeps_the_first_notes_and_says_which_are_left_out(self):
        old = nudge_profile.MAX_NOTES_CHARS
        nudge_profile.MAX_NOTES_CHARS = 200
        self.addCleanup(setattr, nudge_profile, "MAX_NOTES_CHARS", old)
        self.Note.search([]).unlink()
        first = self.Note.create({"name": "First", "body": "a" * 100, "sequence": 1, "company_id": False})
        big = self.Note.create({"name": "Big", "body": "b" * 150, "sequence": 2, "company_id": False})
        small = self.Note.create({"name": "Small", "body": "c" * 20, "sequence": 3, "company_id": False})
        text = self.Note._get_text()
        self.assertIn("a" * 100, text)
        self.assertNotIn("b" * 150, text)
        self.assertIn("c" * 20, text, "A note that fits still goes when an earlier one is too big")
        (first | big | small).invalidate_recordset()
        self.assertEqual((first | big | small).mapped("not_sent"), [False, True, False])
        # The manager sees it in the list
        listed = self.Note.with_user(self.manager).search([("id", "in", big.ids)])
        self.assertTrue(listed.not_sent)

    def test_the_migration_gives_old_notes_the_company_of_their_writer(self):
        # The folder is named for the version of the module (19.0, 18.0 or 17.0 as ported)
        [path] = glob.glob(os.path.join(
            os.path.dirname(__file__), "..", "migrations", "[0-9][0-9].0.1.58.0", "post-ext02.py"))
        spec = importlib.util.spec_from_file_location("post_ext02", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        written = self.Note.with_user(self.manager_a).create({"name": "W", "body": "x"})
        proposed = self.Note.sudo().create({"name": "P", "body": "y", "kind": "lesson", "state": "proposed",
                                            "proposed_by": self.ebrahim.id})
        nobody = self.Note.sudo().create({"name": "N", "body": "z"})
        (written | proposed | nobody).write({"company_id": False})
        self.env.flush_all()
        self.env.cr.execute("UPDATE nudge_note SET create_uid = 1 WHERE id = %s", [nobody.id])
        module.migrate(self.env.cr, "18.0.1.57.31")
        (written | proposed | nobody).invalidate_recordset()
        self.assertEqual(written.company_id, self.company_a)
        self.assertEqual(proposed.company_id, self.company_b)
        self.assertFalse(nobody.company_id, "Unknown writer: all companies, as before")
