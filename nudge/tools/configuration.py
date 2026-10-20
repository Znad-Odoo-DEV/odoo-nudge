"""Setting Odoo up, for any app and any business.

Nothing here knows an app in advance. The settings of every installed app
are read from the Settings screen itself, with their help and their page of
the documentation; what an app lets people define (warehouses, routes,
journals, salary structures...) is read from its Configuration menu; what a
module adds, custom ones included, from what it registered. So an app
installed tomorrow is understood the same way.

Values of settings are an administrator's to read. Changing them is an
administrator's, when the setup is open to administrators, and only once they
said yes. The code of a module is an administrator's to read.
"""
import ast
import os
import re

from lxml import etree

from odoo.modules.module import get_module_path
from odoo.tools import html2plaintext

from . import base
from .actions import read_value
from .navigation import _visible_menus, menu_path, menu_steps
from .records import SECRET_KEY, Refused, cut, get_model, get_fields, format_value, refusing

SETTINGS = "res.config.settings"
APP_PATTERN = r"[A-Za-z0-9_ .&'-]{2,64}"
MODULE_PATTERN = r"[a-z0-9_]{2,64}"
MAX_APPS = 80
MAX_CONFIG_MENUS = 40
MAX_CONFIG_ROWS = 40
MAX_COLUMNS = 6
MAX_FOUND_SETTINGS = 25
# What one answer about an app holds; the rest is read block by block
MAX_SETTINGS_CHARS = 5000
MAX_CHANGES = 10
MAX_SHOWN_SETTINGS = 6
MAX_MODULE_FIELDS = 80
MAX_MODULE_ITEMS = 25
# Reading code
CODE_EXTENSIONS = (".py", ".xml", ".csv", ".js", ".scss", ".css", ".md", ".rst", ".txt", ".json", ".yml", ".yaml")
SKIPPED_DIRECTORIES = {"__pycache__", "lib", "node_modules", ".git", "i18n", "i18n_extra"}
MAX_CODE_FILES = 600
MAX_CODE_BYTES = 512 * 1024
CODE_LINES = 150
MAX_CODE_CHARS = 5500
MAX_LISTED_TESTS = 8
MAX_FOUND_LINES = 40
# A secret written in code is never read out
SECRET_IN_CODE = re.compile(
    r"(?i)((?:api[_-]?key|secret|password|passwd|token|credential)\w*[\"']?\s*[=:]\s*)([\"'])([^\"'\n]{6,})\2"
)
ODOO_AUTHOR = re.compile(r"\bOdoo S\.?A\.?\b|^Odoo$", re.IGNORECASE)


# --------------------------------------------------------------------- settings

def _ancestor(node, tag):
    node = node.getparent()
    while node is not None and node.tag != tag:
        node = node.getparent()
    return node


def settings_layout(env):
    """:return: the apps of the Settings screen, as this person has it, in
    their language: ``[{"app", "label", "settings": [{"label", "help",
    "block", "fields", "links", "documentation", "words"}]}]``. ``words``
    holds the English of it too, as the model searches in English."""
    apps = _layout(env)
    if (env.context.get("lang") or env.user.lang or "en_US") != "en_US":
        english = {app["app"]: app for app in _layout(env(context=dict(env.context, lang="en_US")))}
        for app in apps:
            other = english.get(app["app"])
            if other and len(other["settings"]) == len(app["settings"]):
                for setting, translated in zip(app["settings"], other["settings"]):
                    setting["words"] += " " + translated["words"]
                    setting["block_words"] += " " + translated["block_words"]
    return apps


def _layout(env):
    Settings = env[SETTINGS]
    arch = etree.fromstring(Settings.sudo().get_view(view_type="form")["arch"])
    fields = Settings._fields
    apps = []
    for app in arch.iter("app"):
        settings = []
        for setting in app.iter("setting"):
            names = [node.get("name") for node in setting.iter("field") if node.get("name") in fields]
            names = list(dict.fromkeys(names))
            links = [node.get("string") for node in setting.iter("button") if node.get("string")]
            label = setting.get("string") or (fields[names[0]].string if names else "") or setting.get("id") or ""
            block = _ancestor(setting, "block")
            help_text = (setting.get("help") or (fields[names[0]].help if names else "") or "").strip()
            block_title = (block.get("title") or block.get("name") or "") if block is not None else ""
            settings.append({
                "words": " ".join((app.get("string") or "", block_title, label, help_text, " ".join(names))).casefold(),
                "block_words": block_title.casefold(),
                "label": label.strip(),
                # What the screen says of it, or else what its field says
                "help": help_text,
                "block": block_title,
                "fields": names,
                "links": links,
                "documentation": setting.get("documentation") or "",
            })
        if settings:
            apps.append({
                "app": app.get("name") or "",
                "label": app.get("string") or app.get("data-string") or app.get("name") or "",
                "settings": settings,
            })
    return apps


def find_app(apps, name):
    """:return: the app named by its technical name or by its label"""
    wanted = name.strip().casefold()
    for app in apps:
        if wanted in (app["app"].casefold(), app["label"].casefold()):
            return app
    listed = ", ".join(f"{app['label']} ({app['app']})" for app in apps[:MAX_APPS])
    raise Refused(f"No app {name!r} has settings here. Apps with settings: {listed}.")


def can_read_values(env):
    return env.user.has_group("base.group_system")


def is_secret_setting(field):
    return bool(SECRET_KEY.search(field.name) or (field.type == "char" and SECRET_KEY.search(field.string or "")))


def setting_value(env, field, value):
    """:return: the value of a setting as the person reads it"""
    if is_secret_setting(field):
        return "(hidden)"
    if field.type == "boolean":
        return "on" if value else "off"
    if value in (False, None, ""):
        return "not set"
    if field.type == "selection":
        return str(dict(field._description_selection(env)).get(value, value))
    if field.type == "many2one":
        try:
            return cut(env[field.comodel_name].browse(value).display_name, 80)
        except Exception:  # noqa: BLE001
            return f"#{value}"
    if field.type == "many2many":
        ids = value if isinstance(value, list) else []
        if ids and isinstance(ids[0], (list, tuple)):
            ids = ids[0][2] if len(ids[0]) > 2 else []
        try:
            return cut(", ".join(env[field.comodel_name].browse(ids).mapped("display_name")), 160) or "none"
        except Exception:  # noqa: BLE001
            return f"{len(ids)} record(s)"
    return cut(str(value), 120)


def current_values(env, names):
    """:return: ``{field: value as read}`` for an administrator, None for others"""
    if not can_read_values(env) or not names:
        return None
    Settings = env[SETTINGS]
    # A record of the settings as the screen opens them: what is related to the
    # company (the fiscal country, the default taxes) is not in default_get
    record = Settings.new(Settings.default_get(list({*names, "company_id"})))
    return {name: setting_value(env, Settings._fields[name], _raw(record, name)) for name in names}


def _raw(record, name):
    """:return: the value of ``name`` as default_get gives it: ids for relations"""
    value = record[name]
    field = record._fields[name]
    if field.type == "many2one":
        return value.id or False
    if field.type in ("many2many", "one2many"):
        return value.ids
    return value


def settings_menu(env, app):
    """:return: the menu that opens the settings of ``app``, or the one of the
    Settings app, for this person; the second value tells which"""
    general = env["ir.ui.menu"]
    for menu in _visible_menus(env):
        # The menu is theirs to open; what its action opens is read for them
        action = menu.action.sudo()
        if action._name != "ir.actions.act_window" or action.res_model != SETTINGS:
            continue
        try:
            context = ast.literal_eval(action.context or "{}")
        except (ValueError, SyntaxError):
            context = {}
        if isinstance(context, dict) and context.get("module") == app:
            return menu, True
        if not general and not menu.parent_id:
            general = menu
    return general, False


def describe_setting(setting, values):
    line = f"- {setting['label']}"
    if setting["help"]:
        line += f": {setting['help']}"
    if setting["fields"]:
        shown = []
        for name in setting["fields"]:
            shown.append(f"{name}={values[name]}" if values and name in values else name)
        line += f" [{', '.join(shown)}]"
    if setting["links"]:
        line += f" (buttons: {', '.join(setting['links'])})"
    if setting["documentation"]:
        line += f" (doc: {setting['documentation']})"
    return line


@base.register
class ReadSettings(base.Tool):
    name = "read_settings"
    description = (
        "The settings of the apps installed here, read from the Settings screen itself: each with "
        "its help, the fields that hold it, the buttons next to it that open what it configures, "
        "and its page of the documentation. For an administrator, the value each has here. "
        "Without arguments: the apps that have settings. With app: every setting of that app. "
        "With words: the settings of any app whose label or help holds them, such as "
        "\"routes\", \"lots\", \"overtime\". Read them before advising how to set an app up, so "
        "that the advice starts from how it is set here."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "app": {
                "type": "string",
                "pattern": APP_PATTERN,
                "description": "Technical name (stock, account, hr_payroll) or label of the app.",
            },
            "words": {
                "type": "string",
                "minLength": 2,
                "maxLength": 80,
                "description": "Words of the label or the help of settings, in English or in the language of the interface.",
            },
            "block": {
                "type": "string",
                "maxLength": 80,
                "description": "With app: only this block of its settings, for an app too long to read at once.",
            },
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        apps = settings_layout(env)
        if not apps:
            raise Refused("No app has settings this person can see.")
        note = "" if can_read_values(env) else (
            "\nValues are shown to administrators only: ask the person how it is set, or say "
            "where to look.")
        if arguments.get("app"):
            app = find_app(apps, arguments["app"])
            settings = app["settings"]
            if arguments.get("block"):
                wanted = arguments["block"].strip().casefold()
                settings = [setting for setting in settings if wanted in setting["block_words"]]
                if not settings:
                    blocks = ", ".join(dict.fromkeys(setting["block"] for setting in app["settings"]))
                    raise Refused(f"{app['label']} has no block {arguments['block']!r}. Blocks: {blocks}.")
            names = [name for setting in settings for name in setting["fields"]]
            values = current_values(env, names)
            menu, is_own = settings_menu(env, app["app"])
            where = menu.complete_name if menu else "Settings"
            lines = [f"Settings of {app['label']} ({app['app']}), opened from {where}:"]
            size = len(lines[0])
            block, left = None, []
            for setting in settings:
                if left or size > MAX_SETTINGS_CHARS:
                    if setting["block"] not in left:
                        left.append(setting["block"])
                    continue
                if setting["block"] != block:
                    block = setting["block"]
                    lines.append(f"## {block}")
                    size += len(block) + 4
                lines.append(describe_setting(setting, values))
                size += len(lines[-1]) + 1
            if left:
                lines.append("More, read with app and block: " + ", ".join(left) + ".")
            return base.ToolResult("\n".join(lines) + note)
        if arguments.get("words"):
            words = [word.casefold() for word in arguments["words"].split()]
            found = []
            for app in apps:
                for setting in app["settings"]:
                    if all(word in setting["words"] for word in words):
                        found.append((app, setting))
            if not found:
                raise Refused(
                    f"No setting holds {arguments['words']!r}. Try other words, or read an app with app.")
            found = found[:MAX_FOUND_SETTINGS]
            values = current_values(env, [name for _app, setting in found for name in setting["fields"]])
            lines = [f"{len(found)} setting(s):"]
            for app, setting in found:
                lines.append(f"{app['label']} ({app['app']}) / {setting['block']}")
                lines.append(describe_setting(setting, values))
            return base.ToolResult("\n".join(lines) + note)
        lines = ["Apps with settings here:"]
        for app in apps[:MAX_APPS]:
            lines.append(f"- {app['label']} ({app['app']}): {len(app['settings'])} settings")
        return base.ToolResult("\n".join(lines))


# ---------------------------------------------------------------- configuration

def app_roots(env):
    Menu = env["ir.ui.menu"]
    visible = Menu._visible_menu_ids()
    return Menu.browse(visible).filtered(lambda menu: not menu.parent_id)


def find_app_root(env, name):
    wanted = name.strip().casefold()
    roots = app_roots(env)
    for root in roots:
        module = (root.get_external_id().get(root.id) or "").split(".")[0]
        if wanted == module.casefold():
            return root
    for root in roots:
        names = {root.name.casefold(), root.with_context(lang="en_US").name.casefold()}
        if wanted in names:
            return root
    for root in roots:
        if wanted in root.with_context(lang="en_US").name.casefold() or wanted in root.name.casefold():
            return root
    listed = ", ".join(sorted(roots.mapped("name")))
    raise Refused(f"No app {name!r} is open to this person. Apps: {listed}.")


def configuration_menu(env, root):
    """:return: the Configuration menu of an app, as this person sees it"""
    visible = set(env["ir.ui.menu"]._visible_menu_ids())
    for child in root.child_id:
        if child.id not in visible:
            continue
        english = child.with_context(lang="en_US").name.casefold()
        if english.startswith("config"):
            return child
    return env["ir.ui.menu"]


def leaf_menus(env, menu):
    visible = set(env["ir.ui.menu"]._visible_menu_ids())
    leaves = env["ir.ui.menu"].search([("id", "child_of", menu.id), ("action", "!=", False)])
    return leaves.filtered(lambda leaf: leaf.id in visible and menu_path(env, leaf))


def literal_domain(text):
    try:
        domain = ast.literal_eval(text or "[]")
    except (ValueError, SyntaxError):
        return []
    return domain if isinstance(domain, list) else []


def list_columns(model):
    arch = etree.fromstring(model.get_view(view_type="list")["arch"])
    names = []
    for node in arch.iter("field"):
        name = node.get("name")
        hidden = node.get("column_invisible") in ("1", "True") or node.get("invisible") in ("1", "True")
        if name and not hidden and name in model._fields and name not in names:
            names.append(name)
    return names


@base.register
class ReadConfiguration(base.Tool):
    name = "read_configuration"
    description = (
        "What is defined in an app here, read from its Configuration menu: with app, each menu "
        "of it with the kind of record it holds, how many there are and the first of them "
        "(warehouses, locations, routes, operation types, journals, taxes, salary structures, "
        "work schedules...). With menu_id, the records of one of these menus with the columns "
        "of its list. Read it before advising how to set an app up: the advice names what "
        "exists here, and what is missing."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "app": {
                "type": "string",
                "pattern": APP_PATTERN,
                "description": "Technical name (stock, account, hr) or name of the app.",
            },
            "menu_id": {"type": "integer", "minimum": 1},
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        if arguments.get("menu_id"):
            return self._records(env, arguments["menu_id"])
        if not arguments.get("app"):
            raise Refused("Give the app, or the menu_id of one of its configuration menus.")
        root = find_app_root(env, arguments["app"])
        config = configuration_menu(env, root)
        if not config:
            raise Refused(f"{root.name} has no Configuration menu this person can open.")
        lines = [f"{config.complete_name}:"]
        for menu in leaf_menus(env, config)[:MAX_CONFIG_MENUS]:
            lines.append(self._summary(env, menu))
        return base.ToolResult("\n".join(lines))

    def _summary(self, env, menu):
        action = menu.action.sudo()
        head = f"- {menu.name} (menu_id {menu.id}"
        if action._name != "ir.actions.act_window":
            return head + ")"
        if action.res_model == SETTINGS:
            return head + "): the settings, read with read_settings"
        try:
            model = get_model(env, action.res_model)
        except Refused:
            return head + f", {action.res_model}): not readable through Nudge"
        domain = literal_domain(action.domain)
        try:
            count = model.search_count(domain)
            names = model.search(domain, limit=5).mapped("display_name")
        except Exception:  # noqa: BLE001
            return head + f", {action.res_model})"
        shown = ", ".join(cut(name, 50) for name in names)
        more = "…" if count > len(names) else ""
        return head + f", {action.res_model}): {count}" + (f" — {shown}{more}" if names else "")

    def _records(self, env, menu_id):
        menu = _visible_menus(env).filtered(lambda candidate: candidate.id == menu_id)
        action = menu.action.sudo()
        if not menu or action._name != "ir.actions.act_window":
            raise Refused("This menu does not exist, is not for this person, or opens no list.")
        if action.res_model == SETTINGS:
            raise Refused("This menu opens the settings: read them with read_settings.")
        model = get_model(env, action.res_model)
        names = [name for name in list_columns(model) if name != "id"]
        readable = []
        for name in names:
            try:
                get_fields(model, [name])
            except Refused:
                continue
            readable.append(name)
        readable = readable[:MAX_COLUMNS]
        records = model.search(literal_domain(action.domain), limit=MAX_CONFIG_ROWS)
        rows = records.read(readable) if readable else []
        lines = [f"{menu.complete_name} ({model._name}), {model.search_count(literal_domain(action.domain))} record(s):"]
        for record, row in zip(records, rows or [{}] * len(records)):
            shown = {}
            for name in readable:
                value = format_value(env, model._fields[name], row.get(name))
                if isinstance(value, dict):
                    value = value.get("name") or value.get("count")
                if value not in (None, "", False):
                    shown[name] = value
            lines.append(f"- {cut(record.display_name, 80)} (id {record.id}): {base.dump(shown)}")
        return base.ToolResult("\n".join(lines))


# ---------------------------------------------------------------------- modules

def is_from_odoo(module):
    return bool(ODOO_AUTHOR.search(module.author or ""))


def installed_module(env, name):
    module = env["ir.module.module"].sudo().search([("name", "=", name), ("state", "=", "installed")], limit=1)
    if not module:
        raise Refused(f"No module {name!r} is installed here. describe_module without a name lists the custom ones.")
    return module


def registered(env, module, model):
    """:return: the records of ``model`` the module registered"""
    data = env["ir.model.data"].sudo().search([("module", "=", module), ("model", "=", model)])
    return env[model].sudo().browse(data.mapped("res_id")).exists()


@base.register
class DescribeModule(base.Tool):
    name = "describe_module"
    description = (
        "What an installed module is and what it adds: its purpose, what it depends on, the "
        "kinds of records it creates, the fields it adds to other models, its menus, actions, "
        "scheduled actions, groups and reports. Without module: the modules installed here that "
        "are not made by Odoo, custom ones and from third parties. Read it to explain or to "
        "build on a module of this business, and propose solutions with what it already has."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "module": {"type": "string", "pattern": MODULE_PATTERN, "description": "Technical name."},
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        if not arguments.get("module"):
            modules = env["ir.module.module"].sudo().search([("state", "=", "installed")], order="name")
            custom = modules.filtered(lambda module: not is_from_odoo(module))
            if not custom:
                return base.ToolResult("Every module installed here is made by Odoo.")
            lines = [f"{len(custom)} installed module(s) not made by Odoo:"]
            for module in custom[:MAX_APPS]:
                summary = f": {cut(module.summary, 120)}" if module.summary else ""
                lines.append(f"- {module.name} — {module.shortdesc}{summary} (by {module.author or 'unknown'})")
            return base.ToolResult("\n".join(lines))
        module = installed_module(env, arguments["module"])
        name = module.name
        lines = [
            f"{module.shortdesc} ({name}), version {module.installed_version or module.latest_version}, "
            f"by {module.author or 'unknown'}" + ("" if is_from_odoo(module) else ", not made by Odoo"),
        ]
        if module.summary:
            lines.append(f"Summary: {module.summary}")
        description = cut(html2plaintext(module.description_html or "") or module.description or "", 800)
        if description:
            lines.append(f"Description: {description}")
        depends = module.dependencies_id.mapped("name")
        if depends:
            lines.append(f"Depends on: {', '.join(depends)}")
        models = registered(env, name, "ir.model")
        # A module has the xml id of every model it touches: it created the
        # ones it defined first
        created = models.filtered(
            lambda model: model.model in env and getattr(env[model.model], "_original_module", "") == name)
        if created:
            lines.append("Kinds of records it creates: " + ", ".join(
                f"{model.model} ({model.name})" for model in created[:MAX_MODULE_ITEMS]))
        extended = models - created
        if extended:
            lines.append("Kinds of records it extends: " + ", ".join(extended[:MAX_MODULE_ITEMS].mapped("model")))
        visible = set(env["ir.ui.menu"]._visible_menu_ids())
        menus = registered(env, name, "ir.ui.menu").filtered(lambda menu: menu.id in visible and menu.action)
        if menus:
            lines.append("Menus: " + "; ".join(menus[:MAX_MODULE_ITEMS].mapped("complete_name")))
        actions = registered(env, name, "ir.actions.act_window")
        if actions:
            lines.append("Windows: " + "; ".join(f"{action.name} ({action.res_model})" for action in actions[:MAX_MODULE_ITEMS]))
        server = registered(env, name, "ir.actions.server").filtered(lambda action: action.usage != "ir_cron")
        if server:
            lines.append("Server actions: " + "; ".join(f"{action.name} ({action.model_id.model})" for action in server[:MAX_MODULE_ITEMS]))
        crons = registered(env, name, "ir.cron")
        if crons:
            lines.append("Scheduled actions: " + "; ".join(
                f"{cron.name} (every {cron.interval_number} {cron.interval_type}{'' if cron.active else ', off'})"
                for cron in crons[:MAX_MODULE_ITEMS]))
        groups = registered(env, name, "res.groups")
        if groups:
            lines.append("Groups: " + "; ".join(groups[:MAX_MODULE_ITEMS].mapped("display_name")))
        reports = registered(env, name, "ir.actions.report")
        if reports:
            lines.append("Reports: " + "; ".join(f"{report.name} ({report.model})" for report in reports[:MAX_MODULE_ITEMS]))
        if env.user.has_group("base.group_system"):
            lines.append("Its code can be read with read_module_code.")
        fields = registered(env, name, "ir.model.fields").filtered(lambda field: field.model not in created.mapped("model"))
        if fields:
            by_model = {}
            for field in fields[:MAX_MODULE_FIELDS]:
                relation = f" → {field.relation}" if field.relation else ""
                by_model.setdefault(field.model, []).append(f"{field.name} ({field.field_description}, {field.ttype}{relation})")
            lines.append("Fields it adds to other models:")
            lines += [f"- {model}: {', '.join(items)}" for model, items in by_model.items()]
        return base.ToolResult("\n".join(lines))


def module_root(env, name):
    installed_module(env, name)
    path = get_module_path(name, display_warning=False)
    if not path:
        raise Refused(f"The files of {name!r} cannot be found on this server.")
    return os.path.realpath(path)


def code_files(root):
    found = []
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = sorted(d for d in subdirectories if d not in SKIPPED_DIRECTORIES and not d.startswith("."))
        for name in sorted(files):
            if name.endswith(CODE_EXTENSIONS):
                full = os.path.join(directory, name)
                if os.path.islink(full):
                    continue
                found.append(os.path.relpath(full, root).replace(os.sep, "/"))
                if len(found) >= MAX_CODE_FILES:
                    return found
    return found


def hide_secrets(text):
    return SECRET_IN_CODE.sub(lambda match: f"{match.group(1)}{match.group(2)}***{match.group(2)}", text)


@base.register
class ReadModuleCode(base.Tool):
    name = "read_module_code"
    group = "base.group_system"
    description = (
        "The code of an installed module, for an administrator: without path, its files; with "
        "path, the lines of one file, numbered, from from_line; with find, the lines of its "
        "files that hold the words. Read it to understand what a custom module really does "
        "before explaining it or proposing to build on it. Secrets written in code are hidden."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["module"],
        "properties": {
            "module": {"type": "string", "pattern": MODULE_PATTERN},
            "path": {"type": "string", "pattern": r"[A-Za-z0-9_./-]{1,200}"},
            "from_line": {"type": "integer", "minimum": 1},
            "find": {"type": "string", "minLength": 2, "maxLength": 80},
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        name = arguments["module"]
        root = module_root(env, name)
        files = code_files(root)
        if arguments.get("find"):
            wanted = arguments["find"].casefold()
            found = []
            for relative in files:
                full = os.path.join(root, relative)
                if os.path.getsize(full) > MAX_CODE_BYTES:
                    continue
                with open(full, encoding="utf-8", errors="replace") as handle:
                    for number, line in enumerate(handle, start=1):
                        if wanted in line.casefold():
                            found.append(f"{relative}:{number}: {cut(hide_secrets(line.strip()), 160)}")
                            if len(found) >= MAX_FOUND_LINES:
                                break
                if len(found) >= MAX_FOUND_LINES:
                    break
            return base.ToolResult("\n".join(found) if found else f"No line of {name} holds {arguments['find']!r}.")
        if not arguments.get("path"):
            # One line per folder: a whole module fits in what a tool may return
            folders = {}
            for relative in files:
                folder, _sep, file_name = relative.rpartition("/")
                folders.setdefault(folder, []).append(file_name)
            # Tests are many and rarely what is asked: a few names and a count, so
            # the folders that matter still fit in what a tool may return
            lines, test_lines = [], []
            for folder, names in folders.items():
                is_tests = folder.rpartition("/")[2] == "tests"
                if is_tests and len(names) > MAX_LISTED_TESTS:
                    names = names[:MAX_LISTED_TESTS] + [f"... and {len(names) - MAX_LISTED_TESTS} more (use find)"]
                (test_lines if is_tests else lines).append(
                    f"- {folder + '/' if folder else '(top)'}: {', '.join(names)}")
            # The folders of tests last: what a tool may return cuts the end, not the code
            lines += test_lines
            listed = "\n".join(lines)
            # What it is for, as its own manifest says: told only the files, a
            # small model guessed the purpose of nudge_spreadsheet from their names
            module = env["ir.module.module"].sudo().search([("name", "=", name)], limit=1)
            said = " ".join(filter(None, (module.shortdesc, module.summary)))
            described = " ".join((module.description or "").split())[:600]
            about = f"What {name} is, in its manifest: {said}. {described}\n" if said or described else ""
            return base.ToolResult(f"{about}Files of {name}, by folder (read one with path=folder/file):\n{listed}")
        relative = arguments["path"].strip("/")
        full = os.path.realpath(os.path.join(root, relative))
        if not full.startswith(root + os.sep) or relative not in files:
            raise Refused(f"{name} has no file {relative!r} that can be read. Call without path for the list.")
        if os.path.getsize(full) > MAX_CODE_BYTES:
            raise Refused(f"{relative} is too big to read whole: use find.")
        with open(full, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
        start = max(1, arguments.get("from_line") or 1)
        shown, size = [], 0
        for number in range(start, min(len(lines), start + CODE_LINES - 1) + 1):
            text = f"{number}: {hide_secrets(lines[number - 1])}"
            size += len(text) + 1
            if size > MAX_CODE_CHARS:
                break
            shown.append(text)
        end = start + len(shown) - 1
        more = f" More from line {end + 1}." if end < len(lines) else ""
        return base.ToolResult(f"{name}/{relative}, lines {start}-{end} of {len(lines)}.{more}\n" + "\n".join(shown))


# --------------------------------------------------------------------- changing

def settings_fields(apps):
    return {name: (app, setting) for app in apps for setting in app["settings"] for name in setting["fields"]}


@base.register
class ChangeSettings(base.Action):
    name = "change_settings"
    kind = "write"
    description = (
        "Change settings of the apps, for an administrator when the setup is open to them, as "
        "the Settings screen does: the person is shown each setting as it is and as it will be, "
        "and says yes or no; you read their answer as the result. Fields come from "
        "read_settings. A setting whose field starts with module_ installs an app: say so "
        "before. Nudge never turns one off, which would uninstall an app: send the person to "
        "the Settings screen for that."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["changes"],
        "properties": {
            "changes": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_CHANGES,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["field", "value"],
                    "properties": {
                        "field": {"type": "string", "pattern": r"[a-z][a-z0-9_]{1,80}"},
                        "value": {
                            "type": "string",
                            "maxLength": 200,
                            "description": "true or false for a setting that is on or off; the value, a choice or a name otherwise.",
                        },
                    },
                },
            },
        },
    }

    def is_available(self, env, mode=base.MODE_TALK):
        return super().is_available(env, mode) and env["nudge.conversation"]._can_administer()

    def _read(self, env, changes):
        if not env["nudge.conversation"]._can_administer():
            raise Refused("Settings are changed by an administrator, when the setup is open to them.")
        Settings = env[SETTINGS]
        known = settings_fields(settings_layout(env))
        values, shown = {}, []
        before = current_values(env, [change["field"] for change in changes if change["field"] in known]) or {}
        for change in changes:
            name = change["field"]
            if name not in known:
                raise Refused(f"{name!r} is not a setting of the Settings screen. Read them with read_settings.")
            if name in values:
                raise Refused(f"The setting {name!r} is given twice.")
            field = Settings._fields[name]
            if is_secret_setting(field):
                raise Refused(f"{name} holds a secret: the person sets it on the Settings screen.")
            value, text = read_value(env, field, change["value"])
            if name.startswith("module_") and not value:
                raise Refused(
                    f"Turning {name} off uninstalls an app and may lose its data: the person does it "
                    "on the Settings screen, or in Apps.")
            values[name] = value
            app, setting = known[name]
            label = setting["label"] if len(setting["fields"]) == 1 else f"{setting['label']}: {field.string}"
            if name.startswith("module_"):
                module = env["ir.module.module"].sudo().search([("name", "=", name[len("module_"):])], limit=1)
                label = env._("Install the app %s", module.shortdesc or name[len("module_"):])
            after = setting_value(env, field, value) if field.type != "many2one" else text
            shown.append({
                "field": name,
                "label": f"{app['label']} / {label}",
                "value": after,
                "before": before.get(name),
            })
        return values, shown

    @refusing
    def run(self, env, arguments, screen):
        values, shown = self._read(env, arguments["changes"])
        return base.ToolResult(
            "The person is being shown which settings are about to change.",
            pending={
                "summary": env._("Change the settings"),
                "res_model": SETTINGS,
                "res_ids": [],
                "record_names": ", ".join(line["label"] for line in shown)[:500],
                "values": {"values": values},
                "is_sensitive": True,
                "preview": {"records": [], "lines": shown},
            },
        )

    def execute(self, env, step):
        if not env["nudge.conversation"]._can_administer():
            return base.ToolResult(
                "Settings are changed by an administrator, when the setup is open to them.", is_error=True)
        values = (step.values or {}).get("values") or {}
        known = settings_fields(settings_layout(env))
        if not values or any(name not in known for name in values):
            return base.ToolResult("These settings cannot be changed.", is_error=True)
        result = env[SETTINGS].create(values).execute()
        reload = result if isinstance(result, dict) and result.get("type") else {
            "type": "ir.actions.client", "tag": "reload"}
        return base.ToolResult(
            "The settings were changed: " + ", ".join(sorted(values)) + ". The screen is reloaded.",
            event={"type": "changed", "model": SETTINGS, "ids": [], "action": reload},
            records=(SETTINGS, [], []),
        )


# --------------------------------------------------------------------- showing

def settings_steps(env, app, entries, save_text=""):
    """:param app: an app of ``settings_layout``
    :param entries: ``[{"field", "text"}]``, settings of that app
    :return: the steps of the pointer to them, then to Save
    :raise Refused: when this person cannot open them"""
    if not env.user.has_group("base.group_system"):
        raise Refused(
            "Only an administrator opens the Settings: tell the person, give the steps in words, "
            "and say who can do it.")
    fields = {name for setting in app["settings"] for name in setting["fields"]}
    for entry in entries:
        if entry["field"] not in fields:
            raise Refused(f"{entry['field']!r} is not a setting of {app['label']}. Read them with read_settings.")
    menu, is_own = settings_menu(env, app["app"])
    path = menu_path(env, menu) if menu else []
    if not path:
        raise Refused("The settings cannot be opened by this person.")
    steps = menu_steps(env, path, [])
    if not is_own:
        steps.append({
            "target": {"type": "settings_app", "name": app["app"], "label": app["label"]},
            "text": env._("Open the settings of “%s”.", app["label"]),
            "advance": "auto",
        })
    for entry in entries:
        steps.append({"target": {"type": "field", "name": entry["field"]},
                      "text": entry["text"].strip(), "advance": "auto"})
    steps.append({"target": {"type": "control", "name": "save"},
                  "text": (save_text or "").strip() or env._("Save the settings."),
                  "advance": "auto"})
    return steps


@base.register
class ShowSettings(base.Tool):
    name = "show_settings"
    terminal = True
    # Only administrators open the Settings
    group = "base.group_system"
    description = (
        "Show the person, on their screen, where settings of an app are and how to set them: "
        "Nudge's pointer goes through the menus to the settings of the app, then to each "
        "setting, then to Save, step by step as they click. Use it when they ask to be shown "
        "how to set something up, rather than told or having it done. Fields come from "
        "read_settings. Each text says what to do and why, in the language the person wrote in."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["app", "settings"],
        "properties": {
            "app": {"type": "string", "pattern": APP_PATTERN},
            "settings": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_SHOWN_SETTINGS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["field", "text"],
                    "properties": {
                        "field": {"type": "string", "pattern": r"[a-z][a-z0-9_]{1,80}"},
                        "text": {"type": "string", "minLength": 1, "maxLength": 400},
                    },
                },
            },
            "save_text": {"type": "string", "maxLength": 300},
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        if not env.user.has_group("base.group_system"):
            raise Refused(
                "Only an administrator opens the Settings: tell the person, give the steps in words, "
                "and say who can do it.")
        app = find_app(settings_layout(env), arguments["app"])
        steps = settings_steps(env, app, arguments["settings"], arguments.get("save_text") or "")
        return base.ToolResult(
            f"The way to the settings of {app['label']} is being shown on the person's screen, "
            f"{len(steps)} step(s).",
            event={"type": "guidance", "steps": steps},
        )
