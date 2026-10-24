from odoo.tests import TransactionCase, new_test_user, tagged

LOOKS = ("nudge_high_contrast", "nudge_large_text", "nudge_compact", "nudge_dock", "nudge_company_accent")


@tagged("post_install", "-at_install", "nudge")
class TestUiPreferences(TransactionCase):
    """NP-23: how Nudge looks is kept per person, in the settings of the user, and nowhere else."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.raghad = new_test_user(cls.env, login="raghad_ui", name="Raghad", groups="nudge.group_nudge_user")
        cls.ebrahim = new_test_user(cls.env, login="ebrahim_ui", name="Ebrahim", groups="nudge.group_nudge_user")

    def test_defaults_are_off_and_the_colour_follows_odoo(self):
        settings = self.env["res.users.settings"]._find_or_create_for_user(self.raghad)
        for name in LOOKS:
            self.assertFalse(settings[name], name)
        self.assertEqual(settings.nudge_theme, "odoo")

    def test_the_person_writes_their_own_preferences(self):
        settings = self.env["res.users.settings"].with_user(self.raghad)._find_or_create_for_user(self.raghad)
        settings.set_res_users_settings({name: True for name in LOOKS})
        settings = self.env["res.users.settings"]._find_or_create_for_user(self.raghad)
        for name in LOOKS:
            self.assertTrue(settings[name], name)

    def test_the_preferences_of_one_person_are_not_the_ones_of_another(self):
        mine = self.env["res.users.settings"].with_user(self.raghad)._find_or_create_for_user(self.raghad)
        mine.set_res_users_settings({"nudge_high_contrast": True, "nudge_dock": True})
        other = self.env["res.users.settings"]._find_or_create_for_user(self.ebrahim)
        self.assertFalse(other.nudge_high_contrast)
        self.assertFalse(other.nudge_dock)

    def test_a_person_cannot_change_the_settings_of_another(self):
        theirs = self.env["res.users.settings"]._find_or_create_for_user(self.ebrahim)
        with self.assertRaises(Exception):
            theirs.with_user(self.raghad).write({"nudge_high_contrast": True})

    def test_violet_stays_a_choice(self):
        settings = self.env["res.users.settings"]._find_or_create_for_user(self.raghad)
        settings.nudge_theme = "violet"
        self.assertEqual(settings.nudge_theme, "violet")
