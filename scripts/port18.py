"""Turn the Odoo 19 copy of Nudge into its Odoo 18 copy.

Run on a fresh copy of the 19 tree (binaries copied byte for byte):

    python port18.py "D:/odoo bootcamp/odoo-nudge-18"

Every change is a known difference between Odoo 18 and 19. The script is
idempotent: an edit already made is skipped.
"""
import io
import os
import re
import shutil
import sys

ROOT = sys.argv[1].rstrip("/\\") + "/"


def read(name):
    return io.open(ROOT + name, encoding="utf-8").read()


def write(name, s):
    io.open(ROOT + name, "w", encoding="utf-8", newline="\n").write(s)


def edit(name, pairs):
    if not os.path.exists(ROOT + name):
        return
    s = read(name)
    for old, new in pairs:
        # Already done: the new text is there (it may contain the old one)
        # Done already: the new text is there, or the text to delete is gone
        if (new and new in s) or (not new and old not in s):
            continue
        assert s.count(old) == 1, (name, s.count(old), old[:70])
        s = s.replace(old, new)
    write(name, s)
    print("ported", name)


def sub(name, pattern, replacement, flags=0):
    if not os.path.exists(ROOT + name):
        return 0
    s = read(name)
    s, n = re.subn(pattern, replacement, s, flags=flags)
    write(name, s)
    print("ported", name, n)
    return n


# ---------------------------------------------------------------- modules
if os.path.isdir(ROOT + "nudge_ai"):
    shutil.rmtree(ROOT + "nudge_ai")  # the AI app is Odoo 19 only
    print("dropped nudge_ai")
for module in ("nudge", "nudge_spreadsheet", "nudge_documents", "nudge_studio"):
    sub(f"{module}/__manifest__.py", r'"version": "19\.0\.', '"version": "18.0.')

# ---------------------------------------------------------------- SQL constraints (models.Constraint is 19)
edit("nudge/models/nudge_doc.py", [
    ('    _path_unique = models.Constraint("unique(path)", "A page of the documentation is loaded once.")\n',
     '    _sql_constraints = [("path_unique", "unique(path)", "A page of the documentation is loaded once.")]\n'),
])
edit("nudge/models/nudge_provider.py", [
    ('    _check_timeout = models.Constraint(\n'
     '        "CHECK(timeout > 0)", "The timeout must be strictly positive.")\n'
     '    _check_max_output_tokens = models.Constraint(\n'
     '        "CHECK(max_output_tokens >= 0)", "The maximum of output tokens cannot be negative.")\n',
     '    _sql_constraints = [\n'
     '        ("check_timeout", "CHECK(timeout > 0)", "The timeout must be strictly positive."),\n'
     '        ("check_max_output_tokens", "CHECK(max_output_tokens >= 0)",\n'
     '         "The maximum of output tokens cannot be negative."),\n'
     '    ]\n'),
])

# ---------------------------------------------------------------- domains (odoo.fields.Domain is 19)
edit("nudge/tools/records.py", [
    ("from odoo.fields import Domain\n", "from odoo.osv import expression\n"),
    ("        parts.append(Domain(path, OPERATORS[operator], value))\n"
     "    domain = Domain.OR(parts) if match == \"any\" and parts else Domain.AND(parts)\n",
     "        parts.append((path, OPERATORS[operator], value))\n"
     "    if not parts:\n"
     "        domain = []\n"
     "    elif match == \"any\":\n"
     "        domain = expression.OR([[part] for part in parts])\n"
     "    else:\n"
     "        domain = expression.AND([[part] for part in parts])\n"),
    ("        domain.validate(model)\n", "        expression.expression(domain, model)\n"),
])
edit("nudge/tools/finding.py", [
    ("from odoo.fields import Domain\n", "from odoo.osv import expression\n"),
    ("            parts.append(Domain(field_name, \"ilike\", text))\n"
     "    return Domain.OR(parts) if parts else None\n",
     "            parts.append((field_name, \"ilike\", text))\n"
     "    return expression.OR([[part] for part in parts]) if parts else None\n"),
])

# ---------------------------------------------------------------- field access (_has_field_access is 19)
edit("nudge/tools/records.py", [
    ("def get_fields(model, names, operation=\"read\"):\n",
     "def has_field_access(model, field, operation):\n"
     "    \"\"\"Odoo 18 has no _has_field_access: a field kept for some groups is\n"
     "    for the people of those groups\"\"\"\n"
     "    return not field.groups or model.env.user.has_groups(field.groups)\n"
     "\n\n"
     "def get_fields(model, names, operation=\"read\"):\n"),
])
for name in ("nudge/tools/records.py", "nudge/tools/actions.py", "nudge/tools/finding.py", "nudge/tools/developer.py",
             "nudge/tools/building.py", "nudge/tools/configuration.py", "nudge/tools/reports.py", "nudge/tools/importing.py"):
    if os.path.exists(ROOT + name):
        sub(name, r"model\._has_field_access\(", "has_field_access(model, ")
# The tools of Developer mode take the shim from records, like the others
for name in ("nudge/tools/developer.py", "nudge/tools/building.py", "nudge/tools/configuration.py",
             "nudge/tools/reports.py", "nudge/tools/importing.py"):
    if not os.path.exists(ROOT + name):
        continue
    s = read(name)
    if "has_field_access(model, " in s and "has_field_access" not in s.split("from . import base")[0] and "import has_field_access" not in s:
        write(name, s.replace("from . import base\n", "from . import base\nfrom .records import has_field_access  # noqa: E402\n", 1))
edit("nudge/tools/actions.py", [
    ("from odoo.service.model import call_kw, get_public_method\n",
     "from odoo.api import call_kw\nfrom odoo.service.model import get_public_method\n"),
])
s = read("nudge/tools/actions.py")
if "    has_field_access,\n" not in s and "from .records import (\n" in s:
    write("nudge/tools/actions.py", s.replace("from .records import (\n", "from .records import (\n    has_field_access,\n", 1))
s = read("nudge/tools/finding.py")
if "has_field_access, refusing" not in s:
    write("nudge/tools/finding.py", s.replace(
        "from .records import MODEL_PATTERN, Refused, cut, get_model, refusing\n",
        "from .records import MODEL_PATTERN, Refused, cut, get_model, has_field_access, refusing\n", 1))

# ---------------------------------------------------------------- locks (LockError, lock_for_update are 19)
edit("nudge/models/nudge_conversation.py", [
    ("from odoo.exceptions import AccessError, LockError, UserError\n",
     "from psycopg2 import errors as pg_errors\n\nfrom odoo.exceptions import AccessError, UserError\n"),
    ("            step.lock_for_update()\n        except LockError:\n",
     "            self.env.cr.execute(\"SELECT id FROM nudge_step WHERE id = %s FOR UPDATE NOWAIT\", (step.id,))\n"
     "        except pg_errors.LockNotAvailable:\n"),
])

# ---------------------------------------------------------------- the scheduled action (_commit_progress is 19)
edit("nudge/models/nudge_run.py", [
    ("            run = runs.try_lock_for_update(limit=1)\n",
     "            run = runs.browse()\n"
     "            if runs:\n"
     "                # Odoo 18 has no try_lock_for_update\n"
     "                self.env.cr.execute(\n"
     "                    \"SELECT id FROM nudge_run WHERE id IN %s ORDER BY write_date, id \"\n"
     "                    \"FOR UPDATE SKIP LOCKED LIMIT 1\", (tuple(runs.ids),))\n"
     "                row = self.env.cr.fetchone()\n"
     "                run = runs.browse(row[0]) if row else runs.browse()\n"),
    ("            left = Cron._commit_progress(1, remaining=max(len(runs) - 1, 0))\n",
     "            left = self._nudge_commit_progress(started)\n"),
    ("        Cron = self.env[\"ir.cron\"]\n        seconds = self._get_piece_seconds()\n",
     "        started = time.monotonic()\n        seconds = self._get_piece_seconds()\n"),
    ("    @api.model\n    def _cron_work(self):\n",
     "    @api.model\n"
     "    def _nudge_commit_progress(self, started):\n"
     "        \"\"\"Odoo 18 has no ir.cron._commit_progress: commit what was done and\n"
     "        say how many seconds the scheduled action has left\"\"\"\n"
     "        self.env.cr.commit()\n"
     "        limit = config.get(\"limit_time_real_cron\") or -1\n"
     "        if limit <= 0:\n"
     "            limit = config.get(\"limit_time_real\") or 120\n"
     "        return limit - (time.monotonic() - started)\n"
     "\n"
     "    @api.model\n    def _cron_work(self):\n"),
])
edit("nudge/tests/test_background.py", [
    ('        self.patch(type(self.env["ir.cron"]), "_commit_progress", lambda cron, *args, **kwargs: 10 ** 6)\n',
     '        self.patch(type(self.Run), "_nudge_commit_progress", lambda run, started: 10 ** 6)\n'),
    ('        self.patch(type(self.env["ir.cron"]), "_commit_progress", lambda cron, *args, **kwargs: 5)\n',
     '        self.patch(type(self.Run), "_nudge_commit_progress", lambda run, started: 5)\n'),
])

# ---------------------------------------------------------------- res.groups in 18
edit("nudge/profile/collectors.py", [
    ("    groups = base_user.sudo().all_implied_ids - base_user\n",
     "    groups = base_user.sudo().trans_implied_ids - base_user\n"),
    ('"users": len(group.all_user_ids)', '"users": len(group.users)'),
])
edit("nudge/security/nudge_security.xml", [
    ('    <record id="res_groups_privilege_nudge" model="res.groups.privilege">\n'
     '        <field name="name">Nudge</field>\n'
     '        <field name="category_id" ref="module_category_nudge"/>\n'
     '    </record>\n\n', ''),
])
sub("nudge/security/nudge_security.xml",
    r'<field name="privilege_id" ref="res_groups_privilege_nudge"/>',
    '<field name="category_id" ref="module_category_nudge"/>')
sub("nudge/security/nudge_security.xml",
    r'(<record id="group_[a-z_]+" model="res\.groups">\n(?:(?!</record>).)*?)        <field name="sequence">\d+</field>\n',
    r"\1", flags=re.DOTALL)
sub("nudge/security/nudge_security.xml", r'<field name="user_ids"', '<field name="users"')
sub("nudge/security/nudge_security.xml", r"user\.all_group_ids\.ids", "user.groups_id.ids")

# ---------------------------------------------------------------- safe_eval copies its globals in 18
edit("nudge/tools/python.py", [
    ('safe_eval(code, context, mode="exec", filename="nudge:run_python")',
     'safe_eval(code, context, mode="exec", nocopy=True, filename="nudge:run_python")'),
])

# ---------------------------------------------------------------- the tests: names of 18
edit("nudge/tests/test_agent.py", [
    ('"Press \u201cCreate\u201d: Contact"', '"Press \u201cCreate company\u201d: Contact"'),
    ('"create_company (Create)"', '"create_company (Create company)"'),
    ("\"Pressed 'Create'\"", "\"Pressed 'Create company'\""),
])
sub("nudge/tests/test_profile.py", r"Odoo 19\.0 Community", "Odoo 18.0 Community")

# ---------------------------------------------------------------- Hoot tests: the web client of 18
# A notification shows its title apart from its message
edit("nudge/static/tests/background.test.js", [
    ('    expect(".o_notification_content").toHaveText(/Nudge needs your answer/);\n',
     '    expect(".o_notification").toHaveText(/Nudge needs your answer/);\n'),
    ('    expect(".o_notification_content").toHaveText(/Nudge could not finish/);\n',
     '    expect(".o_notification").toHaveText(/Nudge could not finish/);\n'),
])
# edit() types into the element that has the focus
edit("nudge/static/tests/agent.test.js", [
    ('    await edit("In our own payroll module");\n',
     '    await click(".o_nudge_rate_note");\n    await edit("In our own payroll module");\n'),
])
for name in ("nudge/tests/test_agent.py", "nudge/tests/test_background.py", "nudge/tests/test_injection.py",
             "nudge/tests/test_menu_path.py", "nudge/tests/test_sources.py", "nudge/tests/test_tools.py",
             "nudge/tests/test_admin.py", "nudge_documents/tests/test_documents.py"):
    if os.path.exists(ROOT + name):
        s = read(name)
        # the groups of users and menus; the groups of a company file keep their name
        s = re.sub(r"\.group_ids\b", ".groups_id", s)
        s = s.replace('"group_ids": [(4, self.env.ref("base.group_system").id)],',
                      '"groups_id": [(4, self.env.ref("base.group_system").id)],')
        s = s.replace('self.user_a.write({"group_ids": [(4, hr.id)]})', 'self.user_a.write({"groups_id": [(4, hr.id)]})')
        s = s.replace('cls.agent.write({"group_ids":', 'cls.agent.write({"groups_id":')
        s = s.replace('user.write({"group_ids":', 'user.write({"groups_id":')
        s = s.replace("values(group_ids=str(group.id))", "values(groups_id=str(group.id))")
        write(name, s)
        print("ported", name)

# ---------------------------------------------------------------- spreadsheet data of 18 (o-spreadsheet 21)
# Cells are {"content": ...} objects; a figure sits at x/y pixels, not at a cell
edit("nudge_spreadsheet/tools/workbook.py", [
    ('        cells[f"{column_letter(index)}1"] = str(column["label"])\n',
     '        cells[f"{column_letter(index)}1"] = {"content": str(column["label"])}\n'),
    ("            cells[reference] = content\n", "            cells[reference] = {\"content\": content}\n"),
    ('                cells[reference] = f"=SUM({letter}2:{letter}{last_row})"\n',
     '                cells[reference] = {"content": f"=SUM({letter}2:{letter}{last_row})"}\n'),
    ('                cells[reference] = "Total"\n', '                cells[reference] = {"content": "Total"}\n'),
    ('        "col": column_count + 1,\n        "row": 0,\n        "offset": {"x": 0, "y": 0},\n',
     '        "x": (column_count + 1) * COLUMN_WIDTH,\n        "y": 0,\n'),
    ('    workbook["uniqueFigureIds"] = True\n',
     '    workbook["uniqueFigureIds"] = True\n'
     '    # The version of the data Odoo 18 writes, which its editor reads as it is\n'
     '    workbook["version"] = 21\n'
     '    workbook.setdefault("odooVersion", 12)\n'
     '    for section in ("pivots", "lists"):\n'
     '        workbook.setdefault(section, {})\n'
     '    workbook.setdefault("globalFilters", [])\n'),
])
for name in ("nudge_spreadsheet/tests/test_spreadsheet.py", "nudge_documents/tests/test_documents.py"):
    sub(name, r'cells\["([A-Z]+[0-9]+)"\](?!\[)', r'cells["\1"]["content"]')
    sub(name, r'\["cells"\]\["([A-Z]+[0-9]+)"\](?!\[)', r'["cells"]["\1"]["content"]')
edit("nudge_spreadsheet/tests/test_spreadsheet.py", [
    ('        self.assertEqual(figure["col"], 5)\n', '        self.assertEqual(figure["x"], 5 * workbook.COLUMN_WIDTH)\n'),
    ('        self.assertEqual(data.get("version"), empty.get("version"), "What the empty workbook carries is kept")\n',
     '        self.assertEqual(data["version"], 21)\n'),
])

# ---------------------------------------------------------------- Odoo 18 checks that no model keeps a patch after a test
edit("nudge/tests/test_developer.py", [
    ("    def switch(self, on):\n        self.Parameter.set_param(DEVELOPER_PARAM, \"yes\" if on else \"no\")\n",
     "    def setUp(self):\n"
     "        super().setUp()\n"
     "        if self.has_rules:\n"
     "            # A rule made in a test patches the model; Odoo 18 wants it clean afterwards\n"
     "            self.addCleanup(self.env[\"base.automation\"]._unregister_hook)\n"
     "\n"
     "    def switch(self, on):\n        self.Parameter.set_param(DEVELOPER_PARAM, \"yes\" if on else \"no\")\n"),
])

# ---------------------------------------------------------------- no AI app in 18: the store page says nothing of it
sub("nudge/static/description/index.html",
    r"^[ \t]*<li>[^\n]*Nudge: Odoo AI[^\n]*</li>\n", "", flags=re.M)

# ---------------------------------------------------------------- the words "Odoo 19"
for name in ("nudge/llm/prompt.py", "nudge/README.md", "nudge/README.ar.md", "nudge/static/description/index.html",
             "nudge/doc/user-guide.md", "nudge/doc/user-guide.ar.md", "nudge/doc/security.md", "nudge/__manifest__.py"):
    sub(name, r"Odoo 19(\.0)?", "Odoo 18")
print("done")
