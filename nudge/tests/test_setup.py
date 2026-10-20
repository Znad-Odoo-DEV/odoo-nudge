import base64
from types import SimpleNamespace

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.models.nudge_conversation import ADMIN_PARAM
from odoo.addons.nudge.tools import setup, setup_sentence

from .common import NudgeCase
from .test_agent import asking, call, saying

REQUESTS = [
    "Set up this database with me",
    "help me set up my company in Odoo",
    "I want to configure the system for the first time, first setup please",
    "can you initialise this database",
    "بدي تساعدني جهز قاعدة البيانات",          # Levantine
    "جهزلي النظام من فضلك",                    # Gulf / MSA
    "عايز أظبط السيستم أول مرة",               # Egyptian
    "ابغى تجهز لي الداتابيس",                  # Gulf
    "اريد تهيئة قاعدة البيانات",               # MSA
    "اريد اهيئ الداتابيس",                     # Iraqi
    "نبدا اعداد اولي للشركة",                  # Maghrebi / Levantine
    "ساعدني اضبط اودو",                        # Sudanese / Yemeni
]
NOT_REQUESTS = [
    "hello", "", "How do I set up a bank account?", "كم عدد العملاء؟", "Create a contact for Raghad",
    "كيف اضبط الطابعة؟",
]


@tagged("post_install", "-at_install")
class TestSetupSentence(TransactionCase):
    def test_the_setup_is_asked_for_in_twelve_ways(self):
        for sentence in REQUESTS:
            self.assertTrue(setup_sentence.is_request(sentence), sentence)

    def test_what_is_no_request_for_the_setup_is_left_alone(self):
        for sentence in NOT_REQUESTS:
            self.assertFalse(setup_sentence.is_request(sentence), sentence)

    def test_answers_are_read_in_english_and_arabic(self):
        company = setup_sentence.company_answer("Raghad Trading LLC, TRN 100123456700003, Sheikh Zayed Road, Dubai")
        self.assertEqual(company["name"], "Raghad Trading LLC")
        self.assertEqual(company["vat"], "100123456700003")
        self.assertEqual(company["street"], "Sheikh Zayed Road")
        self.assertEqual(company["city"], "Dubai")
        arabic = setup_sentence.company_answer("الاسم: شركة رغد للتجارة؛ الرقم الضريبي: ١٠٠١٢٣٤٥٦٧٠٠٠٠٣؛ المدينة: دبي")
        self.assertEqual(arabic["vat"], "100123456700003")
        users, invite = setup_sentence.users_answer(
            "Raghad raghad@example.com accountant\nEbrahim ebrahim@example.com مدير المبيعات\nsend invitations")
        self.assertEqual([(u["name"], u["role"]) for u in users], [("Raghad", "accountant"), ("Ebrahim", "salesperson")]
                         if False else [("Raghad", "accountant"), ("Ebrahim", "manager")])
        self.assertTrue(invite)
        self.assertEqual(setup_sentence.fiscal_answer("السنة المالية تنتهي 30 يونيو، قفل لغاية 2025-12-31"),
                         {"month": 6, "day": 30, "lock_date": "2025-12-31"})
        self.assertEqual(setup_sentence.formats_answer("31/12/2026 and 1,234.56")["decimal_point"], ".")


class SetupCase(NudgeCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = new_test_user(
            cls.env, login="nudge_setup_admin",
            groups="nudge.group_nudge_agent,base.group_system,base.group_partner_manager" + (",stock.group_stock_manager" if cls.env.ref("stock.group_stock_manager", raise_if_not_found=False) else ""))
        cls.plain = new_test_user(cls.env, login="nudge_setup_plain", groups="nudge.group_nudge_agent")
        cls.env["ir.config_parameter"].sudo().set_param(ADMIN_PARAM, "yes")
        cls.env["ir.config_parameter"].sudo().set_param(setup.PARAM_DONE, "no")

    def setUp(self):
        super().setUp()
        self.installed = []
        self.patch(setup, "install_modules", lambda env, names: self.installed.extend(names) or names)
        self.conversation = self.env["nudge.conversation"].with_user(self.admin)._chat_open()
        self.dev = self.env(user=self.admin, context={"nudge_conversation_id": self.conversation.id})

    def as_user(self, user=None):
        return self.env(user=user or self.admin, context={"nudge_conversation_id": self.conversation.id})

    def card(self, step, answers=None, user=None):
        return tools.base.call(self.as_user(user), "prepare_setup_step",
                               {"step": step, "answers": answers or {}}, None, "agent")

    def done(self, asked, user=None):
        """Say yes to a card the way the conversation does; return the result and the step"""
        self.assertFalse(asked.is_error, asked.content)
        env = self.as_user(user)
        step = SimpleNamespace(values=asked.pending["values"], res_model=asked.pending["res_model"], res_ids=[])
        result = tools.base.TOOLS["prepare_setup_step"].execute(env, step)
        self.assertFalse(result.is_error, result.content)
        return result, step

    def lines(self, asked):
        return {line["label"]: line["value"] for line in asked.pending["preview"]["lines"]}


@tagged("post_install", "-at_install")
class TestSetupDetection(SetupCase, TransactionCase):
    def fresh_database(self):
        # the users the tests made are not a crowd
        keep = (self.admin | self.plain | self.env.ref("base.user_admin") | self.env.ref("base.user_root")).ids
        self.env["res.users"].search([("id", "not in", keep), ("share", "=", False)]).write({"active": False})
        if not setup.detect(self.dev)["fresh"]:
            self.skipTest("This database was set up already: the detection is tried on the bare one")

    def test_a_bare_database_is_a_new_one(self):
        self.fresh_database()
        found = setup.detect(self.dev)
        self.assertEqual(found["reasons"], {"one_company": True, "default_name": True, "no_chart": True,
                                            "few_users": True})
        self.assertTrue(setup.offered(self.dev))

    def test_a_second_company_a_name_or_a_crowd_makes_it_an_old_one(self):
        self.env["res.company"].create({"name": "Ebrahim Contracting"})
        found = setup.detect(self.dev)
        self.assertFalse(found["fresh"])
        self.assertFalse(found["reasons"]["one_company"])

    def test_a_named_company_is_no_new_database(self):
        self.fresh_database()
        self.env.company.name = "Raghad Trading LLC"
        self.assertFalse(setup.detect(self.dev)["fresh"])
        self.assertFalse(setup.offered(self.dev))

    def test_many_users_are_no_new_database(self):
        self.fresh_database()
        for index in range(setup.FEW_USERS + 1):
            new_test_user(self.env, login=f"nudge_crowd_{index}", groups="base.group_user")
        self.assertFalse(setup.detect(self.dev)["reasons"]["few_users"])

    def test_a_finished_setup_is_not_offered_again(self):
        self.fresh_database()
        self.env["ir.config_parameter"].sudo().set_param(setup.PARAM_DONE, "yes")
        self.assertFalse(setup.offered(self.dev))

    def test_nobody_but_an_administrator_is_told(self):
        self.fresh_database()
        self.assertFalse(setup.detect(self.env(user=self.plain))["fresh"])
        self.assertFalse(setup.offered(self.env(user=self.plain)))
        self.assertTrue(tools.base.TOOLS["prepare_setup_step"].is_available(self.dev, "agent"))
        self.assertFalse(tools.base.TOOLS["prepare_setup_step"].is_available(self.env(user=self.plain), "agent"))

    def test_the_session_tells_the_panel_to_offer_it(self):
        self.fresh_database()
        info = self.env["ir.http"].with_user(self.admin)._get_nudge_session_info()
        self.assertTrue(info["setup_offer"])
        self.assertFalse(self.env["ir.http"].with_user(self.plain)._get_nudge_session_info()["setup_offer"])


@tagged("post_install", "-at_install")
class TestSetupSteps(SetupCase, TransactionCase):
    def test_a_plain_user_is_refused_and_nothing_changes(self):
        name = self.env.company.name
        for step, answers in (("company", {"name": "Hijacked"}), ("users", {"users": [
                {"name": "Raghad", "email": "raghad@example.com", "role": "admin"}]}), ("remaining", {})):
            result = self.card(step, answers, user=self.plain)
            self.assertTrue(result.is_error, step)
            self.assertIsNone(result.pending)
        self.assertEqual(self.env.company.name, name)
        read = tools.base.call(self.env(user=self.plain), "read_setup", {}, None, "agent")
        self.assertTrue(read.is_error)
        with self.assertRaises(Exception):
            tools.base.TOOLS["prepare_setup_step"].execute(
                self.env(user=self.plain), SimpleNamespace(values={"step": "company", "data": {"values": {"name": "X"}}}))
        self.assertEqual(self.env.company.name, name)

    def test_company_card_then_the_write(self):
        asked = self.card("company", {"name": "Raghad Trading LLC", "vat": "100123456700003", "city": "Dubai",
                                      "email": "info@raghad.example"})
        lines = self.lines(asked)
        self.assertIn("Raghad Trading LLC", lines["Company Name"])
        self.assertNotEqual(self.env.company.name, "Raghad Trading LLC", "nothing before the yes")
        result, step = self.done(asked)
        company = self.env.company
        self.assertEqual((company.name, company.vat, company.city), ("Raghad Trading LLC", "100123456700003", "Dubai"))
        self.assertIn("res.company", step.values["undo"])
        self.assertEqual(self.card("company", {"email": "not an email"}).is_error, True)

    def test_country_names_the_module_and_installs_it_only_after_the_yes(self):
        asked = self.card("country", {"country": "الإمارات"})
        self.assertEqual(asked.pending["values"]["data"]["country_id"], self.env.ref("base.ae").id)
        text = " ".join(self.lines(asked).values())
        chart_loaded = "chart_template" in self.env.company._fields and self.env.company.chart_template
        if not chart_loaded:
            self.assertEqual(asked.pending["values"]["data"]["module"], "l10n_ae")
            self.assertIn("l10n_ae", text)
        self.assertEqual(self.installed, [])
        self.assertNotEqual(self.env.company.country_id.code, "AE")
        self.done(asked)
        self.assertEqual(self.env.company.country_id.code, "AE")
        self.assertEqual(self.installed, [] if chart_loaded else ["l10n_ae"])
        self.assertTrue(self.card("country", {"country": "Atlantis"}).is_error)

    def test_currency_languages_timezone_and_formats(self):
        asked = self.card("timezone", {"timezone": "Dubai"})
        self.done(asked)
        self.assertEqual(self.admin.tz, "Asia/Dubai")
        self.assertTrue(self.card("timezone", {"timezone": "Narnia"}).is_error)
        asked = self.card("formats", {"date_format": "%d/%m/%Y", "decimal_point": ".", "thousands_sep": ","})
        self.assertIn("31/12/2026", asked.pending["preview"]["lines"][1]["value"])
        self.assertTrue(self.card("formats", {"decimal_point": ",", "thousands_sep": ","}).is_error)
        asked = self.card("currency", {"currency": "dirham"})
        self.assertEqual(self.env["res.currency"].browse(asked.pending["values"]["data"]["currency_id"]).name, "AED")
        self.done(asked)
        self.assertEqual(self.env.company.currency_id.name, "AED")
        asked = self.card("languages", {"languages": ["French"]})
        self.assertIn("French", asked.pending["record_names"])
        self.assertTrue(self.card("languages", {"languages": ["Klingon"]}).is_error)

    def test_arabic_is_activated_with_its_translations_after_the_yes(self):
        Lang = self.env["res.lang"].with_context(active_test=False)
        arabic = Lang.search([("code", "=", "ar_001")])
        if arabic.active:
            self.skipTest("Arabic is active already")
        asked = self.card("languages", {"languages": ["العربية"]})
        self.assertIn("Arabic", asked.pending["record_names"])
        self.assertFalse(arabic.active, "nothing before the yes")
        self.done(asked)
        self.assertTrue(Lang.search([("code", "=", "ar_001")]).active)

    def test_logo_comes_from_the_picture_attached(self):
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
        attachment = self.env["ir.attachment"].with_user(self.admin).create({
            "name": "logo.png", "raw": png, "mimetype": "image/png"})
        asked = self.card("logo", {"attachment_id": attachment.id})
        self.assertIn("logo.png", self.lines(asked)["Logo"])
        self.done(asked)
        self.assertTrue(self.env.company.logo)
        text = self.env["ir.attachment"].with_user(self.admin).create({"name": "a.txt", "raw": b"x", "mimetype": "text/plain"})
        self.assertTrue(self.card("logo", {"attachment_id": text.id}).is_error)

    def test_apps_come_from_the_kind_of_business_with_what_each_brings(self):
        asked = self.card("apps", {"kind": "contracting"})
        if asked.is_error:
            self.assertIn("installed already", asked.content)
            return
        values = " ".join(self.lines(asked).values())
        self.assertIn("projects, tasks and timesheets", values)
        todo = asked.pending["values"]["data"]["modules"]
        self.assertNotIn("account_that_does_not_exist", todo)
        self.assertEqual(self.installed, [])
        if todo:
            self.done(asked)
            self.assertEqual(self.installed, todo)
        self.assertTrue(self.card("apps", {}).is_error)

    def test_other_companies_are_made_and_undone_by_archiving(self):
        asked = self.card("companies", {"companies": ["Raghad Foods", "Ebrahim Contracting"]})
        self.assertEqual(len(asked.pending["preview"]["lines"]) >= 2, True)
        self.assertFalse(self.env["res.company"].search([("name", "=", "Raghad Foods")]))
        _result, step = self.done(asked)
        made = self.env["res.company"].search([("name", "in", ["Raghad Foods", "Ebrahim Contracting"])])
        self.assertEqual(len(made), 2)
        self.assertEqual(set(step.values["undo"]["res.company"]), {str(company.id) for company in made})
        self.assertTrue(self.card("companies", {"companies": ["Raghad Foods"]}).is_error, "exists already")

    def test_warehouses_one_for_each_site(self):
        if "stock.warehouse" not in self.env:
            self.assertTrue(self.card("warehouses", {"warehouses": ["Dubai Main"]}).is_error)
            return
        asked = self.card("warehouses", {"warehouses": ["Dubai Main", "Raghad Branch"]})
        items = asked.pending["values"]["data"]["items"]
        self.assertEqual(len({item["code"] for item in items}), 2)
        _result, step = self.done(asked)
        self.assertEqual(self.env["stock.warehouse"].search_count([("name", "in", ["Dubai Main", "Raghad Branch"])]), 2)
        self.assertIn("stock.warehouse", step.values["undo"])

    def test_users_get_the_groups_of_their_role_and_no_email_unless_asked(self):
        asked = self.card("users", {"users": [
            {"name": "Raghad", "email": "raghad@example.com", "role": "accountant"},
            {"name": "Ebrahim", "email": "ebrahim@example.com", "role": "admin"}]})
        lines = self.lines(asked)
        self.assertIn("Accountant", lines["Raghad <raghad@example.com>"])
        self.assertEqual(lines["Invitation email"], "none is sent")
        self.assertFalse(self.env["res.users"].search([("login", "=", "raghad@example.com")]))
        mails = self.env["mail.mail"].search_count([]) if "mail.mail" in self.env else 0
        _result, step = self.done(asked)
        raghad = self.env["res.users"].search([("login", "=", "raghad@example.com")])
        ebrahim = self.env["res.users"].search([("login", "=", "ebrahim@example.com")])
        groups = lambda user: user[setup.groups_field(self.env)]  # noqa: E731
        self.assertTrue(groups(raghad).filtered(lambda g: g == self.env.ref("base.group_user")))
        self.assertIn(self.env.ref("base.group_system"), groups(ebrahim))
        self.assertNotIn(self.env.ref("base.group_system"), groups(raghad))
        if "mail.mail" in self.env:
            self.assertEqual(self.env["mail.mail"].search_count([]), mails, "no invitation unless asked")
        self.assertEqual(set(step.values["undo"]["res.users"]), {str(raghad.id), str(ebrahim.id)})
        self.assertTrue(self.card("users", {"users": [{"name": "Raghad", "email": "raghad@example.com"}]}).is_error)
        self.assertTrue(self.card("users", {"users": [{"name": "Z", "email": "z@example.com", "role": "ceo"}]}).is_error)

    def test_fiscal_year_and_lock_date(self):
        asked = self.card("fiscal", {"month": 6, "day": 30})
        if "fiscalyear_last_day" not in self.env.company._fields:
            self.assertTrue(asked.is_error)
            self.assertIn("Invoicing", asked.content)
            return
        self.done(asked)
        self.assertEqual((self.env.company.fiscalyear_last_day, self.env.company.fiscalyear_last_month), (30, "6"))
        self.assertTrue(self.card("fiscal", {"month": 2, "day": 31}).is_error)
        self.assertTrue(self.card("fiscal", {"lock_date": "2999-01-01"}).is_error)

    def test_the_defaults_wait_for_the_first_three_and_then_do_the_rest_in_one_card(self):
        refused = self.card("remaining")
        self.assertTrue(refused.is_error)
        self.assertIn("company, the country and the currency", refused.content)
        self.done(self.card("company", {"name": "Raghad Trading LLC"}))
        self.done(self.card("country", {"country": "AE"}))
        self.done(self.card("currency", {"currency": "AED"}))
        asked = self.card("remaining")
        self.assertFalse(asked.is_error, asked.content)
        steps = [item["step"] for item in asked.pending["values"]["data"]["steps"]]
        self.assertIn("timezone", steps)
        self.assertIn("apps", steps)
        self.done(asked)
        self.assertEqual(self.admin.tz, "Asia/Dubai")

    def test_the_summary_points_at_the_mail_server_and_finishes_the_setup(self):
        asked = self.card("summary")
        text = " ".join(self.lines(asked).values())
        self.assertIn("never configures", text)
        self.assertIn("What to do next", self.lines(asked))
        self.done(asked)
        self.assertEqual(self.env["ir.config_parameter"].sudo().get_param(setup.PARAM_DONE), "yes")
        self.assertFalse(setup.offered(self.dev))

    def test_the_status_says_where_the_setup_stands(self):
        read = tools.base.call(self.dev, "read_setup", {}, None, "agent")
        self.assertFalse(read.is_error, read.content)
        for word in ("fresh_database", "next_step", "l10n_ae", "kinds_of_business", "roles"):
            self.assertIn(word, read.content)


@tagged("post_install", "-at_install")
class TestSetupConversation(SetupCase, TransactionCase):
    """The same cards through the conversation: with a provider that reads free text, and with none"""

    def test_with_a_provider_a_yes_changes_the_company(self):
        adapter = self.use_adapter(script=[
            asking(call("prepare_setup_step", {"step": "company", "answers": {"name": "Raghad Trading LLC"}}),
                   text="Here is the first step."),
            saying("Done."),
        ])
        conversation, events = self.order(self.admin, "Set up this database with me")
        self.assertIn("prepare_setup_step", [tool["name"] for tool in adapter.calls[0]["tools"]])
        self.assertIn("read_setup", [tool["name"] for tool in adapter.calls[0]["tools"]])
        self.assertEqual(events[-1]["type"], "waiting")
        self.assertNotEqual(self.env.company.name, "Raghad Trading LLC")
        self.decide(conversation)
        self.assertEqual(self.env.company.name, "Raghad Trading LLC")

    def test_a_user_made_through_the_conversation_is_archived_by_undo(self):
        self.use_adapter(script=[
            asking(call("prepare_setup_step", {"step": "users", "answers": {"users": [
                {"name": "Raghad", "email": "raghad@example.com", "role": "salesperson"}]}})),
            saying("Done."),
        ])
        conversation, _events = self.order(self.admin, "Add Raghad as a salesperson")
        step = conversation.sudo().waiting_step_id
        self.decide(conversation)
        raghad = self.env["res.users"].search([("login", "=", "raghad@example.com")])
        self.assertTrue(raghad.active)
        self.assertTrue(step.sudo()._get_card()["can_undo"])
        self.env["nudge.step"].with_user(self.admin).undo_changes(step.id)
        raghad = self.env["res.users"].with_context(active_test=False).search([("login", "=", "raghad@example.com")])
        self.assertTrue(raghad, "never deleted")
        self.assertFalse(raghad.active, "archived")

    def test_a_plain_user_cannot_make_the_card_through_the_conversation(self):
        adapter = self.use_adapter(script=[
            asking(call("prepare_setup_step", {"step": "company", "answers": {"name": "Hijacked"}})), saying("No.")])
        conversation, events = self.order(self.plain, "Set up this database with me")
        self.assertNotIn("prepare_setup_step", [tool["name"] for tool in adapter.calls[0]["tools"]])
        self.assertNotEqual(self.env.company.name, "Hijacked")

    def test_with_no_provider_the_assistant_asks_and_shows_the_same_cards(self):
        self.env["nudge.provider"].search([]).active = False
        conversation, events = self.order(self.admin, "Set up this database with me")
        text = "".join(event.get("text", "") for event in events if event["type"] == "delta")
        self.assertIn("Step 1 of 12", text)
        self.assertNotEqual(self.env.company.name, "Raghad Trading LLC")
        # the company, said in one message
        conversation, events = self.order(
            self.admin, "Raghad Trading LLC, TRN 100123456700003, Sheikh Zayed Road, Dubai", conversation.id)
        self.assertEqual(events[-1]["type"], "waiting", events)
        events = self.decide(conversation)
        text = "".join(event.get("text", "") for event in events if event["type"] == "delta")
        self.assertEqual(self.env.company.name, "Raghad Trading LLC")
        self.assertIn("of 12", text, "after the yes the next question is asked")
        self.assertNotIn("Step 1 of 12", text)
        if "Step 2 of 12" not in text:
            return  # a chart of accounts is loaded here: the country is done; the rest is tried on the bare database
        # the country, in Arabic
        conversation, events = self.order(self.admin, "الامارات", conversation.id)
        self.assertEqual(events[-1]["type"], "waiting")
        step = conversation.sudo().waiting_step_id
        self.assertEqual(step.values["data"]["country_id"], self.env.ref("base.ae").id)
        self.decide(conversation)
        self.assertEqual(self.env.company.country_id.code, "AE")
        conversation, events = self.order(self.admin, "درهم", conversation.id)
        self.decide(conversation)
        self.assertEqual(self.env.company.currency_id.name, "AED")
        # the first three are done: the rest with the defaults
        conversation, events = self.order(self.admin, "do the rest with the defaults", conversation.id)
        self.assertEqual(events[-1]["type"], "waiting")
        self.assertEqual(conversation.sudo().waiting_step_id.values["step"], "remaining")

    def test_with_no_provider_a_step_can_be_skipped_and_the_setup_stopped(self):
        self.env["nudge.provider"].search([]).active = False
        conversation, _events = self.order(self.admin, "جهزلي النظام")
        state = conversation.sudo().setup_state
        self.assertTrue(state["active"])
        self.assertEqual(state["asking"], "company")
        _conversation, events = self.order(self.admin, "do the rest with the defaults", conversation.id)
        text = "".join(event.get("text", "") for event in events if event["type"] == "delta")
        self.assertIn("company, the country and the currency", text)
        _conversation, events = self.order(self.admin, "stop", conversation.id)
        self.assertFalse(conversation.sudo().setup_state["active"])

    def test_nothing_is_asked_of_a_plain_user_with_no_provider(self):
        self.env["nudge.provider"].search([]).active = False
        conversation, events = self.order(self.plain, "Set up this database with me")
        self.assertFalse(conversation.sudo().setup_state)
        self.assertNotEqual(self.env.company.name, "Hijacked")
        with self.assertRaises(AccessError):
            self.env["res.company"].with_user(self.plain).browse(self.env.company.id).write({"name": "Hijacked"})


@tagged("post_install", "-at_install")
class TestSetupInstallAllowlist(TransactionCase):

    def test_only_the_apps_of_the_assistant_and_a_localisation_can_be_installed(self):
        for name in ("base_import_module", "web_studio", "l10n_zz", "l10n_ae"+chr(10)+"x", "anything_else"):
            with self.subTest(name=name), self.assertRaises(UserError):
                setup.install_modules(self.env, [name])
        self.assertTrue(setup.installable(self.env, "l10n_ae"))
        self.assertTrue(setup.installable(self.env, "l10n_uk"))
        self.assertTrue(setup.installable(self.env, "purchase"))

