import json

from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import prompt
from odoo.addons.nudge.profile import builder, retrieval

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestActivity(NudgeCase, TransactionCase):
    """Nudge knows where the business works, and reads there."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.partner"].create([{"name": f"Nudge Busy {number}"} for number in range(5)])
        # Industries: in the database for years, untouched since
        cls.env.cr.execute(
            "UPDATE res_partner_industry "
            "SET create_date = now() - interval '400 days', write_date = now() - interval '400 days'")
        cls.env["res.partner.industry"].invalidate_model()
        cls.profile = builder.build(cls.env)

    def piece(self, model):
        return self.profile.slice_ids.filtered(lambda piece: piece.key == f"activity:{model}")

    def test_the_database_is_studied(self):
        partners = self.piece("res.partner")
        self.assertTrue(partners)
        self.assertEqual(partners.kind, "activity")
        self.assertEqual(partners.model_name, "res.partner")
        data = partners.data
        self.assertEqual(data["records"], self.env["res.partner"].with_context(active_test=False).search_count([]))
        self.assertGreaterEqual(data["changed"], 5)
        self.assertGreaterEqual(data["created"], 5)
        self.assertTrue(data["last_change"])
        self.assertEqual(data["module"], "base")
        self.assertIn("used", partners.summary)
        actions = self.env["ir.actions.act_window"].search([("res_model", "=", "res.partner")])
        opening = self.env["ir.ui.menu"].search(
            [("action", "in", [f"ir.actions.act_window,{id_}" for id_ in actions.ids])])
        self.assertEqual(bool(data["menus"]), bool(opening), "The menus that open the contacts are known")

        industries = self.piece("res.partner.industry").data
        self.assertEqual((industries["changed"], industries["created"]), (0, 0))

        keys = self.profile.slice_ids.mapped("key")
        self.assertFalse([key for key in keys if key.startswith(("activity:ir.", "activity:nudge.",
                                                                 "activity:mail.", "activity:res.users"))],
                         "Odoo's machinery and Nudge itself are not the work of the business")

    def test_the_use_of_the_data_is_not_a_change_of_the_database(self):
        self.env["res.partner"].create({"name": "Nudge Another"})
        profile = builder.build(self.env)
        changes = profile.changes
        self.assertFalse([key for part in changes.values() for key in part if key.startswith("activity:")])

    def test_where_the_business_works_comes_first(self):
        found, strong = retrieval.locate(self.env(user=self.user_contacts), self.profile, "partner")
        models = [piece.model_name for piece in found]
        self.assertEqual(models[0], "res.partner")
        self.assertIn("res.partner.industry", models)
        self.assertLess(models.index("res.partner"), models.index("res.partner.industry"))
        self.assertGreaterEqual(strong, 2)

        found, _strong = retrieval.locate(self.env(user=self.user_contacts), self.profile, "partner industry")
        self.assertEqual(found[0].model_name, "res.partner.industry",
                         "What the question names comes before what is used most")

    def test_the_tool_counts_as_the_person(self):
        result = tools.base.call(self.env(user=self.user_contacts), "locate_data", {"topic": "partner"})
        self.assertFalse(result.is_error, result.content)
        found = json.loads(result.content)
        entries = {entry["model"]: entry for entry in found["found"]}
        partners = entries["res.partner"]
        self.assertEqual(partners["records"],
                         self.env["res.partner"].with_user(self.user_contacts).search_count([]))
        self.assertGreaterEqual(partners["changed_last_90_days"], 5)
        self.assertEqual(entries["res.partner.industry"]["changed_last_90_days"], 0)
        self.assertIn("The work on this is in", found["note"])
        self.assertIn("(res.partner)", found["note"].split(":")[0])

        named = json.loads(tools.base.call(
            self.env(user=self.user_contacts), "locate_data", {"topic": "partner industry"}).content)
        self.assertEqual(named["found"][0]["model"], "res.partner.industry")
        self.assertIn("Answer from", named["note"])
        for menu in partners.get("menus", []):
            self.assertIn(menu["menu_id"], self.env["ir.ui.menu"].with_user(self.user_contacts)._visible_menu_ids())

    def test_what_the_person_may_not_read_is_not_told(self):
        self.patch(self.registry["res.partner.industry"], "_nudge_has_access", lambda records, operation: False)
        env = self.env(user=self.user_contacts)
        self.assertNotIn("res.partner.industry",
                         [piece.model_name for piece in retrieval.locate(env, self.profile, "industry partner")[0]])
        self.assertNotIn("res.partner.industry", retrieval.where_the_work_is(env, self.profile))

    def test_where_the_work_is_in_every_conversation(self):
        work = retrieval.where_the_work_is(self.env(user=self.user_contacts), self.profile)
        self.assertIn("## Where the work is", work)
        self.assertIn("(res.partner)", work)
        self.assertNotIn("res.partner.industry", work, "What nobody touches is not where the work is")
        self.assertNotRegex(work, r"\d{2,} records", "Words, not numbers that not everybody may know")

        conversation = self.env["nudge.conversation"].with_user(self.user_contacts)._chat_open(None)
        job = conversation._chat_prepare("How many contacts do we have?")
        self.assertIn("## Where the work is", "\n".join(job["system"]))
        self.assertTrue(job["system"][-1].startswith(prompt.TALK_MODE), "The rules stay last")

    def test_arabic_is_read_however_it_is_written(self):
        # By the root: a salary and the salaries, however they are written
        self.assertEqual(retrieval._words("الرواتب"), retrieval._words("راتب"))
        self.assertEqual(retrieval._words("بالرواتب"), retrieval._words("رواتب"))
        self.assertEqual(retrieval._words("Invoices"), retrieval._words("invoice"))
        self.assertEqual(retrieval._words("أجازة"), retrieval._words("اجازه"))
        self.assertIn("payslip", retrieval._words("hr.payslip"))

    def test_offered_and_told(self):
        for mode in ("talk", "agent"):
            names = [tool.name for tool in tools.base.available(self.env(user=self.agent), mode)]
            self.assertIn("locate_data", names)
        self.assertIn("locate_data", prompt.SYSTEM_PERSONA)
        self.assertIn("locate_data", prompt.AGENT_MODE)
        self.assertIn("locate_data", prompt.TALK_MODE)
