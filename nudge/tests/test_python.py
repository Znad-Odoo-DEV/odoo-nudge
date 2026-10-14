import json
import sys
import time
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.tools import python as python_tool
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

    def test_reads_in_every_way_that_reads(self):
        payload = self.payload(
            "partners = env['res.partner'].search([('name', 'like', 'Nudge Py')], order='name')\n"
            "one = partners[0]\n"
            "def far(p):\n"
            "    return p.partner_latitude > 3\n"
            "result = {\n"
            "    'names': [p.name for p in partners],\n"
            "    'read': one.read(['name'])[0]['name'],\n"
            "    'count': env['res.partner'].search_count([('name', 'like', 'Nudge Py')]),\n"
            "    'exists': env['res.partner'].browse(partners.ids).exists().ids == partners.ids,\n"
            "    'union': len(one | partners[1]),\n"
            "    'in': one in partners,\n"
            "    'sorted': partners.sorted('partner_latitude')[-1].name,\n"
            "    'filtered': partners.filtered(far).mapped('name'),\n"
            "    'item': one['name'],\n"
            "    'rem': 7 % 2,\n"
            "    'text': '%s and %d' % ('a', 1),\n"
            "    'f': f'{1 + 1}',\n"
            "    'company': env.company.name == user.company_id.name,\n"
            "    'field': env['res.partner'].fields_get(['name'])['name']['type'],\n"
            "}\n")
        self.assertEqual(payload["result"], {
            "names": ["Nudge Py One", "Nudge Py Two"], "read": "Nudge Py One", "count": 2,
            "exists": True, "union": 2, "in": True, "sorted": "Nudge Py Two",
            "filtered": ["Nudge Py Two"], "item": "Nudge Py One", "rem": 1, "text": "a and 1",
            "f": "2", "company": True, "field": "char",
        })

    def test_what_would_write_or_act_is_refused(self):
        for code, text in [
            ("env['res.partner'].search([('name', '=', 'Nudge Py One')]).write({'name': 'x'})", ".write()"),
            ("env['res.partner'].create({'name': 'Nudge Py Made'})", ".create()"),
            ("env['res.partner'].search([], limit=1).copy()", ".copy()"),
            ("env['res.partner'].search([], limit=1).action_archive()", ".action_archive()"),
            ("env['res.partner'].search([], limit=1).message_post(body='hi')", ".message_post()"),
            ("env['res.partner'].search([], limit=1).button_done()", ".button_done()"),
            ("env['res.partner'].search([], limit=1).send_mail()", ".send_mail()"),
            ("env['res.partner'].search([], limit=1).do_post()", ".do_post()"),
            # Held without being called, the method is refused when the code takes it
            ("p = env['res.partner'].search([], limit=1)\nf = p.toggle_active\nf()", ".toggle_active()"),
        ]:
            result = self.run_code(code)
            self.assertTrue(result.is_error, code)
            self.assertIn(text, result.content, code)
            self.assertIn("only reads", result.content, code)
        self.assertEqual(self.partners[0].name, "Nudge Py One")
        self.assertFalse(self.env["res.partner"].search([("name", "=", "Nudge Py Made")]))
        # A field named like an action is read all the same
        self.assertEqual(self.payload("result = env['res.partner'].search([], limit=1).create_date is not None")["result"], True)

    def test_what_leads_to_a_closed_model_is_refused(self):
        for code, text in [
            ("result = env['res.partner'].search([], limit=1).user_id", "may not reach res.users"),
            ("result = env['res.partner'].search([], limit=1).user_id.login", "may not reach res.users"),
            ("result = env['res.partner'].search([]).mapped('user_id')", "may not reach res.users"),
            ("result = env['res.partner'].search([]).mapped('user_id.login')", "may not reach res.users"),
            ("result = env['res.partner'].search([]).filtered('user_id.active')", "may not reach res.users"),
            ("result = env['res.partner'].search([('user_id.login', '=', 'admin')])", "may not reach res.users"),
            ("result = env['res.partner'].search([], limit=1).message_ids", "may not reach mail.message"),
            ("result = user.action_id", "may not reach ir.actions.actions"),
            ("result = env.company.partner_id.user_ids", "may not reach res.users"),
            ("result = [p.user_id for p in env['res.partner'].search([], limit=1)]", "may not reach res.users"),
            ("def who(p):\n    return p.user_id.login\n"
             "result = env['res.partner'].search([]).mapped(who)", "may not reach res.users"),
        ]:
            result = self.run_code(code)
            self.assertTrue(result.is_error, code)
            self.assertIn(text, result.content, code)
        # The person themself is open to the code, as env.user always was
        self.assertEqual(self.payload("result = user.name == env.user.name")["result"], True)

    def test_what_shows_the_inside_is_refused(self):
        for code, text in [
            ("f = float_compare\nresult = ('{0._' + '_globals__}').format(f)", ".format"),
            ("result = '{x}'.format_map({'x': 1})", ".format_map"),
            ("result = f'{env!r}'", "!r"),
            ("result = f'{env!a}'", "!a"),
            ("result = '%r' % env", "%r"),
            ("a = '%s'\nresult = a % env", "between two names"),
            ("result = format(1, 'd')", "format"),
            ("try:\n    x = 1\nexcept:\n    pass", "bare except"),
            ("try:\n    x = 1\nexcept BaseException:\n    pass", "BaseException"),
        ]:
            result = self.run_code(code)
            self.assertTrue(result.is_error, code)
            self.assertIn(text, result.content, code)

    def test_the_deadline(self):
        refused = self.run_code("while True:\n    pass")
        self.assertTrue(refused.is_error)
        self.assertIn("No while", refused.content)
        refused = self.run_code("time.sleep(1)")
        self.assertTrue(refused.is_error)
        self.assertIn(".sleep", refused.content)

        previous = sys.gettrace()
        with patch.object(python_tool, "DEADLINE_SECONDS", 0.2):
            started = time.monotonic()
            result = self.run_code("x = 0\nfor i in range(10 ** 9):\n    x += i\nresult = x")
            self.assertTrue(result.is_error)
            self.assertIn("was stopped", result.content)
            self.assertLess(time.monotonic() - started, 5)
            # Catching Exception does not catch the stop
            result = self.run_code(
                "for i in range(10 ** 9):\n    try:\n        x = i\n    except Exception:\n        pass")
            self.assertTrue(result.is_error)
            self.assertIn("was stopped", result.content)
        self.assertIs(sys.gettrace(), previous, "The trace is gone once the code is")
        # The transaction goes on
        self.assertEqual(self.partners[0].name, "Nudge Py One")
        self.assertEqual(self.payload("result = 1 + 1")["result"], 2)

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
