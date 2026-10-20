import json
import os
import tempfile
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.tools import compliance

BUNDLED = {"ae", "sa", "qa", "kw", "bh", "om", "eg"}


def pack(**changes):
    data = {
        "id": "test", "countries": ["AE"],
        "checks": [{"key": "one", "check": "company_currency", "severity": "info", "params": {"code": "AED"},
                    "title": "A title", "explanation": "An explanation"}],
    }
    data.update(changes)
    return data


@tagged("post_install", "-at_install")
class TestCompliancePacks(TransactionCase):
    """The packs are data files: what is wrong in one is refused when it is loaded, in words."""

    def test_the_seven_packs_of_the_package_load(self):
        packs, refused = compliance.load_all(self.env)
        self.assertEqual(refused, [])
        self.assertTrue(BUNDLED <= set(packs))
        for pack_ in packs.values():
            self.assertTrue(pack_.checks, pack_.id)
            for entry in pack_.checks:
                self.assertTrue(entry.title.get("en") and entry.explanation.get("en"), entry.key)
                if pack_.id in BUNDLED:
                    self.assertTrue(entry.title.get("ar") and entry.explanation.get("ar"), (pack_.id, entry.key))

    def test_a_check_key_that_does_not_exist_is_refused_at_load_with_a_clear_error(self):
        data = pack()
        data["checks"][0]["check"] = "no_such_check"
        with self.assertRaises(compliance.PackError) as caught:
            compliance.load_pack(data, "sample.json")
        message = str(caught.exception)
        self.assertIn("sample.json", message)
        self.assertIn("'one'", message)
        self.assertIn("unknown check 'no_such_check'", message)
        self.assertIn("company_field", message, "the kinds that exist are listed")

    def test_the_same_in_a_file_a_partner_drops_in_a_module(self):
        with tempfile.TemporaryDirectory() as folder:
            bad = os.path.join(folder, "bad.json")
            good = os.path.join(folder, "good.json")
            data = pack(id="bad")
            data["checks"][0]["check"] = "made_up"
            with open(bad, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            with open(good, "w", encoding="utf-8") as handle:
                json.dump(pack(id="partner_pack", countries=["JO"]), handle)
            with self.assertRaises(compliance.PackError) as caught:
                compliance.load_file(bad)
            self.assertIn("bad.json", str(caught.exception))
            with patch.object(compliance, "_files", lambda env: [bad, good]):
                packs, refused = compliance.load_all(self.env)
            self.assertEqual(set(packs), {"partner_pack"})
            self.assertEqual(len(refused), 1)
            self.assertIn("unknown check 'made_up'", refused[0][1])

    def test_other_mistakes_are_named_too(self):
        cases = [
            (dict(pack(), id="Not An Id"), "\"id\""),
            (dict(pack(), countries=["Dubai"]), "two-letter"),
            (dict(pack(), checks=[]), "no check"),
        ]
        for data, fragment in cases:
            with self.assertRaises(compliance.PackError) as caught:
                compliance.load_pack(data, "x.json")
            self.assertIn(fragment, str(caught.exception))
        for change, fragment in (
                ({"severity": "fatal"}, "severity 'fatal'"),
                ({"params": {"code": "AED", "colour": "red"}}, "takes no parameter 'colour'"),
                ({"params": {}}, "needs the parameter code"),
                ({"params": {"code": 5}}, "is a str"),
                ({"title": 12}, "title")):
            data = pack()
            data["checks"][0].update(change)
            with self.assertRaises(compliance.PackError) as caught:
                compliance.load_pack(data, "x.json")
            self.assertIn(fragment, str(caught.exception))
        twice = pack()
        twice["checks"].append(dict(twice["checks"][0]))
        with self.assertRaises(compliance.PackError) as caught:
            compliance.load_pack(twice, "x.json")
        self.assertIn("used twice", str(caught.exception))
        stalling = pack()
        stalling["checks"][0].update(check="company_field", params={"fields": ["vat"], "pattern": "(a+)+$"})
        with self.assertRaises(compliance.PackError):
            compliance.load_pack(stalling, "x.json")
        bad_pattern = pack()
        bad_pattern["checks"][0].update(check="company_field", params={"fields": ["vat"], "pattern": "("})
        with self.assertRaises(compliance.PackError):
            compliance.load_pack(bad_pattern, "x.json")

    def test_a_pack_is_found_by_the_country_of_the_company(self):
        company = self.env.company
        for code, expected in (("AE", "ae"), ("SA", "sa"), ("QA", "qa"), ("KW", "kw"), ("BH", "bh"), ("OM", "om"),
                               ("EG", "eg")):
            company.country_id = self.env["res.country"].search([("code", "=", code)])
            self.assertEqual([found.id for found in compliance.packs_for(self.env, company)], [expected], code)
        company.country_id = self.env["res.country"].search([("code", "=", "FR")])
        self.assertEqual(compliance.packs_for(self.env, company), [])
        company.country_id = False
        self.assertEqual(compliance.packs_for(self.env, company), [])


@tagged("post_install", "-at_install")
class TestPackFolders(TransactionCase):

    def test_the_folders_of_the_packs_are_looked_for_once_per_registry(self):
        Module = self.env["ir.module.module"]
        first = Module._nudge_pack_folders()
        with patch("odoo.addons.nudge.models.ir_module_module.get_module_path") as probe:
            self.assertEqual(Module._nudge_pack_folders(), first)
            probe.assert_not_called()


@tagged("post_install", "-at_install")
class TestComplianceRun(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login="nudge_gcc_manager",
                                    groups="nudge.group_nudge_manager,base.group_partner_manager")
        cls.user = new_test_user(cls.env, login="nudge_gcc_user", groups="nudge.group_nudge_user")
        cls.country = lambda cls_, code: cls_.env["res.country"].search([("code", "=", code)])

    def run_pack(self, code, pack_id, **company):
        self.env.company.write(dict(country_id=self.country(code).id, **company))
        Report = self.env["nudge.health.report"].with_user(self.manager)
        return Report.run_check(pack=pack_id)

    def keys(self, report):
        return {finding.key: finding for finding in report.finding_ids}

    def test_a_uae_company_without_a_trn_has_the_finding_and_it_goes_when_the_trn_is_entered(self):
        report = self.run_pack("AE", "ae", vat=False)
        self.assertEqual(report.pack, "ae")
        found = self.keys(report)
        self.assertIn("ae.company_trn", found)
        finding = found["ae.company_trn"]
        self.assertEqual(finding.severity, "critical")
        self.assertIn("Tax Registration Number", finding.message)
        self.assertIn("15-digit", finding.detail)
        self.assertIn("Reference: Executive Regulation", finding.detail, "the official reference is in the explanation")
        self.assertEqual((finding.model_name, json.loads(finding.ids_json)), ("res.company", [self.env.company.id]))
        # a TRN of 14 digits is no TRN
        report = self.run_pack("AE", "ae", vat="10012345670000")
        self.assertIn("ae.company_trn", self.keys(report))
        report = self.run_pack("AE", "ae", vat="100123456700003")
        self.assertNotIn("ae.company_trn", self.keys(report))
        self.assertIn("Tax Registration Number", report.resolved_text)
        self.assertTrue(report.previous_id.pack == "ae")

    def test_the_trn_is_asked_of_the_customers_too(self):
        partner = self.env["res.partner"].create({
            "name": "Raghad Trading LLC", "is_company": True, "country_id": self.country("AE").id, "vat": "123",
            **({"customer_rank": 1} if "customer_rank" in self.env["res.partner"]._fields else {})})
        report = self.run_pack("AE", "ae", vat="100123456700003")
        finding = self.keys(report).get("ae.partner_trn")
        self.assertTrue(finding)
        self.assertIn(partner.id, json.loads(finding.ids_json))
        partner.vat = "100123456700004"
        self.assertNotIn(partner.id, json.loads(self.keys(self.run_pack("AE", "ae")).get(
            "ae.partner_trn", self.env["nudge.health.finding"]).ids_json or "[]"))

    def test_a_saudi_company_without_the_zatca_module_is_told_the_module_is_missing(self):
        if self.env["ir.module.module"].search_count([("name", "=", "l10n_sa_edi"), ("state", "=", "installed")]):
            self.skipTest("l10n_sa_edi is installed here")
        report = self.run_pack("SA", "sa")
        found = self.keys(report)
        self.assertIn("sa.edi_module|module", found)
        finding = found["sa.edi_module|module"]
        self.assertEqual(finding.severity, "info", "what cannot be looked at is not a fault")
        self.assertIn("cannot be checked", finding.message)
        self.assertIn("l10n_sa_edi", finding.message)
        self.assertIn("l10n_sa_edi", finding.detail)
        self.assertIn("sa.qr_code|module", found)
        self.assertIn("sa.hijri_date", found)

    def test_vat_is_only_asked_where_it_is_in_force(self):
        for code, pack_id in (("QA", "qa"), ("KW", "kw")):
            keys = set(self.keys(self.run_pack(code, pack_id, company_registry=False)))
            self.assertIn(f"{pack_id}.vat_status", keys)
            self.assertFalse([key for key in keys if key.startswith(f"{pack_id}.vat_tax")], pack_id)
            self.assertIn(f"{pack_id}.company_registry", keys, "the registration number is asked in each")
        for code, pack_id, rate in (("BH", "bh", 10), ("OM", "om", 5), ("EG", "eg", 14)):
            self.env.company.write({"country_id": self.country(code).id, "company_registry": False})
            taxes = self.env["account.tax"].search_count([("amount", "=", rate), ("type_tax_use", "=", "sale")])                 if "account.tax" in self.env else 0
            keys = set(self.keys(self.run_pack(code, pack_id, company_registry=False)))
            self.assertIn(f"{pack_id}.company_registry", keys)
            if not taxes:
                self.assertTrue([key for key in keys if key.startswith(f"{pack_id}.vat_tax")], pack_id)

    def test_egypt_wants_its_e_invoicing_module(self):
        if self.env["ir.module.module"].search_count(
                [("name", "in", ("l10n_eg_edi_eta", "l10n_eg_edi")), ("state", "=", "installed")]):
            self.skipTest("an Egyptian e-invoicing module is installed here")
        found = self.keys(self.run_pack("EG", "eg"))
        self.assertIn("eg.edi_module|module", found)

    def test_a_report_of_a_checklist_is_kept_apart_from_the_general_check(self):
        general = self.env["nudge.health.report"].with_user(self.manager).run_check()
        self.assertFalse(general.pack)
        self.assertNotIn("compliance", {finding.check for finding in general.finding_ids})
        first = self.run_pack("AE", "ae")
        second = self.run_pack("AE", "ae")
        self.assertEqual(second.previous_id, first)
        self.assertNotEqual(first.previous_id, general)
        self.assertEqual(self.env["nudge.health.report"].with_user(self.manager).run_check().previous_id, general)

    def test_the_list_of_checklists_runs_one_pack_at_a_time_and_is_for_managers(self):
        self.env.company.country_id = self.country("AE")
        Pack = self.env["nudge.compliance.pack"]
        action = Pack.with_user(self.manager).action_open()
        rows = Pack.with_user(self.manager).search(action["domain"])
        self.assertEqual(rows.mapped("pack"), ["ae"])
        self.assertEqual(rows.check_count, len(compliance.load_all(self.env)[0]["ae"].checks))
        opened = rows.with_user(self.manager).action_run()
        report = self.env["nudge.health.report"].browse(opened["res_id"])
        self.assertEqual(report.pack, "ae")
        self.assertTrue(report.finding_ids)
        with self.assertRaises(AccessError):
            Pack.with_user(self.user).action_open()
        self.env.company.country_id = False
        self.assertIn("set the country", Pack.with_user(self.manager).action_open()["name"])

    def test_a_pack_that_failed_to_load_is_shown_with_its_reason(self):
        with tempfile.TemporaryDirectory() as folder:
            bad = os.path.join(folder, "broken.json")
            data = pack(id="broken")
            data["checks"][0]["check"] = "made_up"
            with open(bad, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            with patch.object(compliance, "_files", lambda env: [bad]):
                action = self.env["nudge.compliance.pack"].with_user(self.manager).action_open()
                rows = self.env["nudge.compliance.pack"].with_user(self.manager).search(action["domain"])
                self.assertIn("unknown check 'made_up'", rows.error)
                with self.assertRaises(UserError):
                    rows.action_run()

    def test_nothing_is_changed_by_a_checklist(self):
        self.env.company.write({"country_id": self.country("AE").id, "vat": "123"})
        self.env.flush_all()
        self.env.cr.execute("SELECT write_date FROM res_company WHERE id = %s", [self.env.company.id])
        written = self.env.cr.fetchone()
        products = self.env["product.template"].search_count([]) if "product.template" in self.env else 0
        self.env["nudge.health.report"].with_user(self.manager).run_check(pack="ae")
        self.env.cr.execute("SELECT write_date FROM res_company WHERE id = %s", [self.env.company.id])
        self.assertEqual(self.env.cr.fetchone(), written)
        self.assertEqual(self.env["product.template"].search_count([]) if "product.template" in self.env else 0, products)
        self.assertEqual(self.env.company.vat, "123")

    def test_the_text_is_told_in_the_language_of_the_reader(self):
        report = self.run_pack("AE", "ae", vat=False)
        arabic = self.env["res.lang"].with_context(active_test=False).search([("code", "=", "ar_001")])
        arabic.active = True
        finding = self.keys(report)["ae.company_trn"].with_context(lang="ar_001")
        self.assertIn("رقم التسجيل الضريبي", finding.message)
        self.assertIn("اللائحة التنفيذية", finding.detail)
        self.assertEqual(tools.compliance.pick({"en": "x"}, self.env(context={"lang": "ar_001"})), "x")
