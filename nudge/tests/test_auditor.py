"""NP-16, the auditor: a time-boxed Nudge User with the reading tools (Ask the data) and the log of what Nudge
did, in their companies, to read. Nothing written anywhere through Nudge; the access ends on its date; what
they ask is kept for the company. Never a provider is reached."""
import json
from datetime import timedelta

from odoo import Command, fields, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.nudge.llm import loop
from odoo.addons.nudge.tools import base

from .common import NudgeCase
from .test_ask_data import NEVER

AUDITOR_LOGIN = "nudge_auditor"
QUESTION = "How many contacts does your company have, Raghad?"


@tagged("post_install", "-at_install")
class TestAuditor(NudgeCase, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_one = cls.env.company
        cls.company_two = cls.env["res.company"].create({"name": "Your Other Company"})
        cls.today = fields.Date.today()
        cls.auditor = new_test_user(
            cls.env, login=AUDITOR_LOGIN, groups="nudge.group_nudge_auditor",
            nudge_auditor_until=cls.today + timedelta(days=10),
            company_id=cls.company_one.id, company_ids=[Command.set(cls.company_one.ids)])
        # What Nudge did for Ebrahim, in each company
        Conversation = cls.env["nudge.conversation"].sudo()
        cls.runs = cls.env["nudge.run"]
        cls.steps = cls.env["nudge.step"]
        for company in (cls.company_one, cls.company_two):
            conversation = Conversation.create({"user_id": cls.user_a.id, "company_id": company.id})
            run = cls.env["nudge.run"].sudo().create({
                "name": "Change a contact of %s" % company.name, "conversation_id": conversation.id,
                "user_id": cls.user_a.id, "company_id": company.id, "state": "done"})
            cls.runs |= run
            cls.steps |= cls.env["nudge.step"].sudo().create({
                "run_id": run.id, "company_id": company.id, "tool": "prepare_write", "kind": "write",
                "summary": "Change Ebrahim", "values": {"name": "Ebrahim"},
                "arguments": {"model": "res.partner", "conditions": []}})

    # -- helpers --------------------------------------------------------
    def names_for(self, user, mode="talk"):
        return {tool.name for tool in base.available(self.env(user=user), mode)}

    def ask_as_auditor(self, text=QUESTION):
        Conversation = self.env["nudge.conversation"].with_user(self.auditor)
        conversation = Conversation._chat_open(None)
        job = conversation._chat_prepare(text)
        return conversation, list(loop.run(job, conversation._chat_record))

    def csrf_post(self, url, data=None, **kwargs):
        data = dict(data or {}, csrf_token=http.Request.csrf_token(self))
        return self.url_open(url, data=data, allow_redirects=False, **kwargs)

    # -- the group ------------------------------------------------------
    def test_the_auditor_is_a_nudge_user_and_nothing_more(self):
        for xml_id in ("nudge.group_nudge_user", "nudge.group_nudge_auditor", "base.group_user"):
            self.assertTrue(self.auditor.has_group(xml_id), xml_id)
        for xml_id in ("nudge.group_nudge_agent", "nudge.group_nudge_manager", "base.group_system"):
            self.assertFalse(self.auditor.has_group(xml_id), xml_id)

    def test_an_auditor_can_never_be_an_agent_a_manager_or_an_administrator(self):
        for xml_id in ("nudge.group_nudge_agent", "nudge.group_nudge_manager", "base.group_system"):
            with self.assertRaises(ValidationError, msg=xml_id), self.cr.savepoint():
                self.auditor.write({self.env["nudge.auditor.wizard"]._groups_field(): [Command.link(self.env.ref(xml_id).id)]})
        # And an agent is not made an auditor either
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.agent.write({"nudge_auditor_until": self.today + timedelta(days=1),
                              self.env["nudge.auditor.wizard"]._groups_field(): [
                                  Command.link(self.env.ref("nudge.group_nudge_auditor").id)]})

    def test_an_auditor_has_a_last_day(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            new_test_user(self.env, login="nudge_aud_open", groups="nudge.group_nudge_auditor")

    # -- Ask the data, and nothing that writes ----------------------------
    def test_the_auditor_is_offered_the_reading_tools_and_nothing_that_writes(self):
        names = self.names_for(self.auditor)
        self.assertLessEqual(base.ASK_TOOLS, names)
        self.assertFalse(names & NEVER, names & NEVER)
        for tool in base.available(self.env(user=self.auditor), "talk"):
            self.assertNotIsInstance(tool, base.Action, tool.name)
            self.assertIn(tool.kind, ("read", "show"), tool.name)
        self.assertFalse(self.names_for(self.auditor, "agent") & (NEVER | {"prepare_write"}))
        self.assertFalse(self.names_for(self.auditor, "dev") & NEVER)

    def test_the_schema_the_model_receives_is_read_only(self):
        adapter = self.use_adapter()
        self.ask_as_auditor()
        offered = {tool["name"] for tool in adapter.calls[0]["tools"]}
        self.assertIn("aggregate_records", offered)
        self.assertFalse(offered & NEVER, offered & NEVER)

    def test_the_auditor_asks_a_question_and_it_is_kept(self):
        self.use_adapter(reply="There are some.")
        conversation, events = self.ask_as_auditor()
        self.assertEqual(events[-1]["type"], "done")
        self.assertTrue(conversation.sudo().kept_for_audit)
        self.assertEqual(conversation.sudo().message_ids.filtered(lambda m: m.role == "user").content, QUESTION)

    def test_agent_mode_is_closed_to_the_auditor(self):
        conversation = self.env["nudge.conversation"].with_user(self.auditor)._chat_open(None)
        for mode in ("agent", "dev"):
            with self.assertRaises(UserError, msg=mode):
                conversation._chat_prepare("Change every contact", mode=mode)
        self.assertFalse(self.env["nudge.message"].sudo().search([("content", "=", "Change every contact")]))

    # -- the log: to read, in their companies -----------------------------
    def test_the_log_is_read_only(self):
        as_auditor = self.env(user=self.auditor)
        run, step = self.runs[0].with_env(as_auditor), self.steps[0].with_env(as_auditor)
        self.assertEqual(run.name, self.runs[0].name)
        self.assertEqual(step.values, {"name": "Ebrahim"}, "the values written are read")
        for record in (run, step):
            with self.assertRaises(AccessError), self.cr.savepoint():
                record.write({"state": "done"} if record._name == "nudge.run" else {"summary": "x"})
            with self.assertRaises(AccessError), self.cr.savepoint():
                record.unlink()
        with self.assertRaises(AccessError), self.cr.savepoint():
            as_auditor["nudge.run"].create({"name": "x", "conversation_id": run.conversation_id.id, "user_id": self.auditor.id})
        with self.assertRaises(AccessError), self.cr.savepoint():
            as_auditor["nudge.step"].create({"run_id": run.id, "tool": "prepare_write"})
        for model in ("nudge.built", "nudge.built.log"):
            with self.assertRaises(AccessError, msg=model), self.cr.savepoint():
                as_auditor[model].create({})
        with self.assertRaises(AccessError), self.cr.savepoint():
            as_auditor["nudge.built.switch"].create({})

    def test_the_log_is_the_companies_of_the_auditor_only(self):
        as_auditor = self.env(user=self.auditor)
        self.assertEqual(as_auditor["nudge.run"].search([("id", "in", self.runs.ids)]), self.runs[0])
        self.assertEqual(as_auditor["nudge.step"].search([("id", "in", self.steps.ids)]), self.steps[0])
        # Allowed the second company too, they read it
        self.auditor.write({"company_ids": [Command.link(self.company_two.id)]})
        both = self.env(user=self.auditor, context={"allowed_company_ids": [self.company_one.id, self.company_two.id]})
        self.assertEqual(both["nudge.run"].search([("id", "in", self.runs.ids)]), self.runs)

    def test_made_by_nudge_is_scoped_by_company_too(self):
        Built = self.env["nudge.built"].sudo()
        mine = Built.create({"name": "Rule A", "kind": "automation", "res_model": "base.automation", "res_id": 1,
                             "company_id": self.company_one.id})
        other = Built.create({"name": "Rule B", "kind": "automation", "res_model": "base.automation", "res_id": 2,
                              "company_id": self.company_two.id})
        found = self.env(user=self.auditor)["nudge.built"].search([("id", "in", (mine | other).ids)])
        self.assertEqual(found, mine)

    def test_the_auditor_gets_the_audit_menu_and_a_user_does_not(self):
        menu = self.env.ref("nudge.nudge_menu_audit")
        self.assertIn(menu.id, self.env["ir.ui.menu"].with_user(self.auditor)._visible_menu_ids())
        self.assertNotIn(menu.id, self.env["ir.ui.menu"].with_user(self.user_a)._visible_menu_ids())

    # -- the direct paths -------------------------------------------------
    def test_the_paths_that_write_are_closed_to_the_auditor(self):
        conversation = self.env["nudge.conversation"].with_user(self.auditor)._chat_open(None)
        self.authenticate(AUDITOR_LOGIN, AUDITOR_LOGIN)
        decide = self.csrf_post("/nudge/agent/decide", {
            "conversation_id": conversation.id, "step_id": self.steps[0].id, "decision": "yes"})
        self.assertEqual(decide.status_code, 403)
        bulk = self.csrf_post("/nudge/bulk/start", {
            "conversation_id": conversation.id, "model": "res.partner", "operation": "fill", "field": "comment",
            "ids": json.dumps([self.user_a.partner_id.id])})
        self.assertEqual(bulk.status_code, 403)
        self.assertFalse(self.steps[0].sudo().decided_at)

    def test_the_import_matcher_writes_nothing_for_the_auditor(self):
        self.authenticate(AUDITOR_LOGIN, AUDITOR_LOGIN)
        before = self.env["res.partner"].sudo().search_count([("name", "=", "Nudge Auditor Ebrahim")])
        for step in ("match", "check", "csv"):
            response = self.csrf_post("/nudge/import/file", {"model": "res.partner", "step": step},
                                      files={"file": ("contacts.csv", b"Name\nNudge Auditor Ebrahim\n", "text/csv")})
            self.assertIn(response.status_code, (200, 403), step)
        self.assertEqual(self.env["res.partner"].sudo().search_count([("name", "=", "Nudge Auditor Ebrahim")]), before)

    def test_the_step_list_is_the_companies_of_the_auditor_only(self):
        self.authenticate(AUDITOR_LOGIN, AUDITOR_LOGIN)
        elsewhere = self.url_open("/nudge/step/%s/open" % self.steps[1].id, allow_redirects=False)
        self.assertEqual(elsewhere.status_code, 403)
        mine = self.url_open("/nudge/step/%s/open" % self.steps[0].id, allow_redirects=False)
        self.assertNotEqual(mine.status_code, 403, "their own company's step is theirs to read")

    def test_changing_the_log_through_the_buttons_is_refused(self):
        as_auditor = self.env(user=self.auditor)
        with self.assertRaises(AccessError):
            as_auditor["nudge.step"].undo_changes(self.steps[0].id)
        with self.assertRaises(UserError):
            self.runs[0].with_env(as_auditor).action_stop()
        with self.assertRaises(UserError):
            self.runs[0].with_env(as_auditor).action_undo_built()

    # -- the end ------------------------------------------------------------
    def test_a_session_is_of_no_use_after_the_last_day(self):
        self.authenticate(AUDITOR_LOGIN, AUDITOR_LOGIN)
        url = "/nudge/step/%s/open" % self.steps[0].id
        self.assertNotIn(self.url_open(url, allow_redirects=False).status_code, (401, 403))
        self.auditor.sudo().nudge_auditor_until = self.today - timedelta(days=1)
        self.env.flush_all()
        after = self.url_open(url, allow_redirects=False)
        self.assertNotEqual(after.status_code, 200)
        self.assertTrue(after.status_code in (401, 302, 303) and (after.status_code == 401 or "login" in after.headers.get("Location", "")),
                        (after.status_code, after.headers.get("Location")))
        # The chat stream is closed the same way
        stream = self.csrf_post("/nudge/chat/stream", {"message": "Hello"})
        self.assertNotEqual(stream.status_code, 200)
        self.assertFalse(self.env["nudge.message"].sudo().search([("user_id", "=", self.auditor.id)]))

    def test_the_last_day_itself_still_works(self):
        self.auditor.sudo().nudge_auditor_until = self.today
        self.authenticate(AUDITOR_LOGIN, AUDITOR_LOGIN)
        self.assertNotIn(self.url_open("/nudge/step/%s/open" % self.steps[0].id, allow_redirects=False).status_code, (401, 403))

    def test_the_cron_archives_the_auditors_past_their_day(self):
        late = new_test_user(self.env, login="nudge_aud_late", groups="nudge.group_nudge_auditor",
                             nudge_auditor_until=self.today - timedelta(days=1))
        last = new_test_user(self.env, login="nudge_aud_last", groups="nudge.group_nudge_auditor",
                             nudge_auditor_until=self.today)
        self.assertEqual(self.env["res.users"]._cron_nudge_end_auditors(), 1)
        self.assertFalse(late.active)
        self.assertTrue(late.sudo().nudge_auditor_ended_on)
        self.assertTrue(last.active)
        self.assertTrue(self.auditor.active)
        self.assertEqual(self.env["res.users"]._cron_nudge_end_auditors(), 0, "once")

    def test_an_administrator_revokes_the_access_now(self):
        admin = new_test_user(self.env, login="nudge_adm_aud", groups="base.group_system")
        self.auditor.with_user(admin).action_nudge_revoke_auditor()
        self.assertFalse(self.auditor.active)
        with self.assertRaises(ValidationError):
            self.env["res.users"].with_user(self.manager).browse(self.user_a.id).action_nudge_revoke_auditor()

    # -- what they asked is kept ---------------------------------------------
    def test_what_an_auditor_asked_is_kept_against_the_retention(self):
        self.use_adapter(reply="Some.")
        audited, _events = self.ask_as_auditor()
        ordinary, _event = self.ask(self.user_a, "How many contacts are there?")
        self.env["ir.config_parameter"].sudo().set_param("nudge.retention_days", "1")
        old = fields.Datetime.subtract(fields.Datetime.now(), days=30)
        self.env.flush_all()
        self.env.cr.execute("UPDATE nudge_conversation SET write_date = %s WHERE id IN %s",
                            (old, tuple((audited | ordinary).ids)))
        self.env["nudge.conversation"].invalidate_model()
        self.env["nudge.conversation"]._cron_apply_retention()
        self.assertTrue(audited.sudo().exists(), "the question of an auditor stays")
        self.assertTrue(audited.sudo().message_ids)
        self.assertFalse(ordinary.sudo().exists(), "the retention still forgets the others")

    def test_nobody_deletes_or_unmarks_what_an_auditor_asked(self):
        self.use_adapter(reply="Some.")
        audited, _events = self.ask_as_auditor()
        as_manager = audited.with_user(self.manager)
        with self.assertRaises(AccessError), self.cr.savepoint():
            as_manager.unlink()
        with self.assertRaises(AccessError), self.cr.savepoint():
            as_manager.write({"kept_for_audit": False})
        with self.assertRaises(AccessError), self.cr.savepoint():
            audited.write({"kept_for_audit": False})

    def test_the_managers_see_the_questions_of_the_auditors(self):
        self.use_adapter(reply="Some.")
        self.ask_as_auditor()
        self.ask(self.user_a, "A question of Ebrahim")
        action = self.env.ref("nudge.nudge_auditor_question_action")
        found = self.env["nudge.message"].with_user(self.manager).search(json_domain(action.domain))
        self.assertEqual(found.mapped("content"), [QUESTION])
        self.assertEqual(found.user_id, self.auditor)
        self.assertEqual(self.auditor.sudo().nudge_auditor_questions, 1)
        # An ordinary user does not see the others' questions
        self.assertFalse(self.env["nudge.message"].with_user(self.user_b).search(json_domain(action.domain)))

    # -- the wizard -------------------------------------------------------------
    def test_the_wizard_makes_an_auditor_with_the_one_group_and_nothing_else(self):
        admin = new_test_user(self.env, login="nudge_adm_wiz", groups="base.group_system")
        wizard = self.env["nudge.auditor.wizard"].with_user(admin).create({
            "name": "Raghad Auditor", "login": "raghad.auditor@example.com",
            "company_ids": [Command.set(self.company_one.ids)],
            "until": self.today + timedelta(days=20), "send_invitation": False})
        result = wizard.action_create()
        user = self.env["res.users"].browse(result["res_id"])
        self.assertTrue(user.has_group("nudge.group_nudge_auditor"))
        self.assertTrue(user.has_group("nudge.group_nudge_user"))
        for xml_id in ("nudge.group_nudge_agent", "nudge.group_nudge_manager", "base.group_system"):
            self.assertFalse(user.has_group(xml_id), xml_id)
        self.assertEqual(user.sudo().nudge_auditor_until, self.today + timedelta(days=20))
        self.assertEqual(user.company_ids, self.company_one)
        self.assertFalse(user.share)

    def test_the_wizard_refuses_what_acts_and_the_past(self):
        admin = new_test_user(self.env, login="nudge_adm_wi2", groups="base.group_system")
        values = {"name": "Ebrahim Auditor", "login": "ebrahim.auditor@example.com",
                  "company_ids": [Command.set(self.company_one.ids)], "send_invitation": False,
                  "until": self.today + timedelta(days=5)}
        agent_group = self.env.ref("nudge.group_nudge_agent")
        with self.assertRaises(UserError), self.cr.savepoint():
            self.env["nudge.auditor.wizard"].with_user(admin).create(
                dict(values, read_group_ids=[Command.set(agent_group.ids)])).action_create()
        with self.assertRaises(UserError), self.cr.savepoint():
            self.env["nudge.auditor.wizard"].with_user(admin).create(
                dict(values, until=self.today - timedelta(days=1))).action_create()
        self.assertFalse(self.env["res.users"].search([("login", "=", "ebrahim.auditor@example.com")]))

    def test_the_wizard_is_for_administrators_only(self):
        values = {"name": "Raghad Auditor", "login": "raghad.auditor2@example.com",
                  "company_ids": [Command.set(self.company_one.ids)], "until": self.today + timedelta(days=5)}
        for user in (self.manager, self.agent, self.user_a, self.auditor):
            with self.assertRaises(AccessError, msg=user.login), self.cr.savepoint():
                self.env["nudge.auditor.wizard"].with_user(user).create(values)

    # -- the review fixes -----------------------------------------------------
    def test_the_wizard_refuses_a_group_that_writes_and_names_it(self):
        admin = new_test_user(self.env, login="nudge_adm_wi3", groups="base.group_system")
        group = next((self.env.ref(xml_id) for xml_id in (
            "account.group_account_manager", "sales_team.group_sale_manager", "base.group_partner_manager")
            if self.env.ref(xml_id, raise_if_not_found=False)), None)
        self.assertTrue(group)
        values = {"name": "Ebrahim Auditor", "login": "ebrahim.auditor3@example.com",
                  "company_ids": [Command.set(self.company_one.ids)], "send_invitation": False,
                  "until": self.today + timedelta(days=5), "read_group_ids": [Command.set(group.ids)]}
        with self.assertRaises(UserError) as caught, self.cr.savepoint():
            self.env["nudge.auditor.wizard"].with_user(admin).create(values).action_create()
        self.assertIn(group.display_name, str(caught.exception))
        self.assertFalse(self.env["res.users"].search([("login", "=", values["login"])]))

    def test_a_forbidden_right_given_through_the_groups_is_refused(self):
        auditor_group = self.env.ref("nudge.group_nudge_auditor")
        agent_group = self.env.ref("nudge.group_nudge_agent")
        with self.assertRaises(ValidationError), self.cr.savepoint():
            auditor_group.write({"implied_ids": [Command.link(agent_group.id)]})
        users_field = self.env["res.groups"]._nudge_users_field()
        with self.assertRaises(ValidationError), self.cr.savepoint():
            auditor_group.write({users_field: [Command.link(self.manager.id)]})

    def test_an_expired_auditor_cannot_sign_in_over_rpc(self):
        def authenticate():
            reply = self.url_open("/jsonrpc", data=json.dumps({"jsonrpc": "2.0", "method": "call", "id": 1, "params": {
                "service": "common", "method": "authenticate",
                "args": [self.env.cr.dbname, AUDITOR_LOGIN, AUDITOR_LOGIN, {}]}}),
                headers={"Content-Type": "application/json"})
            return reply.json().get("result")

        self.assertTrue(authenticate())
        self.auditor.sudo().nudge_auditor_until = self.today - timedelta(days=1)
        self.env.flush_all()
        self.assertFalse(authenticate())

    def test_only_an_auditor_can_be_expired_and_the_cron_runs_every_hour(self):
        self.user_a.sudo().nudge_auditor_until = self.today - timedelta(days=1)
        self.assertFalse(self.env["res.users"]._nudge_auditor_expired(self.user_a.id))
        cron = self.env.ref("nudge.cron_end_auditors")
        self.assertEqual((cron.interval_number, cron.interval_type), (1, "hours"))

    def test_an_auditor_cannot_archive_or_rename_what_was_kept(self):
        self.use_adapter(reply="Some.")
        audited, _events = self.ask_as_auditor()
        audited.sudo().name = "How many contacts"
        mine = audited.with_user(self.auditor)
        with self.assertRaises(AccessError), self.cr.savepoint():
            mine.write({"active": False})
        with self.assertRaises(AccessError), self.cr.savepoint():
            mine.write({"name": "Nothing to see"})
        with self.assertRaises(AccessError), self.cr.savepoint():
            mine.unlink()
        self.assertTrue(audited.sudo().active)
        self.assertEqual(audited.sudo().name, "How many contacts")


def json_domain(text):
    """:return: the domain an action holds as text"""
    from odoo.tools.safe_eval import safe_eval  # noqa: PLC0415
    return safe_eval(text)
