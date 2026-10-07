import json

from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.tools.python import PYTHON_PARAM

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestPython(NudgeCase, TransactionCase):
    """Computing with Python, as the person, reading only."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(PYTHON_PARAM, "yes")
        cls.partners = cls.env["res.partner"].create([
            {"name": "Nudge Py One", "partner_latitude": 2.0},
            {"name": "Nudge Py Two", "partner_latitude": 5.0},
        ])

    def run_code(self, code, user=None):
        return tools.base.call(
            self.env(user=user or self.agent), "run_python",
            {"code": code, "purpose": "A test"}, mode="agent")

    def payload(self, code):
        result = self.run_code(code)
        self.assertFalse(result.is_error, result.content)
        return json.loads(result.content)

    def test_computes_as_the_person(self):
        payload = self.payload(
            "partners = env['res.partner'].search([('name', 'like', 'Nudge Py')])\n"
            "log('found', len(partners))\n"
            "result = {'total': sum(partners.mapped('partner_latitude')), "
            "'names': sorted(partners.mapped('name'))}\n")
        self.assertEqual(payload["output"], ["found 2"])
        self.assertEqual(payload["result"], {"total": 7.0, "names": ["Nudge Py One", "Nudge Py Two"]})
        self.assertEqual(payload["purpose"], "A test")
        self.assertIn("discarded", payload["note"])

    def test_records_and_dates_as_results(self):
        payload = self.payload("result = env['res.partner'].search([('name', '=', 'Nudge Py One')])")
        self.assertEqual(payload["result"]["model"], "res.partner")
        self.assertEqual(payload["result"]["count"], 1)
        self.assertEqual(payload["result"]["names"], ["Nudge Py One"])

        payload = self.payload("result = datetime.date(2026, 9, 1) + dateutil.relativedelta.relativedelta(months=1)")
        self.assertEqual(payload["result"], "2026-10-01")

        payload = self.payload("result = env.user.name, env.company.name, 'res.partner' in env, 'res.users' in env")
        self.assertEqual(payload["result"][2:], [True, False])

    def test_what_it_writes_is_discarded(self):
        payload = self.payload(
            "p = env['res.partner'].search([('name', '=', 'Nudge Py One')])\n"
            "p.write({'name': 'Changed by code'})\n"
            "log(p.name)\n")
        self.assertEqual(payload["output"], ["Changed by code"], "Seen by the code itself")
        self.assertEqual(self.partners[0].name, "Nudge Py One", "Taken back once the code ends")

        self.payload("env['res.partner'].create({'name': 'Nudge Py Made'})")
        self.assertFalse(self.env["res.partner"].search([("name", "=", "Nudge Py Made")]))

    def test_what_the_sandbox_refuses(self):
        for code, text in [
            ("result = env['res.partner'].sudo().search([])", ".sudo"),
            ("result = env['res.partner'].search([]).env", ".env"),
            ("x = env.cr", ".cr"),
            ("env.cr.execute('select 1')", "may not use"),
            ("result = env['res.partner']._fields", "._fields"),
            ("import os", "imported"),
            ("result = getattr(env, 'cr')", "getattr"),
            ("result = env['ir.config_parameter'].get_param('database.secret')", "no model"),
            ("result = env['res.users'].search([])", "no model"),
            ("result = env['nudge.provider'].search([])", "no model"),
            ("env['res.partner'].search([]).unlink()", ".unlink"),
            ("x = (lambda: 1)()", "lambda"),
            ("result = env.ref('base.user_admin')", "no record"),
            ("result = '__class__'", "private"),
        ]:
            result = self.run_code(code)
            self.assertTrue(result.is_error, code)
            self.assertIn(text, result.content, code)

    def test_errors_are_told(self):
        result = self.run_code("result = 1 / 0")
        self.assertTrue(result.is_error)
        self.assertIn("ZeroDivisionError", result.content)
        result = self.run_code("raise UserError('Stop here')")
        self.assertTrue(result.is_error)
        self.assertIn("Stop here", result.content)
        result = self.run_code("result = = 1")
        self.assertTrue(result.is_error)
        self.assertIn("could not be read", result.content)

    def test_who_gets_it(self):
        def names(env, mode):
            return [tool.name for tool in tools.base.available(env, mode)]

        agent = self.env(user=self.agent)
        self.assertIn("run_python", names(agent, "agent"))
        self.assertNotIn("run_python", names(agent, "talk"))
        self.assertNotIn("run_python", names(self.env(user=self.user_a), "agent"))

        self.env["ir.config_parameter"].sudo().set_param(PYTHON_PARAM, "no")
        self.assertNotIn("run_python", names(agent, "agent"), "Off unless the administrator says")
        refused = self.run_code("result = 1")
        self.assertTrue(refused.is_error)
        self.assertIn("no tool", refused.content)

    def test_the_switch_in_the_settings(self):
        Settings = self.env["res.config.settings"]
        self.assertTrue(Settings.create({}).nudge_python)
        settings = Settings.create({"nudge_python": False})
        settings.set_values()
        self.assertFalse(self.env["nudge.conversation"]._is_python_allowed())
        self.assertFalse(Settings.create({}).nudge_python)
