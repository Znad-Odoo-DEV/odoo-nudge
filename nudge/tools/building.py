"""Changing the screens, in Developer mode: a change to a view as an
inheritance, a button on a form, a new field.

A view of a module is never edited: every change is an extension view that
Odoo combines with it, the way Studio works, and that can be switched off or
deleted on its own. Before the card, the change is applied in a savepoint
that is rolled back: what Odoo would refuse is told, and what the person is
shown is the difference it makes to the screen.
"""
import difflib
import re

from lxml import etree

from odoo import Command
from odoo.exceptions import UserError, ValidationError

from . import base
from .developer import Building, check_developing, ir_model
from .records import MODEL_PATTERN, Refused, cut, get_model, get_records, refusing

# Where what Nudge makes is registered, so that it is found again and exported
CUSTOM_MODULE = "nudge_custom"
CUSTOM_MODULES = (CUSTOM_MODULE, "studio_customization", "__export__")
VIEW_TYPES = ("form", "list", "kanban", "search")
POSITIONS = ("inside", "after", "before", "replace", "attributes")
FIELD_TYPES = ("char", "text", "html", "integer", "float", "monetary", "boolean", "date", "datetime",
               "selection", "many2one", "many2many")
# What a snippet of a view may hold: the elements people put in views by hand
ALLOWED_TAGS = {
    "field", "label", "group", "div", "span", "p", "button", "page", "notebook", "separator",
    "header", "sheet", "widget", "strong", "em", "b", "i", "br", "h1", "h2", "h3", "h4", "h5",
    "ul", "li", "a", "attribute", "newline", "footer", "templates", "t", "kanban", "progressbar",
    "control", "create", "filter", "searchpanel",
}
MAX_ARCH_LINES = 110
MAX_ARCH_CHARS = 5200
MAX_FOUND_LINES = 40
MAX_DIFF_LINES = 80
MAX_SNIPPET_CHARS = 4000
MAX_SELECTION = 30
NAME_PATTERN = re.compile(r"^x_[a-z0-9_]{1,60}$")


class DryRun(Exception):
    """Raised to leave the savepoint: nothing the trial did is kept."""


# ------------------------------------------------------------------ reading

def pretty(arch):
    """:return: the arch as numbered lines, the same each time"""
    try:
        node = etree.fromstring(arch.encode("utf-8") if isinstance(arch, str) else arch)
        text = etree.tostring(node, pretty_print=True, encoding="unicode")
    except (etree.XMLSyntaxError, ValueError):
        text = arch if isinstance(arch, str) else arch.decode("utf-8", "replace")
    return text.rstrip("\n").splitlines()


def combined_arch(model, view_id, view_type):
    """:return: ``(root view, combined arch)`` as this person gets the screen"""
    model.env.registry.clear_cache("templates")
    result = model.get_view(view_id or None, view_type)
    return model.env["ir.ui.view"].browse(result["id"]), result["arch"]


def view_of(env, model, arguments):
    """:return: the root view asked for, or the default one of the type"""
    view_type = arguments.get("view_type") or "form"
    view_id = arguments.get("view_id")
    if view_id:
        view = get_records(get_model(env, "ir.ui.view"), [view_id])
        if view.model != model._name:
            raise Refused(f"View #{view.id} is a view of {view.model}, not of {model._name}.")
        view_type = view.type
    try:
        root, arch = combined_arch(model, view_id, view_type)
    except (UserError, ValueError, AttributeError) as error:
        raise Refused(f"No {view_type} view of {model._name} can be read: {cut(str(error), 300)}") from None
    return root, arch, view_type


def describe_views(env, root):
    """:return: lines naming the root view and the extensions combined with it"""
    data = {}
    views = root | root._get_inheriting_views()
    for view in views:
        xml_id = view.get_external_id().get(view.id) or ""
        module = xml_id.split(".")[0] if xml_id else ""
        data[view.id] = (xml_id, module)
    lines = [f"View #{root.id} {root.name!r} ({root.type}), {data[root.id][0] or 'no xml id'}:"]
    for view in views - root:
        xml_id, module = data[view.id]
        origin = "made here" if not module or module in CUSTOM_MODULES else f"module {module}"
        state = "" if view.active else " (off)"
        lines.append(f"- extension #{view.id} {view.name!r}, {origin}{state}")
    return lines


@base.register
class ReadView(base.Tool):
    name = "read_view"
    modes = (base.MODE_DEV,)
    group = "base.group_system"
    description = (
        "The definition of a screen of a model, as the person gets it: the combined view, with "
        "its lines numbered, and the extension views that make it up, each with where it came "
        "from. Read it before changing a screen, to write the xpath of the element to change "
        "(fields by their technical name: //field[@name='partner_id'], a page by its name, "
        "//header, //sheet, //group[1]). With find: only the lines that hold the words, with "
        "their numbers. With from_line: the lines from there on."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["model"],
        "properties": {
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "view_type": {"type": "string", "enum": list(VIEW_TYPES), "description": "Default: form."},
            "view_id": {"type": "integer", "minimum": 1, "description": "A view other than the default one."},
            "find": {"type": "string", "minLength": 2, "maxLength": 80},
            "from_line": {"type": "integer", "minimum": 1},
        },
    }

    def is_available(self, env, mode=base.MODE_TALK):
        return super().is_available(env, mode) and env["nudge.conversation"]._can_develop()

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        model = get_model(env, arguments["model"])
        root, arch, _view_type = view_of(env, model, arguments)
        lines = describe_views(env, root)
        numbered = pretty(arch)
        if arguments.get("find"):
            wanted = arguments["find"].casefold()
            found = [f"{number}: {line.strip()}" for number, line in enumerate(numbered, start=1) if wanted in line.casefold()]
            lines.append(f"{len(found)} line(s) hold {arguments['find']!r}:" if found else f"No line holds {arguments['find']!r}.")
            lines += found[:MAX_FOUND_LINES]
            return base.ToolResult("\n".join(lines))
        start = max(1, arguments.get("from_line") or 1)
        shown, size = [], 0
        for number in range(start, min(len(numbered), start + MAX_ARCH_LINES - 1) + 1):
            text = f"{number}: {numbered[number - 1]}"
            size += len(text) + 1
            if size > MAX_ARCH_CHARS:
                break
            shown.append(text)
        end = start + len(shown) - 1
        more = f" More from line {end + 1}." if end < len(numbered) else ""
        lines.append(f"Lines {start}-{end} of {len(numbered)}.{more}")
        return base.ToolResult("\n".join(lines + shown))


# ----------------------------------------------------------------- changing

def check_snippet(node):
    """:raise Refused: when the snippet holds what a view does not"""
    for element in node.iter():
        if not isinstance(element.tag, str):
            raise Refused("A view holds elements, not comments or processing instructions.")
        if element.tag not in ALLOWED_TAGS:
            raise Refused(f"<{element.tag}> is not an element of a view. Use fields, groups, pages, buttons, labels, divs.")
        for name, value in element.attrib.items():
            # Spaces, tabs and control characters inside a scheme still make it one
            plain = re.sub(r"[\s\x00-\x1f]+", "", value).lower()
            if (name.startswith("t-") or name.lower().startswith("on")
                    or any(scheme in plain for scheme in ("javascript:", "vbscript:", "data:text/html"))):
                raise Refused(f"The attribute {name!r} is not allowed in a view made here.")


def inheritance_arch(xpath, position, snippet):
    """:return: the arch of the extension view, checked"""
    try:
        root = etree.fromstring(f"<data><xpath expr={_quote(xpath)} position={_quote(position)}>{snippet}</xpath></data>")
    except etree.XMLSyntaxError as error:
        raise Refused(f"The change is not well-formed XML: {cut(str(error), 200)}") from None
    for element in root.iter("xpath"):
        for child in element:
            check_snippet(child)
    if position == "attributes":
        for child in root.find("xpath"):
            if child.tag != "attribute":
                raise Refused("With position attributes, the change holds <attribute name=\"...\">value</attribute> elements only.")
    return etree.tostring(root, encoding="unicode")


def _quote(text):
    return '"' + str(text).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;") + '"'


def preview_change(env, model, root, view_type, arch_before, name, xpath, position, snippet):
    """Apply the change in a savepoint that is rolled back.
    :return: ``(values of the extension view, lines of the difference)``
    :raise Refused: with what Odoo would refuse"""
    arch = inheritance_arch(xpath, position, snippet)
    values = {
        "name": name,
        "model": model._name,
        "type": view_type,
        "inherit_id": root.id,
        "mode": "extension",
        "priority": 99,
        "arch_db": arch,
    }
    diff = []
    try:
        with env.cr.savepoint():
            try:
                env["ir.ui.view"].create(values)
                env.registry.clear_cache("templates")
                _root, arch_after = combined_arch(model, root.id, view_type)
            except (ValidationError, UserError, ValueError) as error:
                raise Refused(f"Odoo refuses this change: {cut(' '.join(str(error).split()), 500)}") from None
            diff = [
                line.rstrip("\n") for line in difflib.unified_diff(
                    pretty(arch_before), pretty(arch_after), "before", "after", n=2, lineterm="")
            ][2:]
            raise DryRun()
    except DryRun:
        pass
    finally:
        env.invalidate_all()
        env.registry.clear_cache("templates")
    if len(diff) > MAX_DIFF_LINES:
        diff = diff[:MAX_DIFF_LINES] + [f"… {len(diff) - MAX_DIFF_LINES} more line(s)"]
    return values, diff


def register(env, record, name):
    """What Nudge makes carries an xml id of its own module, so that it is
    found again, exported, and never overwritten by an update."""
    slug = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")[:60] or "record"
    env["ir.model.data"].sudo().create({
        "module": CUSTOM_MODULE,
        "name": f"{slug}_{record.id}",
        "model": record._name,
        "res_id": record.id,
        "noupdate": True,
    })


def make_view(env, values):
    view = env["ir.ui.view"].create(values)
    register(env, view, f"view_{values['model']}_{values['type']}")
    env.registry.clear_cache("templates")
    return view


RELOAD = {"type": "ir.actions.client", "tag": "reload"}


@base.register
class PrepareViewChange(Building):
    name = "prepare_view_change"
    kind = "create"
    description = (
        "Change a screen of a model: add, move, replace or hide an element of its form, list, "
        "kanban or search view, as an extension view that Odoo combines with the original, "
        "the way Studio works; the original is never edited. Give the xpath of the element "
        "(from read_view), the position (inside, after, before, replace, attributes) and the "
        "change: elements of a view, or with position attributes, <attribute "
        "name=\"invisible\">...</attribute> elements. Nothing is changed by this call: the "
        "change is tried, what Odoo would refuse is told, and the person is shown the change "
        "and the difference it makes to the screen, then says yes or no; you read their "
        "answer as the result."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["model", "xpath", "position", "change"],
        "properties": {
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "view_type": {"type": "string", "enum": list(VIEW_TYPES), "description": "Default: form."},
            "view_id": {"type": "integer", "minimum": 1},
            "name": {"type": "string", "maxLength": 120, "description": "What the change is for, in a few words."},
            "xpath": {"type": "string", "minLength": 1, "maxLength": 300},
            "position": {"type": "string", "enum": list(POSITIONS)},
            "change": {"type": "string", "minLength": 1, "maxLength": MAX_SNIPPET_CHARS},
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        model = get_model(env, arguments["model"])
        root, arch_before, view_type = view_of(env, model, arguments)
        name = (arguments.get("name") or "").strip() or env._("Change by Nudge: %s", model._description)
        values, diff = preview_change(
            env, model, root, view_type, arch_before, name,
            arguments["xpath"].strip(), arguments["position"], arguments["change"].strip())
        lines = [
            {"label": env._("Model"), "value": model._description},
            {"label": env._("View"), "value": f"{root.name} ({view_type})"},
            {"label": env._("Where"), "value": f"{arguments['xpath'].strip()} · {arguments['position']}"},
            {"label": env._("Change"), "value": arguments["change"].strip(), "code": True},
            {"label": env._("Result"), "value": "\n".join(diff) or env._("No visible difference"), "code": True},
        ]
        return base.ToolResult(
            "The person is being shown the change and the difference it makes.",
            pending={
                "summary": env._("Change the screen: %s", name),
                "res_model": "ir.ui.view",
                "res_ids": [],
                "record_names": name,
                "values": {"view": values},
                "is_sensitive": arguments["position"] == "replace",
                "preview": {"records": [], "lines": lines},
            },
        )

    def execute(self, env, step):
        check_developing(env)
        values = (step.values or {}).get("view") or {}
        if not values.get("inherit_id") or not values.get("arch_db"):
            return base.ToolResult("This change cannot be made.", is_error=True)
        view = make_view(env, values)
        return base.ToolResult(
            f"Made: extension view #{view.id} {view.name!r} on {view.model}. The screen is reloaded. "
            "It is in Settings ‣ Technical ‣ Views; switched off with active false, or deleted.",
            event={"type": "changed", "model": "ir.ui.view", "ids": view.ids, "action": RELOAD},
            records=("ir.ui.view", view.ids, [view.name]),
        )


@base.register
class PrepareButton(Building):
    name = "prepare_button"
    kind = "create"
    description = (
        "Put a button on the form of a model that runs a server action: in the header, next "
        "to the other buttons. Make the action first with prepare_server_action, or take an "
        "existing one from read_automations; an automation rule has no button: it runs on an "
        "event. Nothing is changed by this call: the person is shown the button and the "
        "difference it makes to the form, and says yes or no; you read their answer as the result."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["model", "label", "action_id"],
        "properties": {
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "label": {"type": "string", "minLength": 1, "maxLength": 60},
            "action_id": {"type": "integer", "minimum": 1, "description": "The server action it runs."},
            "primary": {"type": "boolean", "description": "Highlighted, as the main button of the form."},
            "invisible": {
                "type": "string",
                "maxLength": 200,
                "description": "When it is hidden, as a condition on the fields: state != 'draft'.",
            },
            "view_id": {"type": "integer", "minimum": 1},
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        model = get_model(env, arguments["model"])
        action = get_records(get_model(env, "ir.actions.server"), [arguments["action_id"]])
        if action.model_name != model._name:
            raise Refused(f"Server action #{action.id} is on {action.model_name}, not on {model._name}.")
        root, arch_before, _view_type = view_of(env, model, dict(arguments, view_type="form"))
        label = arguments["label"].strip()
        attributes = f'name="{action.id}" type="action" string={_quote(label)}'
        if arguments.get("primary"):
            attributes += ' class="oe_highlight"'
        if (arguments.get("invisible") or "").strip():
            attributes += f" invisible={_quote(arguments['invisible'].strip())}"
        button = f"<button {attributes}/>"
        tree = etree.fromstring(arch_before.encode("utf-8"))
        if tree.find(".//header") is not None:
            xpath, position, snippet = "//header", "inside", button
        elif tree.find(".//sheet") is not None:
            xpath, position, snippet = "//sheet", "before", f"<header>{button}</header>"
        else:
            xpath, position, snippet = "//form/*[1]", "before", f"<header>{button}</header>"
        name = env._("Button %(label)s on %(model)s", label=label, model=model._description)
        values, diff = preview_change(env, model, root, "form", arch_before, name, xpath, position, snippet)
        lines = [
            {"label": env._("Model"), "value": model._description},
            {"label": env._("Button"), "value": label},
            {"label": env._("Runs"), "value": f"{action.name} (#{action.id}, {action.state})"},
            {"label": env._("Shown"), "value": env._("unless %s", arguments["invisible"].strip())
             if (arguments.get("invisible") or "").strip() else env._("always")},
            {"label": env._("Result"), "value": "\n".join(diff) or env._("No visible difference"), "code": True},
        ]
        return base.ToolResult(
            "The person is being shown the button and the difference it makes to the form.",
            pending={
                "summary": env._("Add the button “%s”", label),
                "res_model": "ir.ui.view",
                "res_ids": [],
                "record_names": name,
                "values": {"view": values},
                "is_sensitive": False,
                "preview": {"records": [], "lines": lines},
            },
        )

    execute = PrepareViewChange.execute


# ------------------------------------------------------------------- fields

def field_name_for(label, given=""):
    if given:
        name = given.strip().lower()
        if not NAME_PATTERN.match(name):
            raise Refused("A field made here is named x_ followed by letters, digits and underscores.")
        return name
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40] or "field"
    return "x_" + slug


@base.register
class PrepareField(Building):
    name = "prepare_field"
    kind = "create"
    description = (
        "Add a field to a model, the way Studio does: its label, its type, and for a "
        "many2one or many2many the model it points to, for a selection its values. Odoo "
        "names it x_...; it is stored, and added to the form of the model in the same card, "
        "after the field you name or in the first group. Nothing is changed by this call: the "
        "person is shown the field and where it goes, and says yes or no; you read their "
        "answer as the result."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["model", "label", "type"],
        "properties": {
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "label": {"type": "string", "minLength": 1, "maxLength": 80},
            "type": {"type": "string", "enum": list(FIELD_TYPES)},
            "name": {"type": "string", "maxLength": 63, "description": "Technical name, x_...; made from the label otherwise."},
            "help": {"type": "string", "maxLength": 400},
            "required": {"type": "boolean"},
            "readonly": {"type": "boolean"},
            "relation": {"type": "string", "pattern": MODEL_PATTERN, "description": "For many2one and many2many: the model pointed to."},
            "selection": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_SELECTION,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["value", "label"],
                    "properties": {
                        "value": {"type": "string", "minLength": 1, "maxLength": 60},
                        "label": {"type": "string", "minLength": 1, "maxLength": 80},
                    },
                },
                "description": "For a selection: its values and their labels.",
            },
            "on_form": {"type": "boolean", "description": "Add it to the form (default: yes)."},
            "after": {"type": "string", "pattern": r"[a-z_][a-z0-9_]*", "description": "The field of the form it goes after."},
            "view_id": {"type": "integer", "minimum": 1},
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        model = get_model(env, arguments["model"])
        if model._abstract or model._transient or not model._auto:
            raise Refused(f"No field can be added to {model._name}.")
        label = arguments["label"].strip()
        name = field_name_for(label, arguments.get("name") or "")
        if name in model._fields:
            raise Refused(f"{model._name} already has a field {name!r}.")
        kind = arguments["type"]
        values = {
            "name": name,
            "model_id": ir_model(env, model).id,
            "model": model._name,
            "field_description": label,
            "ttype": kind,
            "state": "manual",
            "help": (arguments.get("help") or "").strip() or False,
            "required": bool(arguments.get("required")),
            "readonly": bool(arguments.get("readonly")),
            "store": True,
            "copied": True,
        }
        lines = [
            {"label": env._("Model"), "value": model._description},
            {"label": env._("Field"), "value": label},
            {"label": env._("Technical name"), "value": name},
            {"label": env._("Type"), "value": kind},
        ]
        if kind in ("many2one", "many2many"):
            if not arguments.get("relation"):
                raise Refused(f"A {kind} field names the model it points to: relation.")
            # Any model of the business, or of Odoo (users, companies): what
            # the field holds is read later, under the rules of that model
            related = env.get(arguments["relation"])
            if related is None or related._abstract or related._transient:
                raise Refused(f"There is no model {arguments['relation']!r} a field can point to.")
            values["relation"] = related._name
            if kind == "many2one":
                values["on_delete"] = "set null"
            lines.append({"label": env._("Points to"), "value": related._description})
        elif arguments.get("relation"):
            raise Refused("relation goes with many2one and many2many only.")
        if kind == "selection":
            choices = arguments.get("selection") or []
            if not choices:
                raise Refused("A selection field lists its values: selection.")
            seen = set()
            for choice in choices:
                if choice["value"] in seen:
                    raise Refused(f"The value {choice['value']!r} is given twice.")
                seen.add(choice["value"])
            values["selection_ids"] = [
                Command.create({"value": choice["value"], "name": choice["label"], "sequence": index})
                for index, choice in enumerate(choices)]
            lines.append({"label": env._("Values"), "value": ", ".join(f"{c['label']} ({c['value']})" for c in choices)})
        elif arguments.get("selection"):
            raise Refused("selection goes with a selection field only.")
        if kind == "monetary" and not any(f in model._fields for f in ("currency_id", "x_currency_id")):
            raise Refused(f"{model._name} has no currency field: use float, or add x_currency_id first.")
        if values["required"]:
            lines.append({"label": env._("Required"), "value": True})
        if values["help"]:
            lines.append({"label": env._("Help"), "value": values["help"]})
        on_form = arguments.get("on_form", True)
        view = None
        if on_form:
            root, arch_before, _view_type = view_of(env, model, dict(arguments, view_type="form"))
            tree = etree.fromstring(arch_before.encode("utf-8"))
            after = (arguments.get("after") or "").strip()
            if after:
                if tree.find(f".//field[@name='{after}']") is None:
                    raise Refused(f"The form of {model._name} has no field {after!r}: read_view lists them.")
                xpath, position, where = f"//field[@name='{after}']", "after", env._("after %s", after)
            elif tree.find(".//sheet//group") is not None:
                xpath, position, where = "//sheet//group[1]", "inside", env._("in the first group of the form")
            elif tree.find(".//sheet") is not None:
                xpath, position, where = "//sheet", "inside", env._("at the end of the form")
            else:
                xpath, position, where = "/form", "inside", env._("at the end of the form")
            # The field is not there yet: the form is checked once it is, when made
            view = {
                "name": env._("Field %(field)s on %(model)s", field=label, model=model._description),
                "model": model._name,
                "type": "form",
                "inherit_id": root.id,
                "mode": "extension",
                "priority": 99,
                "arch_db": inheritance_arch(xpath, position, f'<field name="{name}"/>'),
            }
            lines.append({"label": env._("On the form"), "value": f"{where} ({root.name})"})
        else:
            lines.append({"label": env._("On the form"), "value": env._("not added")})
        return base.ToolResult(
            "The person is being shown the field and where it goes.",
            pending={
                "summary": env._("Add the field “%s”", label),
                "res_model": "ir.model.fields",
                "res_ids": [],
                "record_names": f"{label} ({name})",
                "values": {"field": values, "view": view},
                "is_sensitive": True,
                "preview": {"records": [], "lines": lines},
            },
        )

    def execute(self, env, step):
        check_developing(env)
        values = dict((step.values or {}).get("field") or {})
        if not values.get("name") or values.get("state") != "manual":
            return base.ToolResult("This field cannot be made.", is_error=True)
        if values.get("selection_ids"):
            values["selection_ids"] = [tuple(command) if isinstance(command, list) else command for command in values["selection_ids"]]
        field = env["ir.model.fields"].create(values)
        register(env, field, f"field_{values['model']}_{values['name']}")
        told = f"Made: field {field.field_description!r} ({field.name}, {field.ttype}) on {field.model}."
        view_values = (step.values or {}).get("view")
        if view_values:
            try:
                with env.cr.savepoint():
                    view = make_view(env, view_values)
                told += f" Added to the form by extension view #{view.id}."
            except (ValidationError, UserError, ValueError) as error:
                told += (" It could not be added to the form: " + cut(" ".join(str(error).split()), 300)
                         + " Add it with prepare_view_change.")
        return base.ToolResult(
            told + " The screen is reloaded. It is in Settings ‣ Technical ‣ Fields.",
            event={"type": "changed", "model": "ir.model.fields", "ids": field.ids, "action": RELOAD},
            records=("ir.model.fields", field.ids, [field.field_description]),
        )
