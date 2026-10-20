"""Turn the Odoo 18 copy of Nudge into its Odoo 17 copy.

Run on a fresh copy of the Odoo 18 port (itself made by port18.py):

    python port17.py "D:/odoo bootcamp/odoo-nudge-17"

Every change is a known difference between Odoo 17 and 18. Idempotent.
"""
import glob
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
    if n:
        write(name, s)
    return n


def files(*patterns):
    found = []
    for pattern in patterns:
        found += [path[len(ROOT):].replace("\\", "/") for path in glob.glob(ROOT + pattern, recursive=True)]
    return sorted(set(found))


# ---------------------------------------------------------------- modules
# The spreadsheet editor of 17 is Enterprise only: the reports stay in the chat on 17
for module in ("nudge_spreadsheet", "nudge_documents", "nudge_studio"):
    if os.path.isdir(ROOT + module):
        shutil.rmtree(ROOT + module)
        print("dropped", module)
for module in ("nudge", "nudge_mail", "nudge_discuss", "nudge_mcp", "nudge_website", "nudge_builder", "nudge_messaging"):  # R2 RULE: release-2 modules
    sub(f"{module}/__manifest__.py", r'"version": "18\.0\.', '"version": "17.0.')

# ---------------------------------------------------------------- Hoot does not exist in 17
s = read("nudge/__manifest__.py")
s = re.sub(r'        "web\.assets_unit_tests": \[\n(?:.*\n)*?        \],\n', "", s)
write("nudge/__manifest__.py", s)
for path in files("nudge/static/tests/*.js"):
    os.remove(ROOT + path)
print("Hoot tests removed:", not files("nudge/static/tests/*.js"))

# ---------------------------------------------------------------- env._ is 18
for name in files("nudge/**/*.py", "nudge_mail/**/*.py", "nudge_discuss/**/*.py", "nudge_mcp/**/*.py", "nudge_website/**/*.py", "nudge_builder/**/*.py", "nudge_messaging/**/*.py"):  # R2 RULE
    s = read(name)
    # self.env._( / env._( / record.env._( / self.env["x"].env._( → _(
    s2, n = re.subn(r"(?<![\w.\]])(?:[A-Za-z_]\w*(?:\[[^\[\]]*\])?\.)*env\._\(", "_(", s)
    if not n:
        continue
    if not re.search(r"^from odoo import .*\b_\b", s2, flags=re.M):
        line = re.search(r"^from odoo import ([^\n(]+)$", s2, flags=re.M)
        if line:
            names = sorted({part.strip() for part in line.group(1).split(",")} | {"_"})
            s2 = s2.replace(line.group(0), "from odoo import " + ", ".join(names), 1)
        else:
            first = re.search(r"^(from|import) ", s2, flags=re.M)
            s2 = s2[:first.start()] + "from odoo import _\n" + s2[first.start():]
    write(name, s2)
    print("ported", name, n, "translations")

# ---------------------------------------------------------------- the email_outgoing sanitize mode is 18
# An Html field of 17 takes sanitize as a yes or no: the styles of an email are kept with sanitize_style
for name in files("nudge_mail/**/*.py"):
    sub(name, r'sanitize="email_outgoing"', "sanitize_style=True")

# ---------------------------------------------------------------- has_access, check_access are 18
write("nudge/models/compat17.py", '''from odoo import models
from odoo.exceptions import AccessError


class Base(models.AbstractModel):
    """What Odoo 18 calls has_access and check_access, for Odoo 17."""

    _inherit = "base"

    def _nudge_has_access(self, operation):
        """:return: whether the person may do ``operation`` on the model, and on
        these records when there are some"""
        if not self.check_access_rights(operation, raise_exception=False):
            return False
        if not self:
            return True
        try:
            self.check_access_rule(operation)
        except AccessError:
            return False
        return True

    def _nudge_check_access(self, operation):
        """:raise AccessError: when the person may not do ``operation``"""
        self.check_access_rights(operation)
        self.check_access_rule(operation)
''')
edit("nudge/models/__init__.py", [("from . import nudge_provider\n", "from . import compat17\nfrom . import nudge_provider\n")])
for name in files("nudge/**/*.py", "nudge_mail/**/*.py", "nudge_mcp/**/*.py", "nudge_website/**/*.py", "nudge_builder/**/*.py", "nudge_messaging/**/*.py"):  # R2 RULE
    if name.endswith("compat17.py"):
        continue
    n = sub(name, r"\.has_access\(", "._nudge_has_access(")
    n += sub(name, r"\.check_access\(", "._nudge_check_access(")
    n += sub(name, r"\.has_groups\(", ".user_has_groups(")
    if n:
        print("ported", name, n, "access calls")

# ---------------------------------------------------------------- res.lang._get_data is 18
edit("nudge/models/nudge_conversation.py", [
    ('        lang = self.env["res.lang"]._get_data(code=user.lang or "en_US")\n',
     '        lang = self.env["res.lang"]._lang_get(user.lang or "en_US")\n'),
])
# Tests patch the method by its name
sub("nudge/tests/test_activity.py", r'"has_access", lambda records, operation: False', '"_nudge_has_access", lambda records, operation: False')
# In 17 web_tour adds no field to res.users: the profile tests that used it as the example
# of a standard model extended by a module have nothing to look at (the field created in
# the database still covers it)
edit("nudge/tests/test_profile.py", [
    ('        users = slices["model:res.users"]\n'
     '        self.assertFalse(users["data"]["custom"])\n'
     '        self.assertIn("standard Odoo model that this database extends", users["summary"])\n'
     '        added = {field["name"]: field for field in users["data"]["fields"]}\n'
     '        self.assertEqual(added["tour_enabled"]["added_by"], "web_tour")\n'
     '        self.assertNotIn("login", added, "Standard fields are not repeated")\n', ""),
    ('        self.assertIn("model:res.users", self.keys("users tour_enabled"))\n', ""),
    ('        self.assertEqual(fields_found["tour_enabled"]["added_by"], "web_tour")\n'
     '        self.assertIn("this database extends", described["in_this_database"])\n', ""),
    ('        self.assertEqual(described["fields"][0]["added_by"], "web_tour", "What is added comes first")\n', ""),
])

# ---------------------------------------------------------------- test users (17 gives them no password)
edit("nudge/tests/common.py", [
    ("from odoo.tests import new_test_user\n",
     "from odoo.tests import new_test_user as odoo_new_test_user\n"
     "\n\n"
     "def new_test_user(env, login=\"\", groups=\"base.group_user\", context=None, **kwargs):\n"
     "    \"\"\"Odoo 17 gives a test user no password: the login, as 18 does\"\"\"\n"
     "    kwargs.setdefault(\"password\", login)\n"
     "    return odoo_new_test_user(env, login=login, groups=groups, context=context, **kwargs)\n"),
])
edit("nudge/tests/test_admin.py", [
    ("from odoo.tests.common import new_test_user\n", "from .common import new_test_user\n"),
])

# ---------------------------------------------------------------- list views are tree views in 17
# The tools that read and change screens name the list view as 17 does
if os.path.exists(ROOT + "nudge/tools/building.py"):
    sub("nudge/tools/building.py", r'VIEW_TYPES = \("form", "list", "kanban", "search"\)', 'VIEW_TYPES = ("form", "tree", "kanban", "search")')
if os.path.exists(ROOT + "nudge/tools/configuration.py"):
    sub("nudge/tools/configuration.py", r'get_view\(view_type="list"\)', 'get_view(view_type="tree")')
if os.path.exists(ROOT + "nudge/tests/test_building.py"):
    sub("nudge/tests/test_building.py", r'"view_type": "list"', '"view_type": "tree"')
    sub("nudge/tests/test_building.py", r'assertIn\("\(list\)"', 'assertIn("(tree)"')

for name in files("nudge/**/*.xml", "nudge_mail/**/*.xml", "nudge_mcp/**/*.xml", "nudge_website/**/*.xml", "nudge_builder/**/*.xml", "nudge_messaging/**/*.xml"):  # R2 RULE
    n = sub(name, r"<list(?=[\s>])", "<tree")
    n += sub(name, r"</list>", "</tree>")
    n += sub(name, r'(<field name="view_mode">)list', r"\1tree")
    if n:
        print("ported", name, n, "list views")
edit("nudge/profile/collectors.py", [
    ('("type", "in", ("form", "list", "kanban", "search"))', '("type", "in", ("form", "tree", "kanban", "search"))'),
])

# ---------------------------------------------------------------- the web client of 17
for name in files("nudge/static/src/**/*.js"):
    sub(name, r'@web/core/position/position_hook', "@web/core/position_hook")
# user is a service in 17, not a module
edit("nudge/static/src/core/nudge_service.js", [
    ('import { user } from "@web/core/user";\n', ""),
    ('    dependencies: [\n        "orm",\n', '    dependencies: [\n        "orm",\n        "user",\n'),
    ("        {\n            orm,\n            hotkey: hotkeyService,\n",
     "        {\n            orm,\n            user,\n            hotkey: hotkeyService,\n"),
])
edit("nudge/static/src/voice/voice_service.js", [
    ('import { user } from "@web/core/user";\n', ""),
    ('    dependencies: ["notification"],\n', '    dependencies: ["notification", "user"],\n'),
    ("    start(env, { notification }) {\n", "    start(env, { notification, user }) {\n"),
])
edit("nudge/static/src/components/nudge_panel.js", [
    ('import { user } from "@web/core/user";\n', 'import { session } from "@web/session";\n'),
    ('        const firstName = (user.name || "").trim().split(/\\s+/)[0];\n',
     '        const firstName = (session.name || "").trim().split(/\\s+/)[0];\n'),
])

# ---------------------------------------------------------------- Odoo 17 transpiles a JS file as a module only with the header
for name in files("nudge/static/**/*.js"):
    s = read(name)
    if "@odoo-module" not in s.split("\n", 3)[0:3].__str__():
        write(name, "/** @odoo-module **/\n\n" + s)
        print("ported", name, "module header")

# ---------------------------------------------------------------- tours of 17
TOURS = "nudge/static/tests/tours/nudge_tours.js"
if os.path.exists(ROOT + TOURS):
    sub(TOURS, r'url: "/odoo"', 'url: "/web"')
    sub(TOURS, r'run: "edit ', 'run: "text ')
    # The selector engine of 17 is Sizzle: an input's value is matched by
    # :propValue("..."), not by :value(...)
    sub(TOURS, r'input:value\(([^)]*)\)', r"input:propValue('\1')")
    sub(TOURS, r'input:propValue\("([^"]*)"\)', r"input:propValue('\1')")
    KEYS = {
        "press ctrl+shift+space": '{ key: " ", code: "Space", ctrlKey: true, shiftKey: true }',
        "press alt+ctrl+d": '{ key: "d", code: "KeyD", altKey: true, ctrlKey: true }',
    }
    # A press is a keydown and a keyup: without the keyup the shortcut counts
    # as held, and the next press of it is swallowed
    for command, init in KEYS.items():
        sub(TOURS, re.escape(f'run: "{command}"'),
            "run() { for (const type of [\"keydown\", \"keyup\"]) { document.activeElement.dispatchEvent(new KeyboardEvent(type, "
            + init[:-2] + ", bubbles: true })); } }")
    sub(TOURS,
        r'run\(\) \{ document\.activeElement\.dispatchEvent\(new KeyboardEvent\("keydown", (\{[^}]*\})\)\); \}',
        r'run() { for (const type of ["keydown", "keyup"]) { document.activeElement.dispatchEvent(new KeyboardEvent(type, \1)); } }')
    # What is typed in a field of a form reaches its record only after the
    # onchange round trip; the tour of 17 would ask Nudge before it lands
    s = read(TOURS)
    if "function formHas(" not in s:
        s = s.replace(
            'import { registry } from "@web/core/registry";\n',
            'import { registry } from "@web/core/registry";\n\n'
            "/** Wait until the open form's record carries the value typed in one of its fields */\n"
            "async function formHas(field, value) {\n"
            "    const carries = (node) => {\n"
            "        const data = node.component?.model?.root?.data;\n"
            "        return (data && data[field] === value) || Object.values(node.children || {}).some(carries);\n"
            "    };\n"
            "    for (let i = 0; i < 100; i++) {\n"
            "        if (carries(odoo.__WOWL_DEBUG__.root.__owl__)) {\n"
            "            return;\n"
            "        }\n"
            "        await new Promise((resolve) => setTimeout(resolve, 50));\n"
            "    }\n"
            '    throw new Error(`The form never took "${value}" in ${field}`);\n'
            "}\n", 1)
        s = re.sub(
            r'^(\s+)\{\n\1    (?:content: [^\n]*\n\1    )?trigger: "\.o_form_view \.o_field_widget\[name=(\w+)\] input",\n\1    run: "text ([^"]*)",\n\1\},',
            lambda m: m.group(0) + "\n" + m.group(1) + "{\n" + m.group(1) + '    trigger: "body",\n'
            + m.group(1) + f'    async run() {{ await formHas("{m.group(2)}", "{m.group(3)}"); }},\n' + m.group(1) + "},",
            s, flags=re.M)
        write(TOURS, s)
    # A click of 17 fires the pointer events but gives no focus: the inputs
    # are focused by hand, for the steps that wait for ":focus" and for the
    # keys pressed next
    s = read(TOURS)
    s = re.sub(
        r'(trigger: "[^"]*(?:input|\.o_nudge_input)",\n(\s+))run: "click",',
        r'\1run() { const el = this.$anchor.get(0); el.click(); el.focus(); },',
        s)
    write(TOURS, s)
    # A step with nothing to do is a check in 17 too, not a click
    lines = read(TOURS).split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^\s*\{\s*$", line):
            indent = len(line) - len(line.lstrip())
            j = i + 1
            while j < len(lines) and not re.match(r"^" + " " * indent + r"\},?\s*$", lines[j]):
                j += 1
            block = lines[i:j + 1]
            text = "\n".join(block)
            if "trigger:" in text and not re.search(r"^\s*(?:async )?run\b", text, flags=re.M) and "isCheck" not in text:
                block = [block[0], " " * (indent + 4) + "isCheck: true,"] + block[1:]
            out += block
            i = j + 1
            continue
        out.append(line)
        i += 1
    write(TOURS, "\n".join(out))
    print("ported", TOURS)
edit("nudge/tests/test_ui.py", [
    ('self.start_tour("/odoo", "nudge_chat", login="nudge_a")', 'self.start_tour("/web", "nudge_chat", login="nudge_a")'),
    ('self.start_tour("/odoo", "nudge_hidden", login="nudge_out")', 'self.start_tour("/web", "nudge_hidden", login="nudge_out")'),
])
# Every tour that opens a contact: the URL of 17
sub("nudge/tests/test_ui.py", r'f"/odoo/action-\{action\.id\}/\{partner\.id\}"',
    'f"/web#action={action.id}&id={partner.id}&model=res.partner&view_type=form"')

# ---------------------------------------------------------------- no Studio, no spreadsheets in 17: the store page says nothing of them
# Named among the other companions, they are left out of the sentence; a line of their own goes
sub("nudge/static/description/index.html", r"Nudge: Discuss, Nudge: Spreadsheets, ", "Nudge: Discuss, ")
sub("nudge/static/description/index.html", r", Nudge: Discuss and Nudge: Spreadsheets\. ", " and Nudge: Discuss. ")
sub("nudge/static/description/index.html", r", the tasks, the spreadsheets\.", ", the tasks.")
sub("nudge/static/description/index.html",
    r"(<b>Odoo Enterprise only</b>: )Nudge: Studio \(web_studio\) and Nudge: Spreadsheets in Documents "
    r"\(documents_spreadsheet\), since the apps they bridge are Enterprise; and (dark mode)", r"\1\2")
sub("nudge/static/description/index.html",
    r"Nudge: Spreadsheets, Nudge: Spreadsheets in Documents and Nudge: Studio install themselves "
    r"when Nudge and the app they bridge are both there\. ", "")
sub("nudge/static/description/index.html",
    r"^[ \t]*<li>[^\n]*(Nudge: Studio|Nudge:? Spreadsheets|Odoo Studio)[^\n]*</li>\n", "", flags=re.M)
sub("nudge/static/description/index.html",
    r"^[ \t]*<div class=\"col-md-6\"[^\n]*<h4>Reports as spreadsheets</h4>[^\n]*</div>\n", "", flags=re.M)

# ---------------------------------------------------------------- migrations: named for the version of 17
for module in sorted(os.listdir(ROOT)):
    folder = ROOT + module + "/migrations/"
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if name.startswith("18.0."):
                os.rename(folder + name, folder + "17.0." + name[len("18.0."):])
                print("renamed", module, "migration", name)
# The tests of a migration read its script by its folder, and give the version it comes from
for module in sorted(os.listdir(ROOT)):
    tests = ROOT + module + "/tests/"
    if os.path.isdir(tests):
        for name in sorted(os.listdir(tests)):
            if name.endswith(".py"):
                sub(f"{module}/tests/{name}", r'(file_path\("[\w/]+/migrations/)18\.0\.', r"\g<1>17.0.")
                sub(f"{module}/tests/{name}", r'(\.migrate\(self\.env\.cr, ")18\.0\.', r"\g<1>17.0.")
                sub(f"{module}/tests/{name}", r'(\.migrate\(cr, ")18\.0\.', r"\g<1>17.0.")  # R2 RULE 13b
                sub(f"{module}/tests/{name}" if name == "test_saved_reports.py" else "nudge/none", r'("migrations", ")18\.0\.', r"\g<1>17.0.")  # R2 RULE 13

# ---------------------------------------------------------------- the words "Odoo 18"
for name in ("nudge/llm/prompt.py", "nudge/README.md", "nudge/README.ar.md", "nudge/static/description/index.html",
             "nudge/doc/user-guide.md", "nudge/doc/user-guide.ar.md", "nudge/doc/security.md", "nudge/__manifest__.py"):
    sub(name, r"Odoo 18(\.0)?", "Odoo 17")
sub("nudge/tests/test_profile.py", r"Odoo 18\.0 Community", "Odoo 17.0 Community")
# ---------------------------------------------------------------- STORE REVIEW RULES (nudge_mcp, nudge_website, later files)
# FINAL RULE: the record address is handled by nudge/tools/urls.py (record_url) in the code itself now; the old history.py edit is gone
# The check of access of the public assistant looks for the method by its name
sub("nudge_website/tools/assistant.py", r'hasattr\(records, "has_access"\)', 'hasattr(records, "_nudge_has_access")')
# Odoo 17 has no expiry on an API key: _generate takes two arguments, and the wizard does not ask for a duration
edit("nudge_mcp/models/nudge_mcp_help.py", [
    ("        expires = fields.Datetime.now() + timedelta(days=int(self.duration))\n", ""),
    ("._generate(SCOPE, self.name, expires)", "._generate(SCOPE, self.name)"),
])
sub("nudge_mcp/views/nudge_mcp_views.xml", r'\n[ \t]*<field name="duration"/>', "")

# FINAL RULE: Odoo 17 validates that a field used in a modifier is in the view; the MCP approval buttons use origin
sub("nudge_mcp/views/nudge_mcp_views.xml", r'(<xpath expr="//header" position="inside">\n)(\s*)(<button name="action_mcp_approve")',
    r'\1\2<field name="origin" invisible="1"/>\n\2\3')

# QA RULE 13 (17, merged from the QA copy): `_ = self.env._` is an alias, not a call: the env._( rule never sees it
sub("nudge/models/nudge_embedding.py", r"^(\s*)_ = self\.env\._\n", r"\1from odoo import _  # noqa: PLC0415\n", flags=re.M)

# QA RULE 12 (17, merged from the QA copy): res.lang._get_data is 18; 17 has _lang_get
sub("nudge/models/nudge_history.py", r'self\.env\["res\.lang"\]\._get_data\(code=user\.lang or "en_US"\)', 'self.env["res.lang"]._lang_get(user.lang or "en_US")')
# QA RULE 14 (17, merged): test_profile builds "Odoo 18.0 {edition}"
sub("nudge/tests/test_profile.py", r"Odoo 18\.0 \{edition\}", "Odoo 17.0 {edition}")
# QA RULE 15 (17, merged): tests assert the /odoo/action-N/id shape of 18; 17 gives /web#action=N&active_id=id
sub("nudge/tests/test_task_lists.py", r'self\.assertIn\(f"action-\{server\.id\}", location\)', 'self.assertIn(f"action={server.id}", location)')
sub("nudge/tests/test_task_lists.py", r'self\.assertIn\(f"/\{step\.id\}/", location\)', 'self.assertIn(f"active_id={step.id}", location)')
sub("nudge/tests/test_task_rules.py", r'self\.assertIn\("/action-", task\.last_result\)', 'self.assertIn("#action=", task.last_result)')

# FINAL RULE: tools/history.py:linked read the model out of /odoo/<model>/<id>; 17 has /web#model=<model>&id=<id>
sub("nudge/tools/history.py", r'covered = \{row\.get\("url", ""\)\.split\("/"\)\[2\] for row in rows if row\.get\("url"\)\}',
    'covered = {found.group(1) or found.group(2) for found in (re.search(r"/odoo/([^/]+)/|[#&]model=([^&]+)", row["url"]) for row in rows if row.get("url")) if found}')
# FINAL RULE: the link of a note ("Show me") accepts the 17 address too, and its test expects it
sub("nudge_discuss/models/nudge_mention.py", r'\(/odoo/\[A-Za-z0-9_\./-\]\+\)', r'(/(?:odoo/|web#)[A-Za-z0-9_./=&;#-]+)')
sub("nudge_discuss/tests/test_mentions.py", r'''f'<a href="/odoo/res\.partner/\{child\.id\}">Show me</a>\'''', '''f'<a href="/web#model=res.partner&amp;id={child.id}&amp;view_type=form">Show me</a>\'''')

# FINAL RULE: Odoo 17's _() finds the language of the reader in the locals of its caller (cr, uid, context) or in self.env;
# the health checks translate where only env is known, so they were always in English
sub("nudge/tools/health.py", r"(    def texts\(self, env, key, count, sample\):\n)(        \"\"\"[^\n]*\"\"\"\n)?",
    lambda m: m.group(1) + (m.group(2) or "") + "        cr, uid, context = env.cr, env.uid, env.context  # 17: what _() looks for\n")

print("done")

# R2 RULE 1 (17): the forms of 17 must carry every field their modifiers read (18 adds the field itself)
edit("nudge/views/nudge_translation_views.xml", [
    ('                <sheet>\n                    <div class="alert alert-info" role="alert" invisible="not note">',
     '                <sheet>\n                    <field name="state" invisible="1"/>\n                    <div class="alert alert-info" role="alert" invisible="not note">'),
])
# R2 RULE 2 (17): self.env._ with a language of its own. 17's _() reads `context` and `cr` of the frame that calls it
_name = "nudge_messaging/models/nudge_messaging.py"
_s = read(_name)
if "def _tr(env, source" not in _s:
    _helper = (
        "\n\ndef _tr(env, source, *args, **kwargs):  # R2 RULE 2: 17's _() reads these two locals\n"
        "    context = env.context  # noqa: F841\n"
        "    cr = env.cr  # noqa: F841\n"
        "    return _(source, *args, **kwargs)\n\n\n"
        "class _Lang:\n"
        "    def __init__(self, env):\n"
        "        self.env = env\n\n"
        "    def _(self, source, *args, **kwargs):\n"
        "        return _tr(self.env, source, *args, **kwargs)\n\n\n"
        "class ")
    _s = _s.replace("\n\nclass ", _helper, 1)
    write(_name, _s)
edit(_name, [
    ('        return self.with_context(lang=lang or self.env.context.get("lang") or "en_US").env\n',
     '        return _Lang(self.with_context(lang=lang or self.env.context.get("lang") or "en_US").env)\n'),
])
sub(_name, r"self\.with_context\(lang=self\._lang_of\(user, lang\)\)\.env\._\(",
    "_tr(self.with_context(lang=self._lang_of(user, lang)).env, ")
edit("nudge/views/nudge_health_views.xml", [
    ('                    <field name="scope" invisible="1"/>\n                    <div class="alert alert-info" role="alert" invisible="scope != \'upgrade\'">',
     '                    <field name="scope" invisible="1"/>\n                    <field name="pack" invisible="1"/>\n                    <div class="alert alert-info" role="alert" invisible="scope != \'upgrade\'">'),
])  # R2 RULE 3 (17): the form reads `pack` in modifiers (also parent.pack of the lines): the field must be in the form
# R2 RULE 4 (17): the settings of the messaging companion read two computed flags in modifiers: 17 wants the fields in the view
sub("nudge_messaging/views/res_config_settings_views.xml", r'(\n( *)<field name="nudge_msg_enabled"/>)(?!\n *<field name="nudge_msg_whatsapp_keys_set")',
    r'\1\n\2<field name="nudge_msg_whatsapp_keys_set" invisible="1"/>\n\2<field name="nudge_msg_telegram_keys_set" invisible="1"/>')

# R2 RULE 8 (17): ir.cron has no failure_count on 17
sub("nudge/tools/debugger.py", r"if not cron\.active and cron\.failure_count:", 'if not cron.active and "failure_count" in cron._fields and cron.failure_count:')
sub("nudge/tests/test_debugger.py", r'(        cron\.write\(\{"failure_count": 3)',
    r'        if "failure_count" not in cron._fields:\n            self.skipTest("ir.cron has no failure_count on 17")\n\1')
# R2 RULE 9 (17): product.product has detailed_type, not is_storable
sub("nudge/tests/test_outliers.py", r'"is_storable": True', '"detailed_type": "product"')
# R2 RULE 10 (17): the key of 17 has no expiry
sub("nudge_mcp/tests/test_mcp.py", r'\._generate\(\n\s+scope, "test", fields\.Datetime\.now\(\) \+ timedelta\(days=days\)\)', '._generate(scope, "test")')
# R2 RULE 11 (17): the tests patch has_access, which the port renamed; a sale line of 17 needs a product (CHECK accountable_required_fields)
sub("nudge/tests/test_revert.py", r'patch\.object\(type\(self\.task\), "has_access",', 'patch.object(type(self.task), "_nudge_has_access",')
sub("nudge/tests/test_revert.py", r'"name": "Door", "product_uom_qty": 1, "price_unit": 10\.0\}', '"product_id": self.env["product.product"].create({"name": "Raghad Door"}).id, "name": "Door", "product_uom_qty": 1, "price_unit": 10.0}')
# R2 RULE 12 (17): product.group_product_manager is 18; the sales manager can make products on 17
for _name in ("nudge_builder/tests/test_checks.py", "nudge_builder/tests/test_shop.py"):
    sub(_name, r"product\.group_product_manager", "sales_team.group_sale_manager")
# R2 RULE 13 (17): the snippets of 17: no section title in s_features, no contact snippet, a team without data-name
T = "nudge_builder/tests/test_snippets.py"
sub(T, r'section\.xpath\("\.//h3"\)\[1:\]', 'section.xpath(".//h3")')
sub(T, r'        self\.assertEqual\(section\.xpath\("\.//h3"\)\[0\]\.text, "Why us"\)\n', "        # 17: s_features has no section title\n")
sub(T, r'        self\.assertIn\("<script>", section\.xpath\("\.//h3"\)\[0\]\.text\)\n', "        # 17: s_features has no section title to put the text in\n")
sub(T, r"(    def test_contact_and_map_take_the_company_facts\(self\):\n)", r'\1        self.skipTest("Odoo 17 has no contact snippet: a text block stands in")\n')
sub(T, r"len\(team\.xpath\(\"\.//div\[@data-name='Team Member'\]\"\)\)", 'len(team.xpath(".//h4"))')
sub("nudge_builder/tests/test_templates.py", r"        for snippet in used:\n            self\.assertTrue\(self\.env\.ref\(\"website\.\" \+ snippet",
    '        for snippet in used - {"s_contact_info"}:  # 17 has no contact snippet\n            self.assertTrue(self.env.ref("website." + snippet')
# R2 RULE 15 (17): the tests look for the /odoo/<model>/<id> shape of the link; 17 has /web#model=..&id=..&view_type=form
M = "nudge_messaging/tests/test_messaging.py"
sub(M, r'f"/nudge\.step/\{step\.id\}"', 'f"id={step.id}&view_type=form"')
sub(M, r'f"/res\.partner/\{partner\.id\}"', 'f"id={partner.id}&view_type=form"')
# R2 RULE 16 (17): the accordion of 17 is cards with an <a> and a collapse (18 has accordion-item and a button)
T = "nudge_builder/tests/test_snippets.py"
sub(T, r'faq\.xpath\("\.//div\[contains\(@class,\'accordion-collapse\'\)\]/@id"\)', 'faq.xpath(".//div[contains(@class,\'collapse\')]/@id")')
sub(T, r'faq\.xpath\("\.//button/@data-bs-target"\)', 'faq.xpath(".//a[@data-bs-toggle=\'collapse\']/@data-bs-target")')
sub(T, r"\.//div\[contains\(@class,'accordion-body'\)\]", ".//div[contains(@class,'card-body')]")
sub(T, r"        self\.assertEqual\(section\.xpath\(\"\.//p\[contains\(@class,'lead'\)\]\"\)\[0\]\.text, \"Three reasons\.\"\)\n", "        # 17: s_features has no lead text\n")
# R2 RULE 17: 17's features block has no title slot, and a title is added only to a block that has items (the test gives none)
sub("nudge_builder/tests/test_snippets.py", r'snippets\.fill\(section, "s_features", \{"title": "Hello <script>alert\(1\)</script>"\}\)', 'snippets.fill(section, "s_features", {"title": "Hello <script>alert(1)</script>", "items": [{"title": "One", "text": "x"}]})')
