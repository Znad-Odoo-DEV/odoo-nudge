from datetime import date
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.models import nudge_credential
from odoo.addons.nudge.models.nudge_credential import Source

NOTIFY = "odoo.addons.nudge.models.nudge_compliance_feed.notify_user"
TODAY = date(2026, 10, 22)


@tagged("post_install", "-at_install")
class TestCredentialExpiry(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env.ref("account.group_account_manager", raise_if_not_found=False)
        cls.groups = "base.group_user" + (",account.group_account_manager" if group else "")
        cls.mine = cls.env.company
        cls.other = cls.env["res.company"].create({"name": "Your Other Company"})
        cls.raghad = new_test_user(cls.env, login="np17_raghad", groups=cls.groups,
                                   company_ids=[(6, 0, [cls.mine.id])], company_id=cls.mine.id)
        cls.ebrahim = new_test_user(cls.env, login="np17_ebrahim", groups=cls.groups,
                                    company_ids=[(6, 0, [cls.other.id])], company_id=cls.other.id)
        cls.plain = new_test_user(cls.env, login="np17_plain", groups="base.group_user",
                                  company_ids=[(6, 0, [cls.mine.id])], company_id=cls.mine.id)
        cls.Credential = cls.env["nudge.credential"]
        cls.Notice = cls.env["nudge.compliance.notice"].with_context(nudge_keep_transaction=True)

    def credential(self, name, days, company=None):
        return self.Credential.create({"name": name, "company_id": (company or self.mine).id,
                                       "expiry_date": date.fromordinal(TODAY.toordinal() + days)})

    def run_cron(self, today=TODAY):
        told = []
        with patch(NOTIFY, side_effect=lambda user, kind, message: told.append((user, message))):
            self.Notice._cron_deadlines(today=today)
        return told

    def thresholds(self, name, company=None):
        rows = self.env["nudge.compliance.notice"].search(
            [("pack", "=", "credential"), ("title", "=", name), ("company_id", "=", (company or self.mine).id)])
        return sorted(rows.mapped("threshold"))

    def test_each_threshold_is_told_once(self):
        self.credential("ETA token", 59)
        told = self.run_cron()
        self.assertEqual(self.thresholds("ETA token"), [60])
        count = len(told)
        self.assertTrue(count)
        self.assertEqual(self.run_cron(), [], "a second run tells nothing")
        for offset, expected in ((29, [30, 60]), (6, [7, 30, 60]), (-1, [0, 7, 30, 60])):
            self.run_cron(date.fromordinal(TODAY.toordinal() + 59 - offset))
            self.assertEqual(self.thresholds("ETA token"), expected)
        self.run_cron(date.fromordinal(TODAY.toordinal() + 59 + 40))
        self.assertEqual(self.thresholds("ETA token"), [0, 7, 30, 60], "an expiry long past is not news")

    def test_a_far_date_and_a_missed_cron(self):
        self.credential("Far", 61)
        self.credential("Late", 5)
        self.run_cron()
        self.assertEqual(self.thresholds("Far"), [])
        self.assertEqual(self.thresholds("Late"), [7], "only the tightest threshold reached is told")

    def test_a_renewed_credential_is_watched_afresh(self):
        credential = self.credential("Peppol registration", 3)
        self.run_cron()
        credential.expiry_date = date.fromordinal(TODAY.toordinal() + 20)
        self.run_cron()
        self.assertEqual(self.thresholds("Peppol registration"), [7, 30])

    def test_multi_company_and_managers_only(self):
        self.credential("Mine", 6)
        self.credential("Theirs", 6, self.other)
        told = self.run_cron()
        by_user = {}
        for user, message in told:
            by_user.setdefault(user, []).append(message["title"])
        self.assertTrue(any("Mine" in title for title in by_user[self.raghad]))
        self.assertFalse(any("Theirs" in title for title in by_user[self.raghad]))
        self.assertTrue(any("Theirs" in title for title in by_user[self.ebrahim]))
        self.assertFalse(any("Mine" in title for title in by_user.get(self.ebrahim, [])))
        self.assertNotIn(self.plain, by_user)

    def test_access_is_for_nudge_managers_in_their_companies(self):
        manager = new_test_user(self.env, login="np17_manager", groups="nudge.group_nudge_manager",
                                company_ids=[(6, 0, [self.mine.id])], company_id=self.mine.id)
        mine, theirs = self.credential("Mine", 6), self.credential("Theirs", 6, self.other)
        as_manager = self.Credential.with_user(manager)
        self.assertEqual(as_manager.search([("id", "in", (mine | theirs).ids)]), mine)
        as_manager.create({"name": "New", "company_id": self.mine.id, "expiry_date": TODAY})
        with self.assertRaises(AccessError):
            self.Credential.with_user(self.plain).search([])
        with self.assertRaises(AccessError):
            theirs.with_user(manager).write({"name": "x"})

    def test_a_source_is_used_only_when_model_and_field_exist(self):
        absent = (Source("no_model", "x.not.installed", "date", "company_id", "name"),
                  Source("no_field", "res.partner", "l10n_not_a_field", "company_id", "name"))
        present = Source("order", "sale.order", "date_order", "company_id", "name")
        with patch.object(nudge_credential, "SOURCES", absent + (present,)):
            self.assertEqual([s.key for s in self.Credential._sources()], ["order"])
        for source in self.Credential._sources():  # the real ones in this database
            self.assertIn(source.date_field, self.env[source.model]._fields)

    def test_a_present_field_of_another_module_is_watched_and_nothing_else_is_read(self):
        partner = self.env["res.partner"].create({"name": "Raghad"})
        order = self.env["sale.order"].create({
            "partner_id": partner.id, "company_id": self.mine.id, "name": "Ebrahim",
            "date_order": date.fromordinal(TODAY.toordinal() + 6)})
        source = Source("order", "sale.order", "date_order", "company_id", "name")
        asked = []
        original = type(self.env["sale.order"]).search_read

        def spy(records, domain=None, fields=None, **kw):
            asked.append((records._name, tuple(fields or ())))
            return original(records, domain, fields, **kw)

        with patch.object(nudge_credential, "SOURCES", (source,)), \
                patch.object(type(self.env["sale.order"]), "search_read", spy):
            rows = self.Credential._expiries(self.mine)
        self.assertIn(("order", order.id), [(key, row["id"]) for key, row in rows])  # demo orders are there too
        self.assertEqual(asked, [("sale.order", ("date_order", "name"))], "only the date and the name are read")
        with patch.object(nudge_credential, "SOURCES", (source,)):
            self.run_cron()
        self.assertEqual(self.thresholds("Ebrahim"), [7])

    def test_no_source_names_a_secret_field(self):
        for source in nudge_credential.SOURCES:
            for name in (source.date_field, source.name_field, source.company_field):
                for word in ("key", "content", "password", "pem", "token", "secret"):
                    self.assertNotIn(word, name)
