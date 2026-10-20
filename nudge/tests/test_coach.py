from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.tools import coach

QUOTATIONS = "sale.action_quotations_with_onboarding"
ORDERS = "sale.action_orders"
INVOICES = "account.action_move_out_invoice_type"


def tour(identifier, name="A tour", steps=3, xmlid="", module=""):
    return {"id": identifier, "name": name, "steps": steps, "xmlid": xmlid, "module": module}


def screen(identifier, name, xmlid="", module="", app="Sales"):
    return {"id": identifier, "name": name, "app": app, "action_id": identifier, "xmlid": xmlid, "module": module}


@tagged("post_install", "-at_install", "nudge")
class TestCoachLogic(TransactionCase):
    """Progress and the order of the suggestions: plain functions, no user."""

    def test_progress_counts_the_key_screens_opened(self):
        screens = [screen(1, "Quotations"), screen(2, "Orders"), screen(3, "Customers"), screen(4, "Products")]
        self.assertEqual(coach.progress([], screens), (0, 4, 0))
        self.assertEqual(coach.progress([1, 3], screens), (2, 4, 50))
        # An action that is not a key screen counts for nothing
        self.assertEqual(coach.progress([1, 99], screens), (1, 4, 25))
        self.assertEqual(coach.progress([1, 2, 3, 4], screens), (4, 4, 100))
        # Nothing to learn is complete
        self.assertEqual(coach.progress([], []), (0, 0, 100))

    def test_a_required_tour_comes_before_any_screen(self):
        screens = [screen(1, "Quotations", QUOTATIONS, "sale")]
        tours = [tour(10, "Create a quotation", xmlid=QUOTATIONS), tour(11, "Expenses", module="hr")]
        step = coach.suggest(screens, [], [11], [], tours)
        self.assertEqual((step["kind"], step["tour_id"], step["required"]), ("tour", 11, True))
        # Taken: the screens come next
        step = coach.suggest(screens, [], [11], [11], tours)
        self.assertEqual((step["kind"], step["tour_id"]), ("screen", 10))

    def test_the_next_screen_is_the_first_not_opened_with_its_own_tour(self):
        screens = [screen(1, "Quotations", QUOTATIONS, "sale"), screen(2, "Orders", ORDERS, "sale"),
                   screen(3, "Invoices", INVOICES, "account")]
        tours = [tour(10, "Create a quotation", xmlid=QUOTATIONS, module="sale"),
                 tour(11, "Sales module tour", module="sale"), tour(12, "Invoices", module="account")]
        step = coach.suggest(screens, [], [], [], tours)
        self.assertEqual((step["screen"]["name"], step["tour_id"]), ("Quotations", 10))
        # Quotations opened: Orders, which has no tour of its own, gets the tour of its app
        step = coach.suggest(screens, [1], [], [], tours)
        self.assertEqual((step["screen"]["name"], step["tour_id"]), ("Orders", 10))
        # That tour taken: the other one of the app
        step = coach.suggest(screens, [1], [], [10], tours)
        self.assertEqual((step["screen"]["name"], step["tour_id"]), ("Orders", 11))
        # No tour for the app: the screen alone
        step = coach.suggest(screens, [1, 2], [], [], [])
        self.assertEqual((step["screen"]["name"], step["tour_id"]), ("Invoices", 0))
        self.assertIsNone(coach.suggest(screens, [1, 2, 3], [], [], tours))

    def test_minutes_of_a_tour(self):
        self.assertEqual(coach.minutes_of(tour(1, steps=1)), 1)
        self.assertEqual(coach.minutes_of(tour(1, steps=6)), 2)
        self.assertEqual(coach.minutes_of(tour(1, steps=7)), 3)


@tagged("post_install", "-at_install", "nudge")
class TestCoach(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        # Raghad sells; Ebrahim only reads contacts: two different roles
        cls.raghad = new_test_user(env, login="coach_raghad", groups="nudge.group_nudge_user,sales_team.group_sale_salesman")
        cls.ebrahim = new_test_user(env, login="coach_ebrahim", groups="nudge.group_nudge_user")
        cls.boss = new_test_user(env, login="coach_boss", groups="nudge.group_nudge_manager")
        cls.Coach = env["nudge.coach"]

    def log_in(self, user, days_ago):
        """A login of the user, ``days_ago`` days ago"""
        self.env["res.users.log"].with_user(user).sudo().create({})
        self.env.cr.execute(
            "UPDATE res_users_log SET create_date = %s WHERE create_uid = %s",
            (fields.Datetime.now() - timedelta(days=days_ago), user.id))
        self.env.invalidate_all()

    def screens_of(self, user):
        return {item["xmlid"] for item in self.Coach._screens(user)}

    # ------------------------------------------------------------ the role

    def test_the_key_screens_follow_the_groups(self):
        raghad, ebrahim = self.screens_of(self.raghad), self.screens_of(self.ebrahim)
        self.assertIn(QUOTATIONS, raghad)
        self.assertIn(ORDERS, raghad)
        self.assertNotIn(QUOTATIONS, ebrahim)
        # An app the person has no menu for is not theirs to learn
        self.assertNotIn(INVOICES, raghad)
        by_app = {item["app"] for item in self.Coach._screens(self.raghad)}
        self.assertIn("Sales", by_app)

    def test_a_screen_whose_action_is_not_on_this_database_is_left_out(self):
        self.env["nudge.coach.screen"].create({"name": "Ghost", "action_xmlid": "nowhere.action_ghost"})
        self.assertNotIn("nowhere.action_ghost", self.screens_of(self.raghad))

    def test_a_manager_adds_a_screen(self):
        self.env["nudge.coach.screen"].with_user(self.boss).create(
            {"name": "Customer invoices again", "action_xmlid": INVOICES, "module_name": "account"})
        with self.assertRaises(AccessError):
            self.env["nudge.coach.screen"].with_user(self.raghad).create(
                {"name": "x", "action_xmlid": INVOICES})

    # ------------------------------------------------------- who is followed

    def test_a_first_login_within_14_days_starts_the_coach(self):
        self.log_in(self.raghad, 3)
        self.assertTrue(self.Coach.with_user(self.raghad).status()["active"])
        self.assertEqual(len(self.Coach.search([("user_id", "=", self.raghad.id)])), 1)

    def test_an_old_user_is_not_followed_unless_a_manager_says_so(self):
        self.log_in(self.ebrahim, 40)
        self.assertFalse(self.Coach.with_user(self.ebrahim).status()["active"])
        self.assertFalse(self.Coach.with_user(self.ebrahim).get_card()["active"])
        self.Coach.with_user(self.boss).create({"user_id": self.ebrahim.id})
        card = self.Coach.with_user(self.ebrahim).get_card()
        self.assertTrue(card["active"])

    def test_without_a_login_on_record_nobody_is_followed(self):
        self.assertFalse(self.Coach.with_user(self.raghad).status()["active"])

    # ---------------------------------------------------------- the card

    def test_visits_move_the_progress_and_unknown_actions_are_ignored(self):
        self.log_in(self.raghad, 1)
        mine = self.Coach.with_user(self.raghad)
        card = mine.get_card()
        total = card["total"]
        self.assertEqual((card["opened"], card["percent"]), (0, 0))
        quotations = self.env.ref(QUOTATIONS)
        invoices = self.env.ref(INVOICES)
        # Invoices is not in her role; True and strings are not ids
        self.assertEqual(mine.record_visits([quotations.id, invoices.id, True, "7", -3]), 1)
        # The same screen twice counts once
        self.assertEqual(mine.record_visits([quotations.id]), 1)
        card = mine.get_card()
        self.assertEqual(card["opened"], 1)
        self.assertEqual(card["percent"], round(100 / total))
        self.assertEqual(self.Coach.search([("user_id", "=", self.raghad.id)]).seen_action_ids, [quotations.id])
        self.assertTrue(self.Coach.search([("user_id", "=", self.raghad.id)]).last_activity)

    def test_the_card_suggests_the_tour_of_the_next_screen(self):
        self.log_in(self.raghad, 1)
        card = self.Coach.with_user(self.raghad).get_card()
        self.assertTrue(card["next"])
        self.assertIn("Quotations", card["next"]["text"])
        self.assertTrue(card["next"]["tour_id"])
        self.assertIn("minute tour", card["next"]["text"])
        # The Arabic of the same sentence is in the catalogue
        mine = self.Coach.with_user(self.raghad.with_context(lang="ar_001"))
        self.assertTrue(mine.get_card()["next"]["text"])

    def test_assigned_tours_come_first_and_taking_one_is_recorded(self):
        self.log_in(self.raghad, 1)
        library = self.env.ref("nudge.tour_create_contact")
        row = self.Coach.with_user(self.boss).create({"user_id": self.raghad.id, "required_tour_ids": [(6, 0, library.ids)]})
        mine = self.Coach.with_user(self.raghad)
        card = mine.get_card()
        self.assertEqual(card["next"]["tour_id"], library.id)
        self.assertTrue(card["next"]["required"])
        self.assertEqual([item["done"] for item in card["tours"]], [False])
        self.assertTrue(mine.tour_done(library.id))
        self.assertEqual(row.done_tour_ids, library)
        self.assertEqual(row.tours_taken, "1/1")
        self.assertNotEqual(mine.get_card()["next"].get("tour_id"), library.id)

    def test_done_with_training_closes_the_card(self):
        self.log_in(self.raghad, 1)
        mine = self.Coach.with_user(self.raghad)
        self.assertTrue(mine.get_card()["active"])
        mine.finish()
        self.assertFalse(mine.get_card()["active"])
        row = self.Coach.search([("user_id", "=", self.raghad.id)])
        self.assertEqual(row.state, "trained")
        self.assertTrue(row.trained_on)
        # Not followed again by the 14 days
        self.assertFalse(mine.status()["active"])

    # -------------------------------------------------------- who sees what

    def test_a_plain_user_cannot_see_the_progress_of_others(self):
        self.log_in(self.raghad, 1)
        self.log_in(self.ebrahim, 1)
        self.Coach.with_user(self.raghad).get_card()
        self.Coach.with_user(self.ebrahim).get_card()
        seen_by_ebrahim = self.Coach.with_user(self.ebrahim).search([])
        self.assertEqual(seen_by_ebrahim.user_id, self.ebrahim)
        raghad_row = self.Coach.search([("user_id", "=", self.raghad.id)])
        with self.assertRaises(AccessError):
            raghad_row.with_user(self.ebrahim).read(["seen_action_ids"])
        # And nobody writes the state directly, their own included
        with self.assertRaises(AccessError):
            raghad_row.with_user(self.raghad).write({"state": "trained"})
        with self.assertRaises(AccessError):
            raghad_row.with_user(self.ebrahim).unlink()

    def test_a_user_cannot_mark_somebody_else_trained(self):
        self.log_in(self.raghad, 1)
        self.Coach.with_user(self.raghad).get_card()
        row = self.Coach.search([("user_id", "=", self.raghad.id)])
        with self.assertRaises(AccessError):
            row.with_user(self.ebrahim).action_mark_trained()
        with self.assertRaises(AccessError):
            row.with_user(self.raghad).action_mark_trained()

    def test_the_manager_sees_everyone_with_progress(self):
        self.log_in(self.raghad, 2)
        self.log_in(self.ebrahim, 2)
        for user in (self.raghad, self.ebrahim):
            self.Coach.with_user(user).get_card()
        self.Coach.with_user(self.raghad).record_visits([self.env.ref(QUOTATIONS).id])
        rows = self.Coach.with_user(self.boss).search([("state", "=", "training")])
        self.assertEqual(rows.user_id, self.raghad | self.ebrahim)
        by_user = {row.user_id: row for row in rows}
        self.assertEqual(by_user[self.raghad].opened_count, 1)
        self.assertGreater(by_user[self.raghad].progress, 0)
        self.assertEqual(by_user[self.ebrahim].progress, 100 if not by_user[self.ebrahim].screen_count else 0)
        self.assertTrue(by_user[self.raghad].last_activity)
        self.assertFalse(by_user[self.ebrahim].last_activity)

    def test_mark_trained_and_assign_tours(self):
        self.log_in(self.raghad, 2)
        self.Coach.with_user(self.raghad).get_card()
        row = self.Coach.with_user(self.boss).search([("user_id", "=", self.raghad.id)])
        library = self.env.ref("nudge.tour_create_quotation") | self.env.ref("nudge.tour_create_contact")
        wizard = self.env["nudge.coach.assign"].with_user(self.boss).with_context(
            active_model="nudge.coach", active_ids=row.ids).create({"tour_ids": [(6, 0, library.ids)]})
        self.assertEqual(wizard.coach_ids, row)
        wizard.action_assign()
        self.assertEqual(row.required_tour_ids, library)
        row.action_mark_trained()
        self.assertEqual(row.state, "trained")
        self.assertFalse(self.Coach.with_user(self.raghad).get_card()["active"])
        row.action_back_to_training()
        self.assertTrue(self.Coach.with_user(self.raghad).get_card()["active"])

    def test_the_key_screens_travel_in_a_recipe(self):
        Recipe = self.env["nudge.recipe"].with_user(self.boss)
        screens = self.env["nudge.coach.screen"].search([])
        package = Recipe._export(["screen"])
        items = package["recipe"]["items"]
        self.assertEqual(len(items), len(screens))
        item = next(entry for entry in items if entry["data"]["action_xmlid"] == QUOTATIONS)
        record, created = Recipe._apply("screen", item["key"], dict(item["data"], name="Offers"))
        self.assertFalse(created)
        self.assertEqual(record.name, "Offers")
        record, created = Recipe._apply("screen", "fresh-key", {"name": "Mine", "action_xmlid": ORDERS})
        self.assertTrue(created)
