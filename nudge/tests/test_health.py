import json
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools

from .common import NudgeCase, require_modules


@tagged("post_install", "-at_install")
class TestHealth(NudgeCase, TransactionCase):
    """The health check: small deterministic checks, as the person, nothing changed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        require_modules(cls, "account", "purchase", "stock")
        cls.Report = cls.env["nudge.health.report"]
        Partner = cls.env["res.partner"]
        cls.twins = Partner.create([
            {"name": "Raghad Health One", "email": "Raghad.Health@example.com", "phone": "+971 50 123 4567",
             "vat": "AE 1234-5678"},
            {"name": "Raghad  Health-One!", "email": "raghad.health@example.com ", "phone": "050 123 4567",
             "vat": "ae12345678"},
        ])
        cls.other_company = cls.env["res.company"].create({"name": "Ebrahim Health Co"})
        cls.manager.company_ids |= cls.other_company
        cls.twins.write({"company_id": cls.env.company.id})
        # Reads every app the checks look at: a manager of Nudge alone reads few of them
        groups = ["nudge.group_nudge_manager", "base.group_system"] + [
            xmlid for xmlid in ("sales_team.group_sale_manager", "stock.group_stock_manager",
                                "purchase.group_purchase_manager", "account.group_account_manager")
            if cls.env.ref(xmlid, raise_if_not_found=False)]
        cls.boss = new_test_user(cls.env, login="health_boss", groups=",".join(groups))
        cls.hidden = Partner.with_company(cls.other_company).create([
            {"name": "Ebrahim Hidden One", "email": "ebrahim.hidden@example.com", "company_id": cls.other_company.id},
            {"name": "Ebrahim Hidden Two", "email": "ebrahim.hidden@example.com", "company_id": cls.other_company.id},
        ])

    def run_as(self, user, company=None):
        company = company or user.company_id
        return self.Report.with_user(user).with_company(company).run_check()

    def line(self, report, check, key):
        return report.finding_ids.filtered(lambda finding: finding.check == check and finding.key == key)

    def shown(self, line):
        """:return: ids of the records the "Show me" of a line opens, as the one who reads it"""
        action = line.action_show()
        return set(self.env[action["res_model"]].with_user(self.env.user).search(action["domain"]).ids)

    # ------------------------------------------------------------ who may, what is left alone

    def test_only_a_manager_runs_it(self):
        with self.assertRaises(AccessError):
            self.Report.with_user(self.user_a).run_check()
        with self.assertRaises(AccessError):
            self.Report.with_user(self.agent).run_check()
        self.assertTrue(self.run_as(self.manager))

    def test_nothing_is_changed_but_the_report(self):
        before = self.twins.mapped("write_date") + self.twins.mapped("email")
        count = self.env["res.partner"].search_count([])
        self.run_as(self.manager)
        self.assertEqual(self.twins.mapped("write_date") + self.twins.mapped("email"), before)
        self.assertEqual(self.env["res.partner"].search_count([]), count)

    def test_every_check_is_in_the_list_with_a_sentence_for_each_of_its_findings(self):
        keys = [check.key for check in tools.health.CHECKS + tools.upgrade.UPGRADE_CHECKS]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(set(keys), set(tools.health.labels(self.env)))

    # ------------------------------------------------------------ contacts

    def test_duplicate_contacts_in_four_ways(self):
        report = self.run_as(self.manager)
        for key in ("email", "phone", "vat", "name"):
            line = self.line(report, "partner_duplicates", key)
            self.assertTrue(line, key)
            self.assertTrue(set(self.twins.ids) <= self.shown(line), key)
        self.assertEqual(self.line(report, "partner_duplicates", "email").severity, "warning")
        self.assertEqual(self.line(report, "partner_duplicates", "phone").severity, "info")

    def test_a_contact_below_a_company_is_not_a_duplicate_of_it(self):
        company = self.env["res.partner"].create({"name": "Raghad Parent Health", "email": "parent.health@example.com"})
        self.env["res.partner"].create({"name": "Raghad Child Health", "email": "parent.health@example.com",
                                        "parent_id": company.id})
        report = self.run_as(self.manager)
        self.assertNotIn(company.id, self.shown(self.line(report, "partner_duplicates", "email")))

    def test_only_what_the_person_may_read_is_looked_at(self):
        # The manager works in the first company: the twins of the other one are not theirs to read
        report = self.run_as(self.manager)
        self.assertFalse(set(self.hidden.ids) & self.shown(self.line(report, "partner_duplicates", "email")))
        other = self.run_as(self.manager, self.other_company)
        self.assertEqual(other.company_id, self.other_company)
        self.assertTrue(set(self.hidden.ids) <= self.shown(self.line(other, "partner_duplicates", "email")))
        self.assertFalse(set(self.twins.ids) & self.shown(self.line(other, "partner_duplicates", "email")))

    # ------------------------------------------------------------ products

    def test_duplicate_products_and_their_data(self):
        Product = self.env["product.product"]
        categ = self.env["product.category"].create({"name": "Health Category"})
        first, second = Product.create([
            {"name": "Health Bolt", "default_code": "HB-1", "barcode": "5901234123457", "categ_id": categ.id,
             "list_price": 0},
            {"name": "health bolt", "default_code": "HB-1", "categ_id": categ.id, "list_price": 10},
        ])
        report = self.run_as(self.manager)
        self.assertTrue({first.id, second.id} <= self.shown(self.line(report, "product_duplicates", "code")))
        self.assertTrue({first.product_tmpl_id.id, second.product_tmpl_id.id}
                        <= self.shown(self.line(report, "product_duplicates", "name")))
        self.assertIn(first.product_tmpl_id.id, self.shown(self.line(report, "product_data", "no_sale_price")))
        self.assertNotIn(second.product_tmpl_id.id, self.shown(self.line(report, "product_data", "no_sale_price")))

    # ------------------------------------------------------------ users

    def test_users_without_groups_idle_and_admins(self):
        bare = new_test_user(self.env, login="health_bare", groups="base.group_user")
        admin = new_test_user(self.env, login="health_admin", groups="base.group_system")
        idle = new_test_user(self.env, login="health_idle", groups="base.group_user,base.group_partner_manager")
        old = fields.Datetime.now() - timedelta(days=200)
        self.env.cr.execute("UPDATE res_users SET create_date = %s WHERE id = %s", [old, idle.id])
        self.env.invalidate_all()
        report = self.run_as(self.manager)
        self.assertIn(admin.id, self.shown(self.line(report, "user_access", "admins")))
        self.assertNotIn(self.env.ref("base.user_admin").id, self.shown(self.line(report, "user_access", "admins")))
        self.assertIn(idle.id, self.shown(self.line(report, "user_access", "idle")))
        self.assertNotIn(bare.id, self.shown(self.line(report, "user_access", "idle")))
        self.assertIn(bare.id, self.shown(self.line(report, "user_access", "no_groups")))
        self.assertNotIn(idle.id, self.shown(self.line(report, "user_access", "no_groups")))

    # ------------------------------------------------------------ attachments, mail, cron

    def test_orphan_attachments_are_found_in_a_sample(self):
        partner = self.env["res.partner"].create({"name": "Raghad Gone Health"})
        attachment = self.env["ir.attachment"].create({
            "name": "gone.txt", "raw": b"x", "res_model": "res.partner", "res_id": partner.id})
        kept = self.env["ir.attachment"].create({
            "name": "kept.txt", "raw": b"x", "res_model": "res.partner", "res_id": self.twins[0].id})
        self.env.cr.execute("DELETE FROM res_partner WHERE id = %s", [partner.id])
        self.env.invalidate_all()
        report = self.run_as(self.boss)
        line = self.line(report, "orphan_attachments", "orphans")
        self.assertIn(attachment.id, self.shown(line))
        self.assertNotIn(kept.id, self.shown(line))
        self.assertGreater(line.sample, 0)

    def test_mail_that_failed_and_actions_that_fail(self):
        mail = self.env["mail.mail"].create({"subject": "Health", "email_to": "raghad@example.com"})
        mail.state = "exception"
        # Reading the mail queue is for administrators: skipped, not failed, for a manager of Nudge
        report = self.run_as(self.manager)
        self.assertFalse(self.line(report, "mail_queue", "exception"))
        self.assertIn({"check": "mail_queue", "reason": "access"}, json.loads(report.skipped_json))
        self.assertIn("Email queue", report.skipped_text)
        report = self.run_as(self.boss)
        self.assertIn(mail.id, self.shown(self.line(report, "mail_queue", "exception")))
        if "failure_count" in self.env["ir.cron"]._fields:
            cron = self.env["ir.cron"].search([], limit=1)
            cron.failure_count = 3
            report = self.run_as(self.boss)
            self.assertIn(cron.id, self.shown(self.line(report, "scheduled_actions", "failing")))

    # ------------------------------------------------------------ the other apps

    def test_negative_stock_and_company_mismatch(self):
        if "stock.quant" not in self.env:
            self.skipTest("Inventory is not installed")
        warehouse = self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)], limit=1)
        product = self.env["product.product"].create({"name": "Health Negative", "is_storable": True}) \
            if "is_storable" in self.env["product.template"]._fields \
            else self.env["product.product"].create({"name": "Health Negative", "type": "product"})
        quant = self.env["stock.quant"].sudo().create({
            "product_id": product.id, "location_id": warehouse.lot_stock_id.id, "quantity": -4})
        report = self.run_as(self.boss)
        self.assertIn(quant.id, self.shown(self.line(report, "stock_negative", "quants")))
        self.assertIn(warehouse.lot_stock_id.id, self.shown(self.line(report, "stock_negative", "locations")))
        if "sale.order" in self.env:
            order = self.env["sale.order"].create({"partner_id": self.twins[0].id})
            self.env.cr.execute("UPDATE res_partner SET company_id = %s WHERE id = %s",
                                [self.other_company.id, self.twins[0].id])
            self.env.invalidate_all()
            report = self.run_as(self.boss)
            self.assertIn(order.id, self.shown(self.line(report, "company_consistency", "sale.order.partner_id")))

    # ------------------------------------------------------------ the report, kept

    def test_the_next_run_shows_what_changed(self):
        first = self.run_as(self.manager)
        self.assertTrue(self.line(first, "partner_duplicates", "email").is_new)
        second = self.run_as(self.manager)
        self.assertEqual(second.previous_id, first)
        self.assertFalse(self.line(second, "partner_duplicates", "email").is_new)
        self.assertEqual(self.line(second, "partner_duplicates", "email").delta, 0)
        self.twins[1].email = "someone.else.health@example.com"
        third = self.run_as(self.manager)
        self.assertTrue(self.line(third, "partner_duplicates", "email").delta < 0 or not self.line(third, "partner_duplicates", "email"))
        self.assertEqual(json.loads(third.resolved_json) != [], not self.line(third, "partner_duplicates", "email"))
        self.twins[1].email = "raghad.health@example.com"
        fourth = self.run_as(self.manager)
        self.assertTrue(self.line(fourth, "partner_duplicates", "email").is_new or
                        self.line(fourth, "partner_duplicates", "email").delta > 0)
        self.assertEqual(json.loads(fourth.findings_json)[0]["check"] in tools.health.BY_KEY, True)

    def test_only_the_latest_reports_of_a_company_are_kept(self):
        for _index in range(13):
            last = self.run_as(self.manager)
        reports = self.Report.with_user(self.manager).search([("company_id", "=", self.manager.company_id.id)])
        self.assertEqual(len(reports), 10)
        self.assertIn(last, reports)

    def test_a_report_is_seen_by_the_companies_of_the_reader_only(self):
        report = self.run_as(self.manager, self.other_company)
        self.assertIn(report, self.Report.with_user(self.manager).search([]))
        self.assertNotIn(report, self.Report.with_user(self.manager).with_context(
            allowed_company_ids=[self.manager.company_id.id]).search([]))
        with self.assertRaises(AccessError):
            self.Report.with_user(self.user_a).search([])

    # ------------------------------------------------------------ language, time, errors

    def test_the_sentences_are_in_the_language_of_the_reader(self):
        self.env["res.lang"]._activate_lang("ar_001")
        # The terms of the module in that language are loaded for the test
        self.env["ir.module.module"]._load_module_terms(["nudge"], ["ar_001"], overwrite=True)
        report = self.run_as(self.manager)
        line = self.line(report, "partner_duplicates", "email")
        english = line.message
        self.assertIn("email addresses", english)
        arabic = line.with_context(lang="ar_001").message
        self.assertNotEqual(arabic, english)
        self.assertIn("البريد", arabic)

    def test_a_check_that_takes_too_long_is_skipped_and_the_others_go_on(self):
        slow = tools.health.PartnerDuplicates()

        def slowly(env, clock):
            env.cr.execute("SELECT pg_sleep(2)")
            return []

        slow.run = slowly
        self.patch(tools.health, "CHECKS", (slow, tools.health.UserAccess()))
        self.patch(tools.health, "CHECK_SECONDS", 0.2)
        found, skipped = tools.health.run_all(self.env(user=self.manager))
        self.assertEqual(skipped, [{"check": "partner_duplicates", "reason": "timeout"}])
        self.assertTrue(any(item["check"] == "user_access" for item in found))
        # The transaction is still usable
        self.env.cr.execute("SELECT 1")

    def test_a_failing_check_does_not_stop_the_others(self):
        broken = tools.health.ProductData()

        def explode(env, clock):
            raise ValueError("broken on purpose")

        broken.run = explode
        self.patch(tools.health, "CHECKS", (broken, tools.health.PartnerDuplicates()))
        found, skipped = tools.health.run_all(self.env(user=self.manager))
        self.assertEqual(skipped, [{"check": "product_data", "reason": "error"}])
        self.assertTrue(any(item["check"] == "partner_duplicates" for item in found))

    # ------------------------------------------------------------ the command and the schedule

    def test_the_command_in_both_languages(self):
        quick = self.env["nudge.quick"]
        for text in ("/health", "/فحص"):
            answer = quick.with_user(self.manager).run_command(text)
            self.assertEqual(answer["command"], "health")
            self.assertTrue(answer["health"]["findings"], text)
            finding = answer["health"]["findings"][0]
            self.assertTrue({"id", "severity", "message", "count", "can_show"} <= set(finding))
            action = self.env["nudge.health.finding"].with_user(self.manager).browse(finding["id"]).action_show()
            self.assertEqual(action["type"], "ir.actions.act_window")
        closed = quick.with_user(self.user_a).run_command("/health")
        self.assertIn("not open to you", closed["content"])
        self.assertNotIn("health", [command["name"] for command in quick.with_user(self.user_a).list_commands()])
        self.assertIn("health", [command["name"] for command in quick.with_user(self.manager).list_commands()])

    def test_the_weekly_check_runs_as_the_manager_who_asked(self):
        company = self.manager.company_id
        report = self.run_as(self.manager)
        report.with_user(self.manager).action_toggle_weekly()
        self.assertEqual(company.nudge_health_user_id, self.manager)
        self.assertTrue(report.with_user(self.manager).weekly)
        # A report younger than a week: nothing to do
        count = self.Report.search_count([("company_id", "=", company.id)])
        self.Report.with_context(nudge_keep_transaction=True)._cron_run()
        self.assertEqual(self.Report.search_count([("company_id", "=", company.id)]), count)
        report.write({"date": fields.Datetime.now() - timedelta(days=8)})
        self.Report.search([("company_id", "=", company.id), ("id", "!=", report.id)]).unlink()
        self.Report.with_context(nudge_keep_transaction=True)._cron_run()
        newest = self.Report.search([("company_id", "=", company.id)], limit=1)
        self.assertNotEqual(newest, report)
        self.assertEqual(newest.user_id, self.manager)
        # A person who is no more a manager is not run for
        groups_field = "group_ids" if "group_ids" in self.manager._fields else "groups_id"
        self.manager[groups_field] = [(3, self.env.ref("nudge.group_nudge_manager").id)]
        newest.write({"date": fields.Datetime.now() - timedelta(days=8)})
        before = self.Report.search_count([("company_id", "=", company.id)])
        self.Report.with_context(nudge_keep_transaction=True)._cron_run()
        self.assertEqual(self.Report.search_count([("company_id", "=", company.id)]), before)
        with self.assertRaises(AccessError):
            report.with_user(self.user_a).action_toggle_weekly()

    def test_a_plain_user_cannot_run_or_read_reports(self):
        report = self.run_as(self.manager)
        for user in (self.outsider, self.user_a):
            with self.assertRaises(AccessError):
                self.Report.with_user(user).run_check()
            with self.assertRaises(AccessError):
                self.Report.with_user(user).search([])
            with self.assertRaises(AccessError):
                report.with_user(user).read(["findings_json"])
            with self.assertRaises(AccessError):
                self.env["nudge.health.finding"].with_user(user).search([])

    def test_a_manager_of_another_company_does_not_see_the_reports_of_this_one(self):
        report = self.run_as(self.manager)
        manager_b = new_test_user(
            self.env, login="health_mgr_b", groups="nudge.group_nudge_manager",
            company_id=self.other_company.id, company_ids=[(6, 0, self.other_company.ids)])
        self.assertNotIn(report, self.Report.with_user(manager_b).search([]))
        self.assertFalse(self.env["nudge.health.finding"].with_user(manager_b).search(
            [("report_id", "=", report.id)]))
        with self.assertRaises(AccessError):
            report.with_user(manager_b).read(["findings_json"])
