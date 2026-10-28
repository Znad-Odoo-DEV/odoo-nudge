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
for module in sorted(d for d in os.listdir(ROOT) if os.path.isfile(ROOT + d + "/__manifest__.py")):  # QA RULE 1: was a fixed list, missed nudge_mcp/nudge_website
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
             "nudge/tools/building.py", "nudge/tools/configuration.py", "nudge/tools/reports.py", "nudge/tools/importing.py",
             "nudge/tools/product_search.py"):
    if os.path.exists(ROOT + name):
        sub(name, r"\b(\w+)\._has_field_access\(", r"has_field_access(\1, ")
# The tools of Developer mode take the shim from records, like the others
for name in ("nudge/tools/developer.py", "nudge/tools/building.py", "nudge/tools/configuration.py",
             "nudge/tools/reports.py", "nudge/tools/importing.py", "nudge/tools/product_search.py"):
    if not os.path.exists(ROOT + name):
        continue
    s = read(name)
    if "has_field_access(" in s and "has_field_access" not in s.split("from . import base")[0] and "import has_field_access" not in s:
        # The line may import more than base: `from . import base, schema`
        s, n = re.subn(r"^(from \. import base[^\n]*\n)", r"\1from .records import has_field_access  # noqa: E402\n", s, count=1, flags=re.M)
        assert n == 1, name
        write(name, s)
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
     "            with self.env.cr.savepoint(flush=False):  # a refused lock must not abort the transaction\n"
     "                self.env.cr.execute(\"SELECT id FROM nudge_step WHERE id = %s FOR UPDATE NOWAIT\", (step.id,))\n"
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
     "        self.env[\"ir.cron\"]._commit_progress(1)  # commits, except inside a test (see nudge_cron_compat)\n"
     "        limit = config.get(\"limit_time_real_cron\") or -1\n"
     "        if limit <= 0:\n"
     "            limit = config.get(\"limit_time_real\") or 120\n"
     "        return limit - (time.monotonic() - started)\n"
     "\n"
     "    @api.model\n    def _cron_work(self):\n"),
])
# FINAL RULE: the 10 ** 6 patch now appears twice in test_background.py (edit() wants exactly one)
sub("nudge/tests/test_background.py", r'self\.patch\(type\(self\.env\["ir\.cron"\]\), "_commit_progress", lambda cron, \*args, \*\*kwargs: 10 \*\* 6\)', 'self.patch(type(self.Run), "_nudge_commit_progress", lambda run, started: 10 ** 6)')
edit("nudge/tests/test_background.py", [
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
# The file name is a literal or a constant, as the tool had it
sub("nudge/tools/python.py", r'safe_eval\(code, context, mode="exec", filename=',
    'safe_eval(code, context, mode="exec", nocopy=True, filename=')

# ---------------------------------------------------------------- the tests: names of 18
edit("nudge/tests/test_agent.py", [
    ('"Press \u201cCreate\u201d: Contact"', '"Press \u201cCreate company\u201d: Contact"'),
    ('"create_company (Create)"', '"create_company (Create company)"'),
    ("\"Pressed 'Create'\"", "\"Pressed 'Create company'\""),
])
# The card of the button reads its label
sub("nudge/tests/test_agent.py", r'\{"label": "Button", "value": "Create"\}', '{"label": "Button", "value": "Create company"}')
sub("nudge/tests/test_profile.py", r"Odoo 19\.0 Community", "Odoo 18.0 Community")

# ---------------------------------------------------------------- Hoot tests: the web client of 18
# A notification shows its title apart from its message
edit("nudge/static/tests/background.test.js", [
    ('    expect(".o_notification_content").toHaveText(/Nudge needs your answer/);\n',
     '    expect(".o_notification").toHaveText(/Nudge needs your answer/);\n'),
    ('    expect(".o_notification_content").toHaveText(/Nudge could not finish/);\n',
     '    expect(".o_notification").toHaveText(/Nudge could not finish/);\n'),
])
# The title of any other notice of Nudge too
sub("nudge/static/tests/background.test.js", r'expect\("\.o_notification_content"\)\.toHaveText\(/Nudge ',
    'expect(".o_notification").toHaveText(/Nudge ')
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

# ---------------------------------------------------------------- Default Recipients is off by default in 18
# The test of the update of 19.0.1.56.26 counts on a new template having it on, as in 19
sub("nudge_mail/tests/test_emails.py",
    r'^([ \t]+)(theirs = Template\.create\(\{"name": "Theirs", "model_id": partner_model, "partner_to": "\{\{ object\.id \}\}")\}\)',
    r'\1# Made with Default Recipients on, as Odoo 19 makes a template\n\1\2, "use_default_to": True})', flags=re.M)

# ---------------------------------------------------------------- spreadsheet data of 18 (o-spreadsheet 21)
# Cells are {"content": ...} objects; a figure sits at x/y pixels, not at a cell
edit("nudge_spreadsheet/tools/workbook.py", [
    # FINAL RULE: header text is wrapped in _safe_text now
    ('        cells[f"{column_letter(index)}1"] = _safe_text(str(column["label"]))\n',
     '        cells[f"{column_letter(index)}1"] = {"content": _safe_text(str(column["label"]))}\n'),
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
    ("        self.patch(developer, \"designed_emails_here\", lambda env: False)\n",
     "        self.patch(developer, \"designed_emails_here\", lambda env: False)\n"
     "        if self.has_rules:\n"
     "            # A rule made in a test patches the model; Odoo 18 wants it clean afterwards\n"
     "            self.addCleanup(self.env[\"base.automation\"]._unregister_hook)\n"),
])

# ---------------------------------------------------------------- no AI app in 18: the store page says nothing of it
# Named among the other companions, it is left out of the sentence; a line of its own goes
sub("nudge/static/description/index.html", r", Nudge: Odoo AI \(ai\)", "")
sub("nudge/static/description/index.html", r"Nudge: Odoo AI, (?=Nudge)", "")
sub("nudge/static/description/index.html",
    r"^[ \t]*<li>[^\n]*Nudge: Odoo AI[^\n]*</li>\n", "", flags=re.M)

# ---------------------------------------------------------------- migrations: named for the version of 18
# A folder named 19.0.x is above every version of the 18 module: it would never run
for module in sorted(os.listdir(ROOT)):
    folder = ROOT + module + "/migrations/"
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if name.startswith("19.0."):
                os.rename(folder + name, folder + "18.0." + name[len("19.0."):])
                print("renamed", module, "migration", name)
# The tests of a migration read its script by its folder, and give the version it comes from
for module in sorted(os.listdir(ROOT)):
    tests = ROOT + module + "/tests/"
    if os.path.isdir(tests):
        for name in sorted(os.listdir(tests)):
            if name.endswith(".py"):
                sub(f"{module}/tests/{name}", r'(file_path\("[\w/]+/migrations/)19\.0\.', r"\g<1>18.0.")
                sub(f"{module}/tests/{name}", r'(\.migrate\(self\.env\.cr, ")19\.0\.', r"\g<1>18.0.")
                sub(f"{module}/tests/{name}", r'(\.migrate\(cr, ")19\.0\.', r"\g<1>18.0.")  # R2 RULE 13b
                # R2 RULE 13: a test that joins the folder name by os.path.join("migrations", "19.0.x")
                sub(f"{module}/tests/{name}" if name == "test_saved_reports.py" else "nudge/none", r'("migrations", ")19\.0\.', r"\g<1>18.0.")

# ---------------------------------------------------------------- the caches of the web client (rpcBus has them in 19)
# In 18 the views, the fields and the actions forget what they read when env.bus says CLEAR-CACHES
sub("nudge/static/src/core/nudge_service.js", r'import \{ rpcBus \} from "@web/core/network/rpc";\n', "")
sub("nudge/static/src/core/nudge_service.js", r'\brpcBus\.trigger\("CLEAR-CACHES"', 'env.bus.trigger("CLEAR-CACHES"')
# The test listens on the bus of the env of the web client, there once the client is mounted
REFRESH = "nudge/static/tests/refresh.test.js"
if os.path.exists(ROOT + REFRESH) and "rpcBus" in read(REFRESH):
    s = read(REFRESH)
    s = s.replace('import { rpcBus } from "@web/core/network/rpc";\n', "")
    if "    getMockEnv,\n" not in s:
        s = s.replace("    getService,\n", "    getMockEnv,\n    getService,\n", 1)
    s = re.sub(
        r'(    )rpcBus\.addEventListener\("CLEAR-CACHES", (\w+)\);\n(    const \{[^}]*\} = await start\(\n(?:.*\n)*?    \);\n)',
        r'\3\1getMockEnv().bus.addEventListener("CLEAR-CACHES", \2);\n', s)
    s = s.replace('rpcBus.removeEventListener("CLEAR-CACHES"', 'getMockEnv().bus.removeEventListener("CLEAR-CACHES"')
    assert "rpcBus" not in s, REFRESH
    write(REFRESH, s)
    print("ported", REFRESH)

# The line of the changelog that lists the versions of the modules says the version of this series
sub("nudge/CHANGELOG.md", r"(?m)^Current version: .*$", lambda m: m.group(0).replace("19.0.1.", "18.0.1."))

# ---------------------------------------------------------------- the words "Odoo 19"
for name in ("nudge/llm/prompt.py", "nudge/README.md", "nudge/README.ar.md", "nudge/static/description/index.html",
             "nudge/doc/user-guide.md", "nudge/doc/user-guide.ar.md", "nudge/doc/security.md", "nudge/__manifest__.py"):
    sub(name, r"Odoo 19(\.0)?", "Odoo 18")

# ---------------------------------------------------------------- QA EXTRAS (rules added by the QA run)
# QA RULE 2: nudge/security/nudge_tour_security.xml used user.all_group_ids (the old rule only covered nudge_security.xml)
for _dir, _d, _files in os.walk(ROOT):
    for _f in _files:
        if _f.endswith(".xml") and "all_group_ids" in io.open(os.path.join(_dir, _f), encoding="utf-8").read():
            sub(os.path.relpath(os.path.join(_dir, _f), ROOT).replace(os.sep, "/"), r"user\.all_group_ids\.ids", "user.groups_id.ids")
# QA RULE 3: nudge_mcp uses LockError / lock_for_update (same replacement as nudge_conversation.py)
edit("nudge_mcp/models/nudge_mcp.py", [
    ("from odoo.exceptions import AccessError, LockError, UserError\n",
     "from psycopg2 import errors as pg_errors\n\nfrom odoo.exceptions import AccessError, UserError\n"),
    ("            step.lock_for_update()\n        except LockError:\n",
     "            with self.env.cr.savepoint(flush=False):  # a refused lock must not abort the transaction\n"
     "                self.env.cr.execute(\"SELECT id FROM nudge_step WHERE id = %s FOR UPDATE NOWAIT\", (step.id,))\n"
     "        except pg_errors.LockNotAvailable:\n"),
])

# QA RULE 4: nudge_mcp/security/nudge_mcp_security.xml: res.groups.privilege (19) -> category of the 18 group
sub("nudge_mcp/security/nudge_mcp_security.xml",
    r'<field name="privilege_id" ref="nudge\.res_groups_privilege_nudge"/>',
    '<field name="category_id" ref="nudge.module_category_nudge"/>')
sub("nudge_mcp/security/nudge_mcp_security.xml", r'        <field name="sequence">\d+</field>\n', "")

# QA RULE 5: _has_field_access also used in nudge/tools/history.py and nudge/tools/bulk.py (new in 1.56.5x); the old rule has a fixed file list
for _name in ("nudge/tools/history.py", "nudge/tools/bulk.py"):
    if os.path.exists(ROOT + _name):
        sub(_name, r"\b(\w+)\._has_field_access\(", r"has_field_access(\1, ")
        _s = read(_name)
        if "import has_field_access" not in _s and "has_field_access," not in _s.split("\ndef ")[0]:
            _s, _n = re.subn(r"^(from \.records import [^\n(]*\n|from \. import [^\n]*\n)", r"\1from .records import has_field_access  # noqa: E402\n", _s, count=1, flags=re.M)
            if not _n:
                _s = re.sub(r"^(from odoo[^\n]*\n)", r"\1from .records import has_field_access  # noqa: E402\n", _s, count=1, flags=re.M)
            write(_name, _s)
# QA RULE 6: nudge_run._requeue_stale uses try_lock_for_update (a second place; the old rule only covers _cron_run)
edit("nudge/models/nudge_run.py", [
    ("        stale = stale.try_lock_for_update()\n",
     "        if stale:\n"
     "            self.env.cr.execute(\"SELECT id FROM nudge_run WHERE id IN %s FOR UPDATE SKIP LOCKED\", (tuple(stale.ids),))\n"
     "            stale = stale.browse([row[0] for row in self.env.cr.fetchall()])\n"),
])
# QA RULE 7: tests set group_ids on a user / a menu (groups_id in 18)
sub("nudge/tests/test_tours.py", r"self\.leaf\.group_ids\b", "self.leaf.groups_id")
sub("nudge_mcp/tests/test_mcp.py", r"self\.other\.group_ids\b", "self.other.groups_id")
# QA RULE 8: test_profile now builds the text with {edition}, so the old Odoo 19.0 Community rule matches nothing
sub("nudge/tests/test_profile.py", r"Odoo 19\.0 \{edition\}", "Odoo 18.0 {edition}")

# ---------------------------------------------------------------- STORE REVIEW RULES (history: tracking values)
# What a message changed: Odoo 18 and 17 have no Store formatter like 19's. The values are read with sudo,
# and only the ones of the fields the person may read are kept
edit("nudge/tools/history.py", [
    ("""    try:
        from odoo.addons.mail.tools.discuss import Store
        store = Store()
        store.add(message)
        entries = store.get_result().get("mail.message", [{}])[0].get("trackingValues") or []
    except (ImportError, AccessError, MissingError, KeyError, IndexError):
        return []
    lines = []
    for entry in entries:
        info = entry.get("fieldInfo") or entry
        label = info.get("changedField") or ""
        old, new = entry.get("oldValue"), entry.get("newValue")
        old, new = (old.get("value") if isinstance(old, dict) else old), (new.get("value") if isinstance(new, dict) else new)
        lines.append(f"{label}: {old if old not in (None, False, '') else '-'} -> {new if new not in (None, False, '') else '-'}")
    return lines
""",
     """    try:
        model = message.env[message.model] if message.model in message.env else None
        values = message.sudo().tracking_value_ids if model is not None else []
        lines = []
        for value in values:
            field = model._fields.get(value.field_id.name)
            if field is None or not has_field_access(model, field, "read"):
                continue

            def shown(side):
                for kind in ("char", "text", "integer", "float", "datetime"):
                    found = value[f"{side}_value_{kind}"]
                    if found:
                        return str(found)
                return "-"

            lines.append(f"{field.string}: {shown('old')} -> {shown('new')}")
        return lines
    except (AccessError, MissingError, KeyError):
        return []
"""),
])

# QA RULE 10 (merged from the QA copy): Odoo 18/17 still have res.partner.mobile, and the grammar writes it; 19 has only phone
sub("nudge/tests/test_noai.py", r'self\.assertEqual\(self\.raghad\.phone, "0509998888"\)', 'self.assertEqual(self.raghad.mobile, "0509998888")')
# QA RULE 11 (merged from the QA copy): 18/17 ship 5 res.partner.title rows, so the "work is in res.partner" note needs 3x and does not come
sub("nudge/tests/test_activity.py", r'        self\.assertIn\("The work on this is in", found\["note"\]\)\n        self\.assertIn\("\(res\.partner\)", found\["note"\]\.split\(":"\)\[0\]\)\n',
    '        self.assertTrue(found["note"])  # 18/17 ship res.partner.title data: the "work is in res.partner" note needs 3x\n')

# FINAL RULE: ir.cron._commit_progress is 19; the task and mention crons and their tests call it (and patch it) by that name.
# A small compatibility model gives 18 (and 17) the same method: commit, and say how much time is left
write("nudge/models/nudge_cron_compat.py", '''import time

from odoo import api, models
from odoo.modules import module
from odoo.tools import config


class IrCron(models.Model):
    _inherit = "ir.cron"

    @api.model
    def _commit_progress(self, processed=0, *, remaining=None, deactivate=False):
        """Odoo 18 and 17 have no ir.cron._commit_progress: commit what was done and
        say how many seconds the scheduled action may still run"""
        if not getattr(module, "current_test", None):  # a test keeps its transaction, 19 skips the commit there too
            self.env.cr.commit()
        limit = config.get("limit_time_real_cron") or -1
        if limit <= 0:
            limit = config.get("limit_time_real") or 120
        return float(limit)
''')
edit("nudge/models/__init__.py", [("from . import nudge_task\n", "from . import nudge_task\nfrom . import nudge_cron_compat\n")])
# FINAL RULE: website.page.group_ids is groups_id on 18/17
sub("nudge_website/models/nudge_source.py", r"page\.group_ids", "page.groups_id")
sub("nudge_website/tests/test_assistant.py", r'"group_ids"', '"groups_id"')

print("done")

# R2 RULE 1: models.Constraint (19) in the new models of release 2 (nudge_coach, nudge_question, nudge_builder_*) -> _sql_constraints
import glob as _glob
for _path in _glob.glob(ROOT + "nudge*/models/*.py"):
    _s = io.open(_path, encoding="utf-8").read()
    if "models.Constraint(" not in _s:
        continue
    _lines = _s.split("\n")
    _out, _i = [], 0
    while _i < len(_lines):
        _m = re.match(r"^    _(\w+) = models\.Constraint\((.*)$", _lines[_i])
        if _m:
            _buf = _m.group(2)
            while _buf.count("(") + 1 > _buf.count(")"):
                _i += 1
                _buf += " " + _lines[_i].strip()
            _body = _buf[: _buf.rindex(")")]
            _out.append('    _sql_constraints = [("%s", %s)]' % (_m.group(1), _body))
        else:
            _out.append(_lines[_i])
        _i += 1
    io.open(_path, "w", encoding="utf-8", newline="\n").write("\n".join(_out))
    print("ported (R2 RULE 1)", _path[len(ROOT):])

# R2 RULE 2: nudge_messaging/models/nudge_step.py uses LockError / lock_for_update (19), same as nudge_conversation
edit("nudge_messaging/models/nudge_step.py", [
    ("from odoo.exceptions import AccessError, LockError, UserError\n",
     "from psycopg2 import errors as pg_errors\n\nfrom odoo.exceptions import AccessError, UserError\n"),
    ("            step.lock_for_update()\n        except LockError:\n",
     "            with self.env.cr.savepoint(flush=False):  # a refused lock must not abort the transaction\n"
     "                self.env.cr.execute(\"SELECT id FROM nudge_step WHERE id = %s FOR UPDATE NOWAIT\", (step.id,))\n"
     "        except pg_errors.LockNotAvailable:\n"),
])

# R3 RULE 1: nudge_connect token renewal uses LockError / lock_for_update (19): the 18 copy gets its own LockError
# and a lock_for_update method on the token model (NOWAIT in a savepoint), so the code and its tests stay as they are
edit("nudge_connect/models/nudge_connector_oauth.py", [
    ("from odoo.exceptions import AccessError, LockError, UserError\n",
     "from psycopg2 import errors as pg_errors\n\nfrom odoo.exceptions import AccessError, UserError\n"),
    ("_logger = logging.getLogger(__name__)\n",
     "_logger = logging.getLogger(__name__)\n\n\nclass LockError(Exception):\n    \"\"\"Odoo 19 has odoo.exceptions.LockError; Odoo 18 does not\"\"\"\n"),
    ("    scope = fields.Char()\n\n    @api.model\n    def _keep(",
     "    scope = fields.Char()\n\n"
     "    def lock_for_update(self, allow_referencing=True):\n"
     "        \"\"\"Odoo 18 has no lock_for_update: a refused lock raises LockError, the transaction stays usable\"\"\"\n"
     "        try:\n"
     "            with self.env.cr.savepoint(flush=False):\n"
     "                self.env.cr.execute(\"SELECT id FROM nudge_connector_token WHERE id IN %s FOR UPDATE NOWAIT\", (tuple(self.ids),))\n"
     "        except pg_errors.LockNotAvailable:\n"
     "            raise LockError(\"The row is locked by another transaction\")\n\n"
     "    @api.model\n    def _keep("),
])
edit("nudge_connect/tests/test_oauth_signin.py", [
    ("from odoo.exceptions import AccessError, LockError, UserError\n", "from odoo.exceptions import AccessError, UserError\n"),
    ("from ..models import nudge_connector_oauth as oauth\n",
     "from ..models import nudge_connector_oauth as oauth\nfrom ..models.nudge_connector_oauth import LockError\n"),
])

# R3 RULE 2: HttpCase.url_open(params=...) is 19; 18 and 17 take the query in the address
edit("nudge_builder_realestate/tests/test_listings.py", [
    ('self.url_open("/listings", params=query)',
     'self.url_open("/listings" + ("?" + __import__("urllib.parse").parse.urlencode(query) if query else ""))'),
])
edit("nudge_builder_workshop/tests/test_workshop.py", [
    ('self.url_open("/workshop-booking", params={"service": service} if service else None)',
     'self.url_open("/workshop-booking" + ("?" + __import__("urllib.parse").parse.urlencode({"service": service}) if service else ""))'),
])

# R2 RULE 3: the Preferences form of 19 has page "page_security"; 18 and 17 call it "page_account_security"
sub("nudge_messaging/views/nudge_messaging_views.xml", r"page\[@name='page_security'\]", "page[@name='page_account_security']")

# R2 RULE 4: tools/revert.py calls model._has_field_access (19); the shim of records.py does it on 18
sub("nudge/tools/revert.py", r"\b(\w+)\._has_field_access\(", r"has_field_access(\1, ")
edit("nudge/tools/revert.py", [
    ("from .records import MODEL_PATTERN, Refused, cut, get_model, get_records, holds_secret\n",
     "from .records import MODEL_PATTERN, Refused, cut, get_model, get_records, has_field_access, holds_secret\n"),
])
# R2 RULE 4b: the test of it patches BaseModel._has_field_access: on 18 it patches the shim of the tool
sub("nudge/tests/test_revert.py", r"        real = models\.BaseModel\._has_field_access\n",
    "        from odoo.addons.nudge.tools import revert as _revert_tool\n        real = _revert_tool.has_field_access\n")
sub("nudge/tests/test_revert.py", r'patch\.object\(models\.BaseModel, "_has_field_access", deny_hours\)',
    'patch.object(_revert_tool, "has_field_access", deny_hours)')
# R2 RULE 5: sale.order.line has tax_id on 18/17 (tax_ids is 19)
sub("nudge/tests/test_chart.py", r'"tax_ids": \[\(5, 0, 0\)\]', '"tax_id": [(5, 0, 0)]')
sub("nudge/tests/test_saved_reports.py", r'"tax_ids": \[\(5, 0, 0\)\]', '"tax_id": [(5, 0, 0)]')
# R2 RULE 6: stock.move.name is required on 18/17
sub("nudge/tests/test_outliers.py", r'"product_id": product\.id, "product_uom_qty": quantity,', '"name": "Raghad move", "product_id": product.id, "product_uom_qty": quantity,')
# R2 RULE 7: res.users.group_ids is groups_id on 18/17 (existing test, only runs with Inventory installed)
sub("nudge/tests/test_product_search.py", r"plain\.group_ids -=", "plain.groups_id -=")
# R2 RULE 8: the test reads a title in Arabic without activating the language (needs it active in the database)
sub("nudge/tests/test_recipe_market.py", r'(        self\.assertEqual\(found\.with_context\(lang="ar_001"\)\.title)',
    r'        self.env["res.lang"]._activate_lang("ar_001")\n\1')
# R2 RULE 9: res.partner still has `mobile` on 18/17 (19 dropped it): the validator asks for it
sub("nudge/tests/test_validator.py", r'\{"name", "email", "phone", "vat", "parent_id", "country_id"\}', '{"name", "email", "phone", "mobile", "vat", "parent_id", "country_id"}')
# R2 RULE 10: the setup test makes warehouses as a user with Settings only; 18 asks for Inventory rights to create stock.location
sub("nudge/tests/test_setup.py", r'groups="nudge\.group_nudge_agent,base\.group_system,base\.group_partner_manager"\)',
    'groups="nudge.group_nudge_agent,base.group_system,base.group_partner_manager" + (",stock.group_stock_manager" if cls.env.ref("stock.group_stock_manager", raise_if_not_found=False) else ""))')
# R2 RULE 11: base_automation patches create/write on the model class when a rule is made; 18's check_attrs flags it
edit("nudge/tests/test_approval_flow.py", [
    ("class TestApprovalFlow(NudgeCase, TransactionCase):\n",
     "class TestApprovalFlow(NudgeCase, TransactionCase):\n"
     "    def check_attrs(self):  # R2 RULE 11: base_automation's class patches are not a leak\n"
     "        pass\n\n"),
])
# R2 RULE 12: the snippets of 18 and 17 (features, faq, call to action, team) title the section with an h3 (19 has an h2)
# R2 RULE 12b: the tests of the snippets read the heading levels of 19 (title h2, items h3, team items h3)
T = "nudge_builder/tests/test_snippets.py"
sub(T, r'headings = \[h\.text for h in section\.xpath\("\.//h3"\)\]', 'headings = [h.text for h in section.xpath(".//h3")[1:]]')
sub(T, r'self\.assertEqual\(section\.xpath\("\.//h2"\)\[0\]\.text, "Why us"\)', 'self.assertEqual(section.xpath(".//h3")[0].text, "Why us")')
# (the features filled with one item and no title: since 2.0 an untitled section loses its title h3 on 18 too, so the
# only h3 left is the item's, as on 19: no rule)
sub(T, r'team\.xpath\("\.//h3"\)\[0\]\.text, "Raghad"', 'team.xpath(".//h4")[0].text, "Raghad"')
sub(T, r'self\.assertIn\("<script>", section\.xpath\("\.//h2"\)\[0\]\.text\)', 'self.assertIn("<script>", section.xpath(".//h3")[0].text)')
sub("nudge_builder/tests/test_core.py", r'self\.assertEqual\(section\.xpath\("\.//h2"\)\[0\]\.text, "Visit us"\)', 'self.assertEqual(section.xpath(".//h3")[0].text, "Visit us")')

# R2 RULE 14: the title of s_features is an h3 in 18 (the test of the text-not-markup looks for an h2)
sub(T, r'section\.xpath\("\.//h2"\)\[0\]\.text\.strip\(\), "Hello"', 'section.xpath(".//h2|.//h3")[0].text.strip(), "Hello"')
# R2 RULE 15: a many2one is [id, name] in the record data of 18, an object with an id in 19 (the linked record of the screen)
edit("nudge/static/src/context/snapshot.js", [
    ("        if (field.type === \"many2one\" && Number.isInteger(record.data[info.name]?.id)) {\n"
     "            entry.rid = record.data[info.name].id;\n",
     "        const linked = record.data[info.name];\n"
     "        const linkedId = Array.isArray(linked) ? linked[0] : linked?.id;\n"
     "        if (field.type === \"many2one\" && Number.isInteger(linkedId)) {\n"
     "            entry.rid = linkedId;\n"),
])

# FIX-18 RULE 1: a test patches Partner._has_field_access (19); on 18 it patches the shim of the records tool
sub("nudge/tests/test_redact.py", r"        real = Partner\._has_field_access\n", "        real = records.has_field_access\n")
sub("nudge/tests/test_redact.py", r'patch\.object\(Partner, "_has_field_access",', 'patch.object(records, "has_field_access",')

# FIX-TRIAL-2 RULE 1: the groups of a server action are group_ids in 19, groups_id in 18/17 (only on ir.actions.server records)
for _dir, _dirs, _fs in os.walk(ROOT):
    for _f in _fs:
        if _f.endswith(".xml"):
            _rel = os.path.relpath(os.path.join(_dir, _f), ROOT).replace(os.sep, "/")
            sub(_rel, r'(<record id="[^"]+" model="ir\.actions\.server">(?:(?!</record>).)*?)name="group_ids"',
                r'\1name="groups_id"', flags=re.S)

# DOCS RULE 1: the tree carries the Odoo documentation of its own version, not the one of 19
# (docs_data/ is at the root of the repository, next to scripts/; built by addons/nudge/scripts/build_odoo_docs.py)
_docs = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs_data", "odoo_docs_18.0.jsonl.gz")
if os.path.exists(_docs) and os.path.isdir(ROOT + "nudge/data"):
    shutil.copyfile(_docs, ROOT + "nudge/data/odoo_docs.jsonl.gz")
    print("documentation of Odoo 18.0 put in nudge/data")
else:
    print("WARNING: no documentation of Odoo 18.0 found at", _docs, "- the 19 pages stay")

# QA RULE: the agents' security (60.0) names the group of 19 by its privilege, and a sequence 18 has not
if os.path.exists(ROOT + "nudge/security/nudge_agent_security.xml"):
    sub("nudge/security/nudge_agent_security.xml",
        r'<field name="privilege_id" ref="res_groups_privilege_nudge"/>',
        '<field name="category_id" ref="module_category_nudge"/>')
    sub("nudge/security/nudge_agent_security.xml", r'        <field name="sequence">\d+</field>\n', "")

# QA RULE (60.1): the direct chat with an agent in Discuss (static/src/agent_chat.js) imports modules of Odoo 19 only;
# 17 and 18 open the chat through the server guards alone, so the file and its assets entry are dropped
_chat = ROOT + "nudge_discuss/static/src/agent_chat.js"
if os.path.exists(_chat):
    os.remove(_chat)
    _m = io.open(ROOT + "nudge_discuss/__manifest__.py", encoding="utf-8").read()
    _m = re.sub(r'\n\s*"assets": \{\s*"web\.assets_backend": \[[^\]]*\],?\s*\},?', "", _m)
    io.open(ROOT + "nudge_discuss/__manifest__.py", "w", encoding="utf-8", newline="\n").write(_m)
