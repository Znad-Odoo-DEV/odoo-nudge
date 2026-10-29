import base64
import json
import os
import re
from datetime import date
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.tools import compliance, compliance_feed

NOTIFY = "odoo.addons.nudge.models.nudge_compliance_feed.notify_user"
DOWNLOAD = "odoo.addons.nudge.models.nudge_compliance_feed._download"
ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def bundled(code):
    with open(os.path.join(compliance.PACKS_HERE, f"{code}.json"), encoding="utf-8") as handle:
        return json.load(handle)


def public_text(private):
    return private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


@tagged("post_install", "-at_install")
class TestPackContent(TransactionCase):
    """The e-invoicing content of NP-01: each date is sourced, reviewed and told to be confirmed."""

    def checks(self, code):
        return {entry.key: entry for entry in compliance.load_all(self.env)[0][code].checks}

    def test_every_pack_has_a_review_date_and_every_dated_check_a_source(self):
        packs, refused = compliance.load_all(self.env)
        self.assertEqual(refused, [])
        for code in ("ae", "sa", "qa", "kw", "bh", "om", "eg"):
            pack = packs[code]
            self.assertTrue(ISO.fullmatch(pack.reviewed), code)
            for entry in pack.checks:
                if entry.deadline or entry.key in ("zatca_wave", "e_receipt", "fawtara"):
                    self.assertTrue(entry.source.startswith("https://"), (code, entry.key))
                    self.assertTrue(ISO.fullmatch(entry.reviewed), (code, entry.key))

    def test_the_uae_has_its_e_invoicing_dates(self):
        checks = self.checks("ae")
        self.assertEqual(checks["einvoicing_asp"].deadline, "2026-10-30")
        self.assertEqual(checks["einvoicing_large"].deadline, "2027-01-01")
        self.assertEqual(checks["einvoicing_others"].deadline, "2027-07-01")
        for entry in (checks["einvoicing_asp"], checks["einvoicing_large"]):
            self.assertEqual(entry.severity, "warning")
            self.assertIn("confirm with the authority", entry.explanation["en"])
            self.assertIn("تأكد لدى الجهة المختصة", entry.explanation["ar"])
            self.assertIn("mof.gov.ae", entry.source)

    def test_saudi_egypt_and_oman_say_what_they_cannot_confirm(self):
        sa, eg, om = self.checks("sa"), self.checks("eg"), self.checks("om")
        self.assertIn("wave", sa["zatca_wave"].title["en"])
        self.assertIn("30 June 2026", sa["zatca_wave"].explanation["en"])
        self.assertIn("B2C", eg["e_receipt"].explanation["en"])
        self.assertEqual(om["fawtara"].deadline, "2027-04-01")
        self.assertIn("provisional", om["fawtara"].explanation["en"])
        for entry in (sa["zatca_wave"], eg["e_receipt"], om["fawtara"]):
            self.assertIn("confirm with the authority", entry.explanation["en"])
            self.assertTrue(entry.explanation["ar"])

    def test_the_saudi_qr_check_looks_for_the_module_that_has_the_field(self):
        self.assertEqual(self.checks("sa")["qr_code"].params["modules"], ["l10n_sa"])

    def test_a_bad_date_or_source_is_refused_in_words(self):
        base = bundled("ae")
        for change, text in (({"deadline": "01/01/2027"}, "deadline"), ({"reviewed": "2027-13-40"}, "reviewed"),
                             ({"source": "http://example.com"}, "https")):
            data = json.loads(json.dumps(base))
            data["checks"][0].update(change)
            with self.assertRaises(compliance.PackError) as caught:
                compliance.load_pack(data, "x.json")
            self.assertIn(text, str(caught.exception))
        data = json.loads(json.dumps(base))
        data["reviewed"] = "yesterday"
        with self.assertRaises(compliance.PackError):
            compliance.load_pack(data, "x.json")

    def test_the_detail_of_a_finding_shows_the_date_the_source_and_the_review(self):
        self.env.company.country_id = self.env.ref("base.ae")
        manager = new_test_user(self.env, login="nudge_np01_m", groups="nudge.group_nudge_manager")
        report = self.env["nudge.health.report"].with_user(manager).run_check(pack="ae")
        finding = next(f for f in report.finding_ids if f.key == "ae.einvoicing_asp")
        self.assertIn("Date: 2026-10-30", finding.detail)
        self.assertIn("Official source: https://mof.gov.ae", finding.detail)
        self.assertIn("Last reviewed: 2026-10-22", finding.detail)


@tagged("post_install", "-at_install")
class TestDeadlineNotices(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Group = cls.env.ref("account.group_account_manager", raise_if_not_found=False)
        cls.groups = "base.group_user" + (",account.group_account_manager" if Group else "")
        cls.uae = cls.env.company
        cls.uae.country_id = cls.env.ref("base.ae")
        cls.other = cls.env["res.company"].create({"name": "Your Other Company", "country_id": cls.env.ref("base.ae").id})
        cls.egypt = cls.env["res.company"].create({"name": "Your Egyptian Company", "country_id": cls.env.ref("base.eg").id})
        cls.raghad = new_test_user(cls.env, login="np01_raghad", groups=cls.groups, company_ids=[(6, 0, [cls.uae.id])],
                                   company_id=cls.uae.id)
        cls.ebrahim = new_test_user(cls.env, login="np01_ebrahim", groups=cls.groups,
                                    company_ids=[(6, 0, [cls.other.id])], company_id=cls.other.id)
        cls.plain = new_test_user(cls.env, login="np01_plain", groups="base.group_user",
                                  company_ids=[(6, 0, [cls.uae.id])], company_id=cls.uae.id)
        cls.Notice = cls.env["nudge.compliance.notice"].with_context(nudge_keep_transaction=True)

    def told(self, company=None):
        domain = [("company_id", "=", company.id)] if company else []
        return {(n.company_id, n.check_key): n.threshold for n in self.env["nudge.compliance.notice"].search(domain)}

    def test_a_deadline_is_told_once_at_its_threshold(self):
        with patch(NOTIFY) as notify:
            self.Notice._cron_deadlines(today=date(2026, 10, 22))
            first = self.told(self.uae)
            self.assertEqual(first[(self.uae, "einvoicing_asp")], 30)  # 8 days left
            self.assertEqual(first[(self.uae, "einvoicing_large")], 90)  # 71 days left
            self.assertNotIn((self.uae, "einvoicing_others"), first)  # 252 days left
            count = notify.call_count
            self.assertTrue(count)
            self.Notice._cron_deadlines(today=date(2026, 10, 22))
            self.Notice._cron_deadlines(today=date(2026, 10, 22))
            self.assertEqual(self.told(self.uae), first, "the same threshold is never told twice")
            self.assertEqual(notify.call_count, count)
            self.Notice._cron_deadlines(today=date(2026, 10, 24))  # 6 days to the 30th
            self.assertEqual(self.told(self.uae)[(self.uae, "einvoicing_asp")], 7)
            self.assertEqual(self.env["nudge.compliance.notice"].search_count(
                [("company_id", "=", self.uae.id), ("check_key", "=", "einvoicing_asp")]), 2)

    def test_a_deadline_that_moved_is_told_again_for_its_new_date(self):
        """Review (functional 15): a notice told for the old date silenced the new one"""
        Notice = self.env["nudge.compliance.notice"]
        domain = [("company_id", "=", self.uae.id), ("check_key", "=", "einvoicing_asp")]
        with patch(NOTIFY) as notify:
            self.Notice._cron_deadlines(today=date(2026, 10, 22))
            old = Notice.search(domain)
            self.assertEqual(len(old), 1)
            # as if it had been told at 7 days for the date before the pack moved it
            old.write({"deadline": date(2026, 7, 31), "threshold": 7})
            count = notify.call_count
            self.Notice._cron_deadlines(today=date(2026, 10, 22))
            self.assertEqual(Notice.search_count(domain), 2)
            self.assertEqual(sorted(Notice.search(domain).mapped("deadline")), [date(2026, 7, 31), date(2026, 10, 30)])
            self.assertGreater(notify.call_count, count)

    def test_a_missed_cron_tells_only_the_tightest_threshold_and_a_past_date_nothing(self):
        with patch(NOTIFY):
            self.Notice._cron_deadlines(today=date(2026, 10, 26))  # 4 days left
            rows = self.env["nudge.compliance.notice"].search(
                [("company_id", "=", self.uae.id), ("check_key", "=", "einvoicing_asp")])
            self.assertEqual(rows.mapped("threshold"), [7])
            self.Notice._cron_deadlines(today=date(2026, 11, 2))  # past
            self.assertEqual(self.env["nudge.compliance.notice"].search_count(
                [("company_id", "=", self.uae.id), ("check_key", "=", "einvoicing_asp")]), 1)

    def test_each_company_has_its_own_notices_and_only_its_own_managers_are_told(self):
        told = []
        with patch(NOTIFY, side_effect=lambda user, kind, message: told.append((user, message))):
            self.Notice._cron_deadlines(today=date(2026, 10, 22))
        self.assertIn((self.uae, "einvoicing_asp"), self.told())
        self.assertIn((self.other, "einvoicing_asp"), self.told())
        self.assertFalse([key for key in self.told() if key[0] == self.egypt], "Egypt has no dated check")
        users = {user for user, _message in told}
        self.assertIn(self.raghad, users)
        self.assertIn(self.ebrahim, users)
        self.assertNotIn(self.plain, users, "a user who is not an accounting manager is not told")
        self.assertTrue(any("2026-10-30" in message["message"] for user, message in told if user == self.ebrahim))
        raghad_count = len([1 for user, _message in told if user == self.raghad])
        self.assertEqual(raghad_count, 2, "one message for each notice of the company, not of the other")

    def test_the_deadline_notices_are_read_only_for_managers(self):
        manager = new_test_user(self.env, login="np01_nm", groups="nudge.group_nudge_manager")
        with patch(NOTIFY):
            self.Notice._cron_deadlines(today=date(2026, 10, 22))
        Notice = self.env["nudge.compliance.notice"].with_user(manager)
        rows = Notice.search([])
        self.assertTrue(rows)
        with self.assertRaises(AccessError):
            rows[:1].write({"threshold": 7})
        with self.assertRaises(AccessError):
            Notice.create({"company_id": self.uae.id, "pack": "ae", "check_key": "x", "threshold": 7,
                           "deadline": "2027-01-01"})


@tagged("post_install", "-at_install")
class TestSignedFeed(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private = Ed25519PrivateKey.generate()  # in memory only
        cls.public = public_text(cls.private)
        cls.admin = new_test_user(cls.env, login="np08_admin", groups="base.group_system,nudge.group_nudge_manager")
        cls.manager = new_test_user(cls.env, login="np08_manager", groups="nudge.group_nudge_manager")
        cls.Update = cls.env["nudge.compliance.update"]
        pack = bundled("ae")
        for entry in pack["checks"]:
            if entry["key"] == "einvoicing_large":
                entry["deadline"] = "2027-02-01"
        cls.new_ae = pack

    def signed(self, packs=None, key=None):
        payload = json.dumps({"issued": "2026-11-02", "packs": packs or [self.new_ae]}, sort_keys=True)
        return compliance_feed.sign(payload, key or self.private).encode()

    def feed(self, raw=None):
        self.env["ir.config_parameter"].sudo().set_param("nudge.compliance_feed_url", "https://feed.example.com/p.json")
        self.env["ir.config_parameter"].sudo().set_param("nudge.compliance_feed_key", self.public)
        return patch(DOWNLOAD, return_value=raw or self.signed())

    # ----- signature
    def test_a_valid_signature_is_accepted_with_the_pem_or_the_raw_key(self):
        issued, packs = compliance_feed.verify(self.signed(), self.public)
        self.assertEqual((issued, packs[0]["id"]), ("2026-11-02", "ae"))
        raw = self.private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        compliance_feed.verify(self.signed(), base64.b64encode(raw).decode())

    def test_another_key_a_tampered_payload_and_garbage_are_refused(self):
        stranger = Ed25519PrivateKey.generate()
        with self.assertRaisesRegex(compliance_feed.FeedError, "signature"):
            compliance_feed.verify(self.signed(key=stranger), self.public)
        envelope = json.loads(self.signed())
        envelope["payload"] = envelope["payload"].replace("2026-11-02", "2026-11-03")
        with self.assertRaisesRegex(compliance_feed.FeedError, "signature"):
            compliance_feed.verify(json.dumps(envelope).encode(), self.public)
        for raw in (b"not json", b"{}", json.dumps({"format": 1, "payload": 5, "signature": "AA=="}).encode(),
                    json.dumps({"format": 1, "payload": "{}", "signature": "***"}).encode()):
            with self.assertRaises(compliance_feed.FeedError):
                compliance_feed.verify(raw, self.public)
        with self.assertRaises(compliance_feed.FeedError):
            compliance_feed.verify(self.signed(), "")
        with self.assertRaises(compliance_feed.FeedError):
            compliance_feed.verify(self.signed(), "not a key")
        with self.assertRaisesRegex(compliance_feed.FeedError, "no pack"):
            compliance_feed.verify(compliance_feed.sign('{"packs": []}', self.private).encode(), self.public)

    def test_the_feed_is_off_by_default(self):
        Parameter = self.env["ir.config_parameter"].sudo()
        self.assertFalse(Parameter.get_param("nudge.compliance_feed_url"))
        self.assertFalse(Parameter.get_param("nudge.compliance_feed_key"))
        with patch(DOWNLOAD) as download:
            self.assertFalse(self.Update._fetch(quiet=True))
            self.Update._cron_fetch()
            with self.assertRaises(UserError):
                self.Update._fetch()
            download.assert_not_called()

    def test_use_the_nudge_feed_fills_the_address_and_the_publisher_key_without_fetching(self):
        Parameter = self.env["ir.config_parameter"].sudo()
        publisher = self.env.ref("nudge.recipe_publisher_nudge")
        with patch(DOWNLOAD) as download:
            self.env["res.config.settings"].with_user(self.admin).action_nudge_compliance_feed_use_nudge()
            download.assert_not_called()
        self.assertEqual(Parameter.get_param("nudge.compliance_feed_url"),
                         "https://znad-odoo-dev.github.io/nudge/compliance/feed.json")
        self.assertEqual(Parameter.get_param("nudge.compliance_feed_key"), publisher.public_key.strip())

    def test_use_the_nudge_feed_is_refused_to_a_manager_who_is_not_a_system_administrator(self):
        Parameter = self.env["ir.config_parameter"].sudo()
        with self.assertRaises(AccessError):
            self.env["res.config.settings"].with_user(self.manager).action_nudge_compliance_feed_use_nudge()
        self.assertFalse(Parameter.get_param("nudge.compliance_feed_url"))
        self.assertFalse(Parameter.get_param("nudge.compliance_feed_key"))

    def test_the_check_says_the_signature_was_verified_and_how_many_countries_the_feed_covers(self):
        self.assertEqual(self.Update._status_text(), "")
        extra = {"id": "xx", "countries": ["EG", "SA"], "checks": [{
            "key": "one", "check": "note", "severity": "info", "title": "T", "explanation": "E"}]}
        with self.feed(self.signed(packs=[self.new_ae, extra])):
            action = self.Update.with_user(self.admin).action_check_now()
        self.assertIn("Signature verified", action["name"])
        self.assertIn("3 countries", action["name"])  # AE from the first pack, EG and SA from the second: read, not fixed
        self.assertIn("2 packs would change", action["name"])
        # The title the list opens with: the web client reads display_name before name (bug 22: it said "Compliance
        # Pack Updates" while the name said what the check found)
        self.assertEqual(action.get("display_name") or action["name"], action["name"])
        settings = self.env["res.config.settings"].new({})
        self.assertEqual(settings.nudge_compliance_feed_status, self.Update._status_text())

    def test_a_refused_feed_leaves_no_verified_status(self):
        with self.feed(self.signed(key=Ed25519PrivateKey.generate())):
            with self.assertRaises(UserError):
                self.Update._fetch()
        self.assertEqual(self.Update._status_text(), "")

    def test_a_bad_signature_changes_nothing(self):
        with self.feed(self.signed(key=Ed25519PrivateKey.generate())):
            with self.assertRaises(UserError):
                self.Update._fetch()
        self.assertFalse(self.Update.search([]))

    # ----- fetch, diff, approval
    def test_a_fetch_makes_a_pending_update_with_the_difference_and_changes_nothing_yet(self):
        with self.feed():
            made = self.Update._fetch()
        self.assertEqual((made.pack_id, made.state), ("ae", "pending"))
        self.assertIn("~ einvoicing_large (changed): deadline 2027-01-01 -> 2027-02-01", made.diff)
        self.assertEqual(compliance.load_all(self.env)[0]["ae"].checks[-2].deadline, "2027-01-01",
                         "the bundled pack is still in force")
        with self.feed():
            self.assertFalse(self.Update._fetch(), "the same feed is not made pending twice")

    def test_a_pack_that_does_not_validate_is_left_out_and_a_new_pack_is_a_new_pack(self):
        broken = bundled("qa")
        broken["checks"][0]["severity"] = "dire"
        extra = {"id": "xx", "countries": ["AE"], "checks": [{
            "key": "one", "check": "note", "severity": "info", "title": "T", "explanation": "E"}]}
        with self.feed(self.signed(packs=[broken, extra])):
            made = self.Update._fetch()
        self.assertEqual(made.mapped("pack_id"), ["xx"])
        self.assertIn("+ one (added)", made.diff)

    def test_only_a_system_administrator_approves(self):
        with self.feed():
            update = self.Update._fetch()
        with self.assertRaises(AccessError):
            update.with_user(self.manager).action_approve()
        with self.assertRaises(AccessError):
            update.with_user(self.manager).write({"state": "applied"})
        with self.assertRaises(AccessError):
            self.Update.with_user(self.manager).action_check_now()
        with self.assertRaises(AccessError):
            update.with_user(self.manager).action_reject()
        self.assertEqual(update.state, "pending")
        update.with_user(self.admin).action_approve()
        self.assertEqual((update.state, update.approved_by), ("applied", self.admin))

    def test_an_approved_pack_replaces_the_bundled_one_and_a_rollback_brings_it_back(self):
        with self.feed():
            update = self.Update._fetch()
        update.with_user(self.admin).action_approve()
        pack = compliance.load_all(self.env)[0]["ae"]
        self.assertEqual({e.key: e.deadline for e in pack.checks}["einvoicing_large"], "2027-02-01")
        with self.assertRaises(AccessError):
            update.with_user(self.manager).action_rollback()
        update.with_user(self.admin).action_rollback()
        pack = compliance.load_all(self.env)[0]["ae"]
        self.assertEqual({e.key: e.deadline for e in pack.checks}["einvoicing_large"], "2027-01-01")

    def test_a_declined_update_is_not_proposed_again_and_a_newer_one_supersedes_a_pending_one(self):
        with self.feed():
            first = self.Update._fetch()
        first.with_user(self.admin).action_reject()
        with self.feed():
            self.assertFalse(self.Update._fetch())
        later = json.loads(json.dumps(self.new_ae))
        later["checks"][0]["severity"] = "critical" if later["checks"][0]["severity"] != "critical" else "warning"
        with self.feed(self.signed(packs=[later])):
            second = self.Update._fetch()
        later2 = json.loads(json.dumps(later))
        later2["checks"][0]["severity"] = "info"
        with self.feed(self.signed(packs=[later2])):
            third = self.Update._fetch()
        self.assertEqual((second.state, third.state), ("superseded", "pending"))

    def test_a_stored_pack_that_no_longer_loads_falls_back_to_the_bundled_one(self):
        with self.feed():
            update = self.Update._fetch()
        update.with_user(self.admin).action_approve()
        update.sudo().payload = '{"id": "ae"}'
        packs, _refused = compliance.load_all(self.env)
        self.assertTrue(packs["ae"].checks)

    def test_the_feed_is_not_read_in_private_mode(self):
        with self.feed() as download, patch.object(
                type(self.env["nudge.provider"]), "_is_private_mode", return_value=True):
            self.assertFalse(self.Update._fetch(quiet=True))
            with self.assertRaises(UserError):
                self.Update._fetch()
            download.assert_not_called()

    def test_the_signing_script_signs_what_the_server_verifies(self):
        import importlib.util
        import tempfile
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "sign_pack_feed.py")
        spec = importlib.util.spec_from_file_location("sign_pack_feed", path)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        with tempfile.TemporaryDirectory() as folder:
            key_path = os.path.join(folder, "k.pem")
            with open(key_path, "wb") as handle:  # a throwaway key, in a temporary folder
                handle.write(self.private.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            source = os.path.join(folder, "feed.json")
            with open(source, "w", encoding="utf-8") as handle:
                json.dump({"issued": "2026-11-02", "packs": [self.new_ae]}, handle)
            out = os.path.join(folder, "signed.json")
            script.main(["--key", key_path, "--payload", source, "--out", out])
            with open(out, "rb") as handle:
                issued, packs = compliance_feed.verify(handle.read(), self.public)
        self.assertEqual((issued, packs[0]["id"]), ("2026-11-02", "ae"))
