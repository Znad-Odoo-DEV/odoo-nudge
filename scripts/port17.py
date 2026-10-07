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
for module in ("nudge", "nudge_mail"):
    sub(f"{module}/__manifest__.py", r'"version": "18\.0\.', '"version": "17.0.')

# ---------------------------------------------------------------- Hoot does not exist in 17
s = read("nudge/__manifest__.py")
s = re.sub(r'        "web\.assets_unit_tests": \[\n(?:.*\n)*?        \],\n', "", s)
write("nudge/__manifest__.py", s)
for path in files("nudge/static/tests/*.js"):
    os.remove(ROOT + path)
print("Hoot tests removed:", not files("nudge/static/tests/*.js"))

# ---------------------------------------------------------------- env._ is 18
for name in files("nudge/**/*.py", "nudge_mail/**/*.py"):
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
for name in files("nudge/**/*.py", "nudge_mail/**/*.py"):
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

# ---------------------------------------------------------------- the bus (records._bus_send is 18)
edit("nudge/models/nudge_run.py", [
    ('            run.user_id._bus_send("nudge.run", {\n',
     '            run.env["bus.bus"]._sendone(run.user_id.partner_id, "nudge.run", {\n'),
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

for name in files("nudge/**/*.xml", "nudge_mail/**/*.xml"):
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
sub("nudge/static/description/index.html",
    r"^[ \t]*<li>[^\n]*(Nudge: Studio|Nudge Spreadsheets|Odoo Studio)[^\n]*</li>\n", "", flags=re.M)
sub("nudge/static/description/index.html",
    r"^[ \t]*<div class=\"col-md-6\"[^\n]*<h4>Reports as spreadsheets</h4>[^\n]*</div>\n", "", flags=re.M)

# ---------------------------------------------------------------- the words "Odoo 18"
for name in ("nudge/llm/prompt.py", "nudge/README.md", "nudge/README.ar.md", "nudge/static/description/index.html",
             "nudge/doc/user-guide.md", "nudge/doc/user-guide.ar.md", "nudge/doc/security.md", "nudge/__manifest__.py"):
    sub(name, r"Odoo 18(\.0)?", "Odoo 17")
sub("nudge/tests/test_profile.py", r"Odoo 18\.0 Community", "Odoo 17.0 Community")
print("done")
