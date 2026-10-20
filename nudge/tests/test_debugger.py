from types import SimpleNamespace

from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import loop
from odoo.addons.nudge.models.nudge_conversation import DEVELOPER_PARAM
from odoo.addons.nudge.tools.debugger import code_problems, code_safe_to_run, parse_why

from .common import NudgeCase

BROKEN = "for r in records:\n    r.write({'comment': r.x_missing_note})"
FIXED = "for r in records:\n    r.write({'comment': r.function or ''})"


@tagged("post_install", "-at_install")
class TestParseWhy(TransactionCase):

    def test_the_question_is_read_in_english_and_in_arabic(self):
        cases = [
            ("why did the Mark new buyers rule fail", "rule", "Mark new buyers"),
            ("ليش فشلت أتمتة Mark new buyers", "rule", "Mark new buyers"),
            ("شو صار مع الـ cron تبع الرواتب", "cron", "الرواتب"),
            ("why did the payroll cron fail?", "cron", "payroll"),
            ("ليش ما اشتغل الاجراء Archive old leads", "action", "Archive old leads"),
            ("Notify manager مش شغالة", None, "Notify manager"),
        ]
        for text, kind, words in cases:
            parsed = parse_why(text)
            self.assertEqual((parsed["kind"], parsed["words"]), (kind, words), text)


@tagged("post_install", "-at_install")
class TestDebugger(NudgeCase, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = new_test_user(
            cls.env, login="nudge_dbg_admin",
            groups="nudge.group_nudge_agent,base.group_system,base.group_partner_manager")
        cls.env["ir.config_parameter"].sudo().set_param(DEVELOPER_PARAM, "yes")
        cls.partner_model = cls.env["ir.model"]._get("res.partner")

    def make_action(self, code=BROKEN, name="Raghad note"):
        return self.env["ir.actions.server"].create({
            "name": name, "model_id": self.partner_model.id, "state": "code", "code": code})

    def dev_env(self, user=None):
        return self.env(user=user or self.admin, context={"nudge_mode": "dev"})

    def run_tool(self, name, arguments, user=None):
        return tools.base.call(self.dev_env(user), name, arguments, None, "dev")

    def do(self, name, result):
        step = SimpleNamespace(values=result.pending["values"], res_model=result.pending["res_model"],
                               res_ids=result.pending["res_ids"])
        return tools.base.TOOLS[name].execute(self.dev_env(), step)

    # ------------------------------------------------------------ findings
    def test_a_missing_field_is_diagnosed_without_the_provider(self):
        action = self.make_action()
        result = self.run_tool("diagnose_failure", {"words": "why did Raghad note fail"})
        self.assertFalse(result.is_error, result.content)
        self.assertIn("res.partner has no field or method x_missing_note", result.content)
        # The code was run once on a dummy record and the failure put in words
        self.assertIn("no such field or method", result.content)
        self.assertIn(f"action_id {action.id}", result.content)
        self.assertFalse(self.env["res.partner"].search([("name", "=", "Nudge trial")]), "the dummy was discarded")

    def test_a_missing_model_and_a_wrong_search_field_are_named(self):
        found = code_problems(self.env, "env['x.nothing'].search([])\nrecord.parent_id.nothing_here\n"
                              "env['res.partner'].search([('no_such', '=', 1)])", "res.partner")
        text = " ".join(problem["text"] for problem in found)
        self.assertIn("x.nothing", text)
        self.assertIn("nothing_here", text)
        self.assertIn("no_such", text)
        self.assertEqual(code_problems(self.env, FIXED, "res.partner"), [])

    def test_rights_are_told(self):
        nobody = new_test_user(self.env, login="nudge_dbg_none", groups="base.group_user")
        cron = self.env["ir.cron"].create({
            "name": "Raghad payroll", "model_id": self.env["ir.model"]._get("res.users").id,
            "state": "code", "code": "env['res.partner'].search([])", "user_id": nobody.id,
            "interval_number": 1, "interval_type": "days"})
        text = self.run_tool("diagnose_failure", {"cron_id": cron.id}).content
        self.assertIn("runs as", text)
        self.assertIn(nobody.name, text)
        plain = self.run_tool("diagnose_failure", {"action_id": self.make_action(FIXED).id}).content
        self.assertIn("runs as", plain)

    def test_a_cron_with_a_stored_error_is_read(self):
        cron = self.env["ir.cron"].create({
            "name": "Ebrahim payroll run", "model_id": self.partner_model.id, "state": "code",
            "code": "for r in model.search([]):\n    r.write({'comment': r.x_missing_note})",
            "user_id": self.admin.id, "interval_number": 1, "interval_type": "days"})
        if "failure_count" not in cron._fields:
            self.skipTest("ir.cron has no failure_count on 17")
        cron.write({"failure_count": 3, "first_failure_date": "2026-10-01 08:00:00"})
        self.env["ir.logging"].sudo().create({
            "name": "odoo.addons.base.models.ir_cron", "type": "server", "dbname": self.env.cr.dbname,
            "level": "ERROR", "path": "ir_cron", "func": "_run", "line": "1",
            "message": 'Job "Ebrahim payroll run" failed\nTraceback (most recent call last):\n'
                       '  File "<string>", line 2, in <module>\n    r.write({\'comment\': r.x_missing_note})\n'
                       "AttributeError: 'res.partner' object has no attribute 'x_missing_note'"})
        text = self.run_tool("diagnose_failure", {"words": "شو صار مع الـ cron تبع Ebrahim payroll"}).content
        self.assertIn("Failed in a row: 3", text)
        self.assertIn("2026-10-01", text)
        self.assertIn("AttributeError: 'res.partner' object has no attribute 'x_missing_note'", text)
        self.assertIn("line 2, in", text)

    def test_a_pasted_error_is_read_as_data(self):
        self.make_action()
        text = self.run_tool("diagnose_failure", {
            "words": "Raghad note", "error": "ZeroDivisionError: division by zero"}).content
        self.assertIn("divides by zero", text)

    def test_a_non_developer_is_refused(self):
        self.make_action()
        agent = self.agent
        self.assertTrue(self.run_tool("diagnose_failure", {"words": "Raghad note"}, user=agent).is_error)
        self.assertTrue(self.run_tool("prepare_code_fix", {"action_id": 1, "code": FIXED}, user=agent).is_error)
        offered = [tool.name for tool in tools.base.available(self.env(user=agent), "dev")]
        self.assertNotIn("diagnose_failure", offered)
        self.env["ir.config_parameter"].sudo().set_param(DEVELOPER_PARAM, "no")
        self.assertTrue(self.run_tool("diagnose_failure", {"words": "Raghad note"}).is_error)

    # ------------------------------------------------------------ the fix
    def test_the_fix_card_applies_after_yes_and_is_undoable(self):
        action = self.make_action()
        card = self.run_tool("prepare_code_fix", {"action_id": action.id, "code": FIXED})
        self.assertFalse(card.is_error, card.content)
        diff = next(line for line in card.pending["preview"]["lines"] if line["label"] == "Difference")
        self.assertIn("-    r.write({'comment': r.x_missing_note})", diff["value"])
        self.assertIn("+    r.write({'comment': r.function or ''})", diff["value"])
        self.assertEqual(action.code, BROKEN, "Nothing before the yes")
        done = self.do("prepare_code_fix", card)
        self.assertFalse(done.is_error, done.content)
        self.assertEqual(action.code, FIXED)
        archived = self.env["ir.actions.server"].search([("name", "like", "(before fix)")])
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived.code, BROKEN)
        # Undone
        back = self.run_tool("prepare_code_fix", {"restore_from": archived.id})
        self.assertFalse(back.is_error, back.content)
        self.do("prepare_code_fix", back)
        self.assertEqual(action.code, BROKEN)

    def test_the_fix_card_refuses_an_action_of_an_app_or_of_another_user(self):
        self.env["ir.model.data"].create({
            "module": "base", "name": "nudge_dbg_shipped", "model": "ir.actions.server", "res_id": self.make_action().id})
        card = self.run_tool("prepare_code_fix", {"action_id": self.env.ref("base.nudge_dbg_shipped").id, "code": FIXED})
        self.assertTrue(card.is_error)
        self.assertIn("comes with an app", card.content)
        other = new_test_user(self.env, login="nudge_dbg_other", groups="base.group_user")
        foreign = self.env["ir.actions.server"].with_user(self.env.ref("base.user_admin")).create({
            "name": "Raghad foreign", "model_id": self.partner_model.id, "state": "code", "code": BROKEN})
        foreign.create_uid = other
        card = self.run_tool("prepare_code_fix", {"action_id": foreign.id, "code": FIXED})
        self.assertTrue(card.is_error)
        self.assertIn("written by", card.content)

    def test_the_trial_allows_no_exec_eval_or_open(self):
        for code in ("exec('x=1')", "eval('1')", "open('/etc/passwd')", "getattr(record, 'write')({})"):
            self.assertFalse(code_safe_to_run(code), code)
        self.assertTrue(code_safe_to_run("for r in records:\n    r.write({'comment': str(len(records))})"))

    def test_a_fix_that_still_names_a_missing_field_is_refused(self):
        action = self.make_action()
        bad = self.run_tool("prepare_code_fix", {
            "action_id": action.id, "code": "for r in records:\n    r.write({'comment': r.still_missing})"})
        self.assertTrue(bad.is_error)
        self.assertIn("still_missing", bad.content)
        self.assertTrue(self.run_tool("prepare_code_fix", {"action_id": action.id, "code": "record.write({"}).is_error)
        self.assertEqual(action.code, BROKEN)

    # ------------------------------------------------------------ through the provider
    def test_the_provider_diagnoses_then_proposes_the_fix(self):
        from .test_agent import asking, call, saying  # noqa: PLC0415

        action = self.make_action()
        adapter = self.use_adapter(script=[
            asking(call("diagnose_failure", {"words": "ليش فشلت أتمتة Raghad note"})),
            asking(call("prepare_code_fix", {"action_id": action.id, "code": FIXED}, call_id="call_2")),
            saying("Fixed."),
        ])
        conversation = self.env["nudge.conversation"].with_user(self.admin)._chat_open()
        job = conversation._chat_prepare("ليش فشلت أتمتة Raghad note", mode="dev")
        list(loop.run(job, conversation._chat_record))
        read = adapter.calls[1]["turns"][-1]["results"][0]["content"]
        self.assertIn('<data source="diagnose_failure">', read)
        self.assertIn("x_missing_note", read)
        self.decide(conversation)
        self.assertEqual(action.code, FIXED)

    def test_the_command_answers_without_a_provider(self):
        self.make_action()
        answer = self.env["nudge.quick"].with_user(self.admin).run_command("/debug ليش فشلت Raghad note")
        self.assertIn("x_missing_note", answer["code"])
        refused = self.env["nudge.quick"].with_user(self.agent).run_command("/debug Raghad note")
        self.assertNotIn("code", refused)
