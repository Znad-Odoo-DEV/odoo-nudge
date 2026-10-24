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

    def test_one_step_too_large_for_the_deadline_is_refused(self):
        # The deadline is looked at between two steps: one step in C is bounded before it runs
        for code, text in [
            ("result = 10 ** 10 ** 10", "10 ** 10 is beyond 10 ** 9"),
            ("result = 'Raghad' * 10 ** 10", "beyond 10 ** 9"),
            ("result = sum(range(10 ** 12))", "10 ** 12 is beyond 10 ** 9"),
            ("result = [0] * 10 ** 10", "beyond 10 ** 9"),
            ("result = 2 ** 100", "2 ** 100 is beyond 10 ** 9"),
            ("result = 10 ** 6 * 10 ** 6", "beyond 10 ** 9"),
            ("result = 1 << 100", "beyond 10 ** 9"),
            ("result = 12345678901", "12345678901 is beyond 10 ** 9"),
            ("n = 3\nresult = n ** n", "** and <<"),
            ("n = 3\nn **= n", "** and <<"),
            ("n = 3\nresult = 2 << n", "** and <<"),
            ("n = 3\nresult = 'Ebrahim' * n", "Repeat a text or a list"),
            ("result = ['Raghad', 'Ebrahim'] * 10 ** 6", "Repeat a text or a list"),
            ("result = ('Raghad' * 10 ** 4) * 10 ** 4", "Repeat a text or a list"),
        ]:
            result = self.run_code(code)
            self.assertTrue(result.is_error, code)
            self.assertIn(text, result.content, code)

    def test_a_step_on_computed_values_is_refused_before_it_runs(self):
        # x * x in a loop, s + s, l.extend(l)...: each step is checked as it runs, none runs past the deadline
        huge, long = "beyond 2 ** 262144", "more than 1000000 items"
        for code, text in [
            ("x = 10 ** 9\nfor i in range(40):\n    x = x * x", huge),
            ("x = 10 ** 9\nfor i in range(40):\n    x *= x", huge),
            ("x = 3\nfor i in range(10 ** 6):\n    x = x + x", huge),
            ("x = 3\nfor i in range(5000):\n    x = x << 64", huge),
            ("x = 10 ** 9\nfor i in range(100):\n    x = x ** 3", huge),
            ("x = 10 ** 9\nfor i in range(40):\n    x = x * x\n    y = x - 1", huge),
            ("s = 'Raghad'\nfor i in range(40):\n    s = s + s", long),
            ("s = 'Raghad'\nfor i in range(40):\n    s += s", long),
            ("s = 'Raghad'\nfor i in range(40):\n    s = s * 2", long),
            ("l = [1, 2]\nfor i in range(40):\n    l += l", long),
            ("l = [1, 2]\nfor i in range(40):\n    l.extend(l)", long),
            ("l = [1, 2]\nfor i in range(40):\n    l = l + l", long),
            ("s = 'x' * 90000\nresult = len(', '.join([s] * 90000))", long),
            ("s = 'Ebrahim'\nfor i in range(40):\n    s = '%s%s' % (s, s)", long),
            ("s = 'Ebrahim'\nfor i in range(40):\n    s = f'{s}{s}'", long),
            ("result = 'a'.ljust(10 ** 9)", long),
            ("result = 'a'.zfill(10 ** 8)", long),
            ("s = 'a' * 90000\nresult = len(s.replace('a', 'b' * 100))", long),
            ("result = len(bytes(10 ** 9))", long),
            ("result = (10 ** 9).to_bytes(10 ** 9, 'big')", long),
            ("n = 5\nresult = f'{n:{10 ** 9}}'", "beyond 999999"),
            ("result = '%999999999d' % 1", "beyond 999999"),
            ("result = '%*d' % (10 ** 9, 1)", "beyond 999999"),
            ("l = [[1] * 50000] * 90\nresult = len(sum(l, []))", long),
            ("l = [1]\nl[0:0] = l", "No assignment to a slice"),
            ("f = 'a'.ljust\nresult = f(10 ** 9)", "Call .ljust()"),
            ("f = bytes\nresult = f(10 ** 9)", "bytes is called"),
            ("_nudge_mul = max\nresult = 1", "may not use _nudge_mul"),
        ]:
            started = time.monotonic()
            result = self.run_code(code)
            # An escape ran for minutes; 5 s leaves room for a loaded test machine
            self.assertLess(time.monotonic() - started, 5, code)
            self.assertTrue(result.is_error, code)
            self.assertIn(text, result.content, code)
            self.assertNotIn("was stopped", result.content, code)

    def test_rounding_to_a_huge_power_of_ten_is_not_computed(self):
        started = time.monotonic()
        payload = self.payload("result = [round(12345, -10 ** 9), round(12345, -2), round(2.5), round(1.2345, 2)]")
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(payload["result"], [0, 12300, 2, 1.23])

    def test_the_memory_the_code_holds_is_bounded(self):
        # Many steps that are each small: the memory is looked at with the deadline
        with patch.object(python_tool, "MAX_MEMORY", 20 * 1024 * 1024), \
                patch.object(python_tool, "MEMORY_CHECK", 0.0):
            started = time.monotonic()
            result = self.run_code("keep = []\nfor i in range(10 ** 6):\n    keep.append([0] * 100000)")
            self.assertTrue(result.is_error)
            self.assertIn("of memory and was stopped", result.content)
            self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(self.payload("result = 1 + 1")["result"], 2)

    def test_arithmetic_on_computed_values_still_works(self):
        payload = self.payload(
            "a = 123456789\nb = 987654321\n"
            "total = 0\nfor i in range(1, 101):\n    total += i * a - b\n"
            "names = ['Raghad', 'Ebrahim']\nn = len(names)\ndash = '-'\nrows = names * 2\nrows += names\n"
            "text = ', '.join(rows)\nline = dash * n + '|'\nwords = 'Raghad and Ebrahim'.replace('and', '&')\n"
            "square = a * a\ncube = n ** 3\nshift = n << 4\nbig = a * b * 1000\n"
            "ids = [1, 2]\nids.extend([3, 4])\nmerged = sum([[1], [2, 3]], [])\n"
            "result = {'total': total, 'rows': len(rows), 'text': text, 'line': line, 'words': words,\n"
            "          'square': square, 'cube': cube, 'shift': shift, 'big': big, 'ids': ids,\n"
            "          'merged': merged, 'pct': '%s is %d%%' % (names[0], 50), 'width': f'{n:*>4}|{a:,}',\n"
            "          'padded': 'Ebrahim'.ljust(10, '.') + str(n).zfill(3), 'neg': -a - -b, 'f': 1.5 * a,\n"
            "          'bytes': len(bytes(5)), 'isbytes': isinstance(b'x', bytes), 'sum': sum([1.5, 2.5])}\n")
        self.assertEqual(payload["result"], {
            "total": 5050 * 123456789 - 100 * 987654321, "rows": 6,
            "text": "Raghad, Ebrahim, Raghad, Ebrahim, Raghad, Ebrahim", "line": "--|",
            "words": "Raghad & Ebrahim", "square": 123456789 ** 2, "cube": 8, "shift": 32,
            "big": 123456789 * 987654321 * 1000, "ids": [1, 2, 3, 4], "merged": [1, 2, 3], "pct": "Raghad is 50%",
            "width": "***2|123,456,789", "padded": "Ebrahim...002", "neg": 864197532,
            "f": 185185183.5, "bytes": 5, "isbytes": True, "sum": 4.0})

    def test_a_long_range_is_walked_where_the_deadline_sees_it(self):
        # sum() walks a range in C, to its end: a long one is walked step by step instead
        with patch.object(python_tool, "DEADLINE_SECONDS", 0.2):
            started = time.monotonic()
            result = self.run_code("n = 10 ** 5\nresult = sum(range(n * n))")
            self.assertTrue(result.is_error)
            self.assertIn("was stopped", result.content)
            self.assertLess(time.monotonic() - started, 5)

    def test_small_arithmetic_is_computed(self):
        payload = self.payload(
            "names = ['Raghad', 'Ebrahim'] * 2\n"
            "x = len(names)\n"
            "x **= 2\n"
            "result = {'names': names, 'square': 7 ** 2, 'root': 16 ** 0.5, 'x': x, 'cube': x ** 3,\n"
            "          'line': '-' * 10, 'zeros': [0] * 3, 'million': 10 ** 6, 'amount': 25000000000.0 * 2,\n"
            "          'flag': 1 << 8, 'sum': sum(range(101)), 'long': sum(range(1000001))}\n")
        self.assertEqual(payload["result"], {
            "names": ["Raghad", "Ebrahim", "Raghad", "Ebrahim"], "square": 49, "root": 4.0, "x": 16,
            "cube": 4096, "line": "----------", "zeros": [0, 0, 0], "million": 1000000,
            "amount": 50000000000.0, "flag": 256, "sum": 5050, "long": 500000500000,
        })

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
