"""A complete Odoo module drafted for a developer, as a ZIP, in Developer mode.

The description, in any dialect, becomes a structured spec (the model that
talks to the person fills the arguments of prepare_module_scaffold). The
person reads the spec on a card; after their yes the files are built in
memory from string templates with the conventions of the Odoo version they
chose (17, 18 or 19), checked statically, and offered as a download. Nothing
is written to the server: not in an addons path, not in a temporary folder,
and Nudge never installs the module; the ZIP is built again, byte for byte,
from the spec kept in the step, when the person downloads it.
"""
import ast
import csv
import io
import keyword
import re
import zipfile
from xml.sax.saxutils import escape, quoteattr

from lxml import etree

from odoo import _, fields as odoo_fields
from odoo import release

from . import base
from .developer import Building, check_developing
from .records import Refused, cut, refusing

VERSIONS = ("17.0", "18.0", "19.0")
FIELD_TYPES = ("char", "text", "html", "integer", "float", "monetary", "boolean", "date", "datetime", "selection",
               "many2one", "one2many", "many2many")
RELATIONAL = ("many2one", "one2many", "many2many")
FIELD_CLASSES = {
    "char": "Char", "text": "Text", "html": "Html", "integer": "Integer", "float": "Float", "monetary": "Monetary",
    "boolean": "Boolean", "date": "Date", "datetime": "Datetime", "selection": "Selection", "many2one": "Many2one",
    "one2many": "One2many", "many2many": "Many2many",
}
SNAKE = re.compile(r"[a-z][a-z0-9_]*")  # used with fullmatch: "$" would let a newline through
MODEL_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
# Names Odoo already gives to every record, and what the chatter adds
RESERVED = frozenset({
    "id", "create_date", "write_date", "create_uid", "write_uid", "display_name", "__last_update", "message_ids",
    "message_follower_ids", "message_partner_ids", "activity_ids", "message_is_follower", "has_message",
    "website_message_ids", "message_attachment_count", "message_main_attachment_id", "activity_state",
    "activity_user_id", "activity_type_id", "activity_date_deadline", "activity_summary", "my_activity_date_deadline",
})
CHATTER_FIELDS = ("message_follower_ids", "activity_ids", "message_ids")
VALID_MANIFEST_KEYS = frozenset({
    "name", "version", "category", "summary", "description", "author", "website", "license", "depends", "data",
    "demo", "installable", "application", "auto_install", "assets", "external_dependencies", "sequence", "images",
    "maintainer", "post_init_hook", "uninstall_hook", "pre_init_hook", "bootstrap", "countries", "icon",
    "live_test_url", "price", "currency", "support", "cloc_exclude", "demo_xml", "update_xml", "init_xml",
    "data_xml",
})
COMPUTE_NAMES = frozenset({"record", "sum", "len", "min", "max", "round", "abs", "any", "all", "str", "int",
                           "float", "bool", "sorted", "set", "list", "range"})
MAX_MODELS = 12
MAX_FIELDS = 40
ZIP_TIME = (2026, 1, 1, 0, 0, 0)
SCAFFOLD_TOOL = "prepare_module_scaffold"

SPEC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "models"],
    "properties": {
        "name": {"type": "string", "minLength": 2, "maxLength": 60,
                 "description": "Technical name of the module: lower case, digits and underscores."},
        "title": {"type": "string", "maxLength": 80, "description": "Name shown in Apps."},
        "summary": {"type": "string", "maxLength": 200},
        "models": {
            "type": "array", "minItems": 1, "maxItems": MAX_MODELS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "fields"],
                "properties": {
                    "name": {"type": "string", "maxLength": 80, "description": "Dotted, such as my_module.thing."},
                    "description": {"type": "string", "maxLength": 80},
                    "fields": {
                        "type": "array", "minItems": 1, "maxItems": MAX_FIELDS,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "type"],
                            "properties": {
                                "name": {"type": "string", "maxLength": 64, "description": "snake_case, unique in the model."},
                                "type": {"type": "string", "enum": list(FIELD_TYPES)},
                                "string": {"type": "string", "maxLength": 80, "description": "The label."},
                                "required": {"type": "boolean"},
                                "relation": {"type": "string", "maxLength": 80,
                                             "description": "Model of a many2one, one2many or many2many."},
                                "inverse": {"type": "string", "maxLength": 64,
                                            "description": "For a one2many: the many2one of the other model that points here."},
                                "selection": {
                                    "type": "array", "maxItems": 20,
                                    "items": {"type": "object", "additionalProperties": False, "required": ["key", "label"],
                                              "properties": {"key": {"type": "string", "maxLength": 40},
                                                             "label": {"type": "string", "maxLength": 80}}},
                                },
                                "compute": {
                                    "type": "object", "additionalProperties": False, "required": ["expression"],
                                    "description": "A computed field: a Python expression over record.",
                                    "properties": {"depends": {"type": "array", "maxItems": 10, "items": {"type": "string", "maxLength": 80}},
                                                   "expression": {"type": "string", "maxLength": 300}},
                                },
                            },
                        },
                    },
                    "sql_constraints": {
                        "type": "array", "maxItems": 6,
                        "items": {"type": "object", "additionalProperties": False, "required": ["name", "definition"],
                                  "properties": {"name": {"type": "string", "maxLength": 60},
                                                 "definition": {"type": "string", "maxLength": 200,
                                                                "description": "such as UNIQUE(name) or CHECK(qty >= 0)"},
                                                 "message": {"type": "string", "maxLength": 200}}},
                    },
                    "python_constraints": {
                        "type": "array", "maxItems": 6,
                        "items": {"type": "object", "additionalProperties": False, "required": ["condition", "message"],
                                  "properties": {"fields": {"type": "array", "maxItems": 10, "items": {"type": "string", "maxLength": 64}},
                                                 "condition": {"type": "string", "maxLength": 300,
                                                               "description": "Python expression over record, true when the data is wrong."},
                                                 "message": {"type": "string", "maxLength": 200}}},
                    },
                    "workflow": {
                        "type": "array", "maxItems": 8,
                        "description": "States in order: the model gets a state field, a status bar and a button per step.",
                        "items": {"type": "object", "additionalProperties": False, "required": ["key", "label"],
                                  "properties": {"key": {"type": "string", "maxLength": 40},
                                                 "label": {"type": "string", "maxLength": 80}}},
                    },
                },
            },
        },
        "groups": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 40},
                   "description": "Security groups from the least to the most privileged (default: user, manager)."},
        "menus": {
            "type": "array", "maxItems": 20,
            "items": {"type": "object", "additionalProperties": False, "required": ["name"],
                      "properties": {"name": {"type": "string", "maxLength": 80},
                                     "model": {"type": "string", "maxLength": 80, "description": "Opens the list of this model."},
                                     "parent": {"type": "string", "maxLength": 80, "description": "Name of another menu of the list."}}},
        },
        "chatter": {"type": "boolean", "description": "Messages and activities on the records (default yes)."},
    },
}


# ----------------------------------------------------------------- templates

def render(template, **values):
    """:return: ``template`` with each ``{{ name }}`` taken from ``values``: no logic,
    no evaluation, an unknown name is an error"""
    def put(found):
        key = found.group(1)
        if key not in values:
            raise KeyError(key)
        return str(values[key])
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", put, template)


MANIFEST = '''{
    "name": {{ title }},
    "summary": {{ summary }},
    "version": "{{ version }}",
    "category": "Customizations",
    "author": "Raghad Ebrahim",
    "license": "LGPL-3",
    "depends": {{ depends }},
    "data": {{ data }},
    "demo": {{ demo }},
    "application": True,
    "installable": True,
}
'''
MODEL_FILE = '''from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class {{ klass }}(models.Model):
    _name = "{{ model }}"
    _description = {{ description }}
{{ body }}
'''
VIEW_FILE = '''<?xml version="1.0" encoding="utf-8"?>
<odoo>
{{ records }}
</odoo>
'''
TEST_FILE = '''from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class Test{{ klass }}(TransactionCase):
{{ body }}
'''
PO_HEADER = '''# Translation of Odoo Server.
# This file contains the translation of the following modules:
# \t* {{ module }}
#
msgid ""
msgstr ""
"Project-Id-Version: Odoo Server {{ series }}\\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: \\n"
"Language: ar\\n"
"Plural-Forms: nplurals=6; plural=n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : n%100>=3 && n%100<=10 ? 3 : n%100>=11 ? 4 : 5;\\n"
'''


# ---------------------------------------------------------------- the spec

def _label(name):
    for tail in ("_ids", "_id"):
        if name.endswith(tail) and len(name) > len(tail):
            name = name[:-len(tail)]
    return name.replace("_", " ").strip().title()


def _under(model):
    return model.replace(".", "_")


def _klass(model):
    return "".join(part.title() for part in re.split(r"[._]", model))


def _py(text):
    return repr(str(text))


def _compute_name(field):
    return f"_compute_{field}"


def read_spec(env, arguments, version=None):
    """Check the spec and complete it.
    :return: the spec with its defaults, as it is kept in the step
    :raise Refused: naming what is wrong, the field included"""
    spec = {key: arguments[key] for key in ("name", "title", "summary", "models", "groups", "menus", "chatter")
            if key in arguments}
    module = (spec["name"] or "").strip()
    if not SNAKE.fullmatch(module) or keyword.iskeyword(module):
        raise Refused(f"The module name {module!r} must be snake_case: lower case letters, digits and underscores, "
                      "starting with a letter.")
    if "ir.module.module" in env and env["ir.module.module"].sudo().search_count([("name", "=", module)]):
        raise Refused(f"A module named {module} exists in this database: choose another name.")
    spec["name"] = module
    spec["title"] = (spec.get("title") or _label(module)).strip()
    spec["summary"] = (spec.get("summary") or spec["title"]).strip()
    spec["chatter"] = spec.get("chatter", True)
    groups = [group.strip() for group in (spec.get("groups") or ["user", "manager"])]
    for group in groups:
        if not SNAKE.fullmatch(group):
            raise Refused(f"The group name {group!r} must be snake_case.")
    if len(set(groups)) != len(groups):
        raise Refused("A group is named twice: " + ", ".join(sorted({g for g in groups if groups.count(g) > 1})) + ".")
    spec["groups"] = groups
    names = [model["name"].strip() for model in spec["models"]]
    for model in spec["models"]:
        model["name"] = model["name"].strip()
        if not MODEL_NAME.match(model["name"]):
            raise Refused(f"The model name {model['name']!r} must be dotted lower case, such as {module}.thing.")
        if model["name"] in env:
            raise Refused(f"The model {model['name']} exists already in this database: the module would redefine it.")
        if names.count(model["name"]) > 1:
            raise Refused(f"The model {model['name']} is twice in the spec.")
        model["description"] = (model.get("description") or _label(model["name"].split(".")[-1])).strip()
    files = {}
    for model in spec["models"]:
        stem = _under(model["name"])
        if stem.startswith(module + "_"):
            stem = stem[len(module) + 1:]
        if stem in files.values():
            raise Refused(f"The models {model['name']} and another have the same file name: rename one.")
        files[model["name"]] = stem
    for model in spec["models"]:
        model["file"] = files[model["name"]]
        _read_fields(env, spec, model, names)
        _read_constraints(model)
        _read_workflow(model)
    _read_menus(spec)
    return spec


def _read_fields(env, spec, model, names):
    seen = set()
    fields_ = model["fields"]
    # The state of a workflow is a field like another: a clash is told
    state = model.get("workflow")
    for field in fields_:
        name = (field["name"] or "").strip()
        where = f"{model['name']}.{name}"
        if not SNAKE.fullmatch(name) or keyword.iskeyword(name):
            raise Refused(f"The field {where} must be snake_case and not a Python keyword.")
        if name in seen:
            raise Refused(f"The field {name} is twice in the model {model['name']}: field names are unique in a model.")
        seen.add(name)
        if name in RESERVED:
            raise Refused(f"The field {where} has a name Odoo uses already: choose another.")
        if name == "state" and state:
            raise Refused(f"The field {where} clashes with the state field of the workflow: remove one.")
        field["name"] = name
        field["string"] = (field.get("string") or _label(name)).strip()
        if field["type"] in RELATIONAL:
            relation = (field.get("relation") or "").strip()
            if not relation:
                raise Refused(f"The field {where} is a {field['type']}: it needs relation, the model it points to.")
            if relation not in names and relation not in env:
                raise Refused(f"The field {where} points to {relation}, which is neither in this module nor in this database.")
            field["relation"] = relation
        if field["type"] == "one2many":
            inverse = (field.get("inverse") or "").strip()
            target = next((m for m in spec["models"] if m["name"] == field["relation"]), None)
            if not inverse:
                raise Refused(f"The field {where} is a one2many: it needs inverse, the many2one of {field['relation']}.")
            if target is not None and not any(
                    other["name"] == inverse and other["type"] == "many2one" and other.get("relation") == model["name"]
                    for other in target["fields"]):
                raise Refused(f"The field {where} has inverse {inverse}, but {field['relation']} has no many2one "
                              f"{inverse} that points to {model['name']}: add it.")
            field["inverse"] = inverse
        if field["type"] == "selection":
            options = field.get("selection") or []
            keys = [option["key"] for option in options]
            if not options:
                raise Refused(f"The field {where} is a selection: it needs selection, its keys and labels.")
            if len(set(keys)) != len(keys):
                raise Refused(f"The selection {where} has a key twice.")
        compute = field.get("compute")
        if compute:
            try:
                tree = ast.parse(compute["expression"].strip(), mode="eval")
            except SyntaxError as error:
                raise Refused(f"The computation of {where} is not Python: {error.msg}.") from None
            unknown = sorted({node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
                              and node.id not in COMPUTE_NAMES and not isinstance(node.ctx, ast.Store)})
            comprehension = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
                             and isinstance(node.ctx, ast.Store)}
            unknown = [name for name in unknown if name not in comprehension]
            if unknown:
                raise Refused(f"The computation of {where} uses {', '.join(unknown)}: only record and plain functions.")
    for field in fields_:
        compute = field.get("compute")
        if compute:
            for depends in compute.get("depends") or []:
                if depends.split(".")[0] not in seen:
                    raise Refused(f"The computation of {model['name']}.{field['name']} depends on "
                                  f"{depends}, which is not a field of the model.")
    if not [field for field in fields_ if field["type"] == "char"] and "name" not in seen:
        raise Refused(f"The model {model['name']} has no char field to name its records: add a name.")
    if any(field["type"] == "monetary" for field in fields_) and "currency_id" not in seen:
        fields_.insert(0, {"name": "currency_id", "type": "many2one", "relation": "res.currency",
                           "string": "Currency", "_auto": "currency"})
    return None


def _read_constraints(model):
    names = {field["name"] for field in model["fields"]}
    for constraint in model.get("sql_constraints") or []:
        if not SNAKE.fullmatch(constraint["name"]):
            raise Refused(f"The constraint {constraint['name']!r} of {model['name']} must be snake_case.")
        constraint.setdefault("message", "")
        constraint["message"] = constraint["message"] or constraint["name"]
    for constraint in model.get("python_constraints") or []:
        for name in constraint.get("fields") or []:
            if name not in names:
                raise Refused(f"The constraint on {name} of {model['name']} names a field the model does not have.")
        try:
            ast.parse(constraint["condition"].strip(), mode="eval")
        except SyntaxError as error:
            raise Refused(f"The condition {constraint['condition']!r} of {model['name']} is not Python: {error.msg}.") from None


def _read_workflow(model):
    states = model.get("workflow") or []
    keys = [state["key"] for state in states]
    for key in keys:
        if not SNAKE.fullmatch(key):
            raise Refused(f"The state {key!r} of {model['name']} must be snake_case.")
    if len(set(keys)) != len(keys):
        raise Refused(f"A state of {model['name']} is twice in its workflow.")
    if states and len(states) < 2:
        raise Refused(f"The workflow of {model['name']} needs two states at least.")


def _read_menus(spec):
    names = {model["name"] for model in spec["models"]}
    menus = spec.get("menus") or []
    if not menus:
        menus = [{"name": model["description"], "model": model["name"]} for model in spec["models"]]
    titles = [menu["name"] for menu in menus]
    for menu in menus:
        if menu.get("model") and menu["model"] not in names:
            raise Refused(f"The menu {menu['name']!r} opens {menu['model']}, which is not a model of this module.")
        if menu.get("parent") and menu["parent"] not in titles:
            raise Refused(f"The menu {menu['name']!r} has parent {menu['parent']!r}, which is not in the menus.")
        if titles.count(menu["name"]) > 1:
            raise Refused(f"The menu {menu['name']!r} is twice in the menus.")
    spec["menus"] = menus


# ----------------------------------------------------------------- the files

def _model(spec, name):
    return next((model for model in spec["models"] if model["name"] == name), None)


def _field_source(field, model, spec, version):
    kind = field["type"]
    args = [_py(field["string"])]
    named = {}
    if kind == "many2one":
        args = [_py(field["relation"]), "string=" + _py(field["string"])]
        if field.get("_auto") == "currency":
            named["default"] = "lambda self: self.env.company.currency_id"
        elif field.get("required"):
            named["ondelete"] = _py("restrict")
    elif kind == "one2many":
        args = [_py(field["relation"]), _py(field["inverse"]), "string=" + _py(field["string"])]
    elif kind == "many2many":
        args = [_py(field["relation"]), "string=" + _py(field["string"])]
    elif kind == "selection":
        choices = ", ".join(f"({_py(option['key'])}, {_py(option['label'])})" for option in field["selection"])
        args = [f"[{choices}]", "string=" + _py(field["string"])]
        if field.get("required"):
            named["default"] = _py(field["selection"][0]["key"])
    elif kind == "monetary":
        args = ["string=" + _py(field["string"])]
        named["currency_field"] = _py("currency_id")
    else:
        args = ["string=" + _py(field["string"])]
    if field.get("required") and not field.get("compute"):
        named["required"] = "True"
    if field.get("compute"):
        named["compute"] = _py(_compute_name(field["name"]))
        named["store"] = "True"
    if spec["chatter"] and kind in ("char", "selection", "many2one", "monetary", "integer", "float", "date") \
            and field.get("_auto") is None and not field.get("compute"):
        named["tracking"] = "True"
    parts = args + [f"{key}={value}" for key, value in named.items()]
    return f"    {field['name']} = fields.{FIELD_CLASSES[kind]}({', '.join(parts)})"


def _model_source(model, spec, version):
    lines = []
    if spec["chatter"]:
        lines.append('    _inherit = ["mail.thread", "mail.activity.mixin"]')
    char = next((f["name"] for f in model["fields"] if f["type"] == "char"), None)
    if not any(f["name"] == "name" for f in model["fields"]) and char:
        lines.append(f'    _rec_name = "{char}"')
    if model.get("sql_constraints") and version == "19.0":
        lines.append("")
    if model.get("sql_constraints") and version != "19.0":
        rows = ",\n".join(f"        ({_py(c['name'])}, {_py(c['definition'])}, {_py(c['message'])})"
                          for c in model["sql_constraints"])
        lines.append(f"    _sql_constraints = [\n{rows},\n    ]")
    lines.append("")
    workflow = model.get("workflow") or []
    if workflow:
        choices = ", ".join(f"({_py(s['key'])}, {_py(s['label'])})" for s in workflow)
        lines.append(f"    state = fields.Selection([{choices}], string=\"Status\", default={_py(workflow[0]['key'])}, "
                     f"required=True, copy=False{', tracking=True' if spec['chatter'] else ''})")
    for field in model["fields"]:
        lines.append(_field_source(field, model, spec, version))
    if model.get("sql_constraints") and version == "19.0":
        lines.append("")
        for c in model["sql_constraints"]:
            lines.append(f"    _{c['name']} = models.Constraint({_py(c['definition'])}, {_py(c['message'])})")
    for field in model["fields"]:
        compute = field.get("compute")
        if compute:
            depends = ", ".join(_py(d) for d in compute.get("depends") or [])
            lines += ["", f"    @api.depends({depends})" if depends else "    @api.depends()",
                      f"    def {_compute_name(field['name'])}(self):", "        for record in self:",
                      f"            record.{field['name']} = {compute['expression'].strip()}"]
    for index, constraint in enumerate(model.get("python_constraints") or [], start=1):
        names = ", ".join(_py(n) for n in constraint.get("fields") or [])
        lines += ["", f"    @api.constrains({names})", f"    def _check_rule_{index}(self):", "        for record in self:",
                  f"            if {constraint['condition'].strip()}:",
                  f"                raise ValidationError(_({_py(constraint['message'])}))"]
    for current, following in zip(workflow, workflow[1:]):
        lines += ["", f"    def action_set_{following['key']}(self):", "        for record in self:",
                  f"            record.state = {_py(following['key'])}", "        return True"]
    return "\n".join(lines)


def _groups_xml(spec, version):
    module = spec["name"]
    lines = [f'    <record id="module_category_{module}" model="ir.module.category">',
             f'        <field name="name">{escape(spec["title"])}</field>',
             '        <field name="sequence">100</field>', "    </record>"]
    if version == "19.0":
        lines += [f'    <record id="privilege_{module}" model="res.groups.privilege">',
                  f'        <field name="name">{escape(spec["title"])}</field>',
                  f'        <field name="category_id" ref="module_category_{module}"/>', "    </record>"]
    previous = None
    for index, group in enumerate(spec["groups"]):
        implied = "base.group_user" if previous is None else previous
        lines += [f'    <record id="group_{module}_{group}" model="res.groups">',
                  f'        <field name="name">{escape(_label(group))}</field>',
                  (f'        <field name="privilege_id" ref="privilege_{module}"/>' if version == "19.0"
                   else f'        <field name="category_id" ref="module_category_{module}"/>'),
                  f'        <field name="implied_ids" eval="[(4, ref(\'{implied}\'))]"/>']
        if index == len(spec["groups"]) - 1:
            users = "user_ids" if version == "19.0" else "users"
            lines.append(f'        <field name="{users}" eval="[(4, ref(\'base.user_root\')), '
                         f'(4, ref(\'base.user_admin\'))]"/>')
        lines.append("    </record>")
        previous = f"group_{module}_{group}"
    return "\n".join(lines)


def _access_csv(spec):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["id", "name", "model_id:id", "group_id:id", "perm_read", "perm_write", "perm_create", "perm_unlink"])
    module = spec["name"]
    for model in spec["models"]:
        for index, group in enumerate(spec["groups"]):
            last = index == len(spec["groups"]) - 1
            writer.writerow([f"access_{_under(model['name'])}_{group}", f"{model['name']} {group}",
                             f"model_{_under(model['name'])}", f"group_{module}_{group}", 1, 1, 1, 1 if last else 0])
    return out.getvalue()


def _simple(model, spec, limit):
    """:return: the fields to put in a list or a search: those that read in one cell"""
    kinds = ("char", "selection", "many2one", "integer", "float", "monetary", "date", "datetime", "boolean")
    return [field for field in model["fields"]
            if field["type"] in kinds and field.get("_auto") is None][:limit]


def _views_xml(model, spec, version):
    module, name = spec["name"], _under(model["name"])
    tree = "tree" if version == "17.0" else "list"
    group_tag = "<group>" if version == "19.0" else '<group expand="0" string="Group By">'
    search = [f'                <field name="{field["name"]}"/>' for field in _simple(model, spec, 6)
              if field["type"] in ("char", "many2one", "selection")]
    groupby = [f'                    <filter name="group_{field["name"]}" string={quoteattr(field["string"])} '
               f'context="{{\'group_by\': \'{field["name"]}\'}}"/>'
               for field in model["fields"] if field["type"] in ("selection", "many2one") and field.get("_auto") is None][:4]
    if model.get("workflow"):
        groupby.insert(0, '                    <filter name="group_state" string="Status" context="{\'group_by\': \'state\'}"/>')
    cells = [f'                <field name="{field["name"]}"/>' for field in _simple(model, spec, 6)]
    if model.get("workflow"):
        cells.append('                <field name="state" widget="badge"/>')
    form_head = ""
    if model.get("workflow"):
        buttons = []
        for current, following in zip(model["workflow"], model["workflow"][1:]):
            buttons.append(
                f'                <button name="action_set_{following["key"]}" type="object" '
                f'string={quoteattr(following["label"])} class="oe_highlight" '
                f'invisible="state != {_py(current["key"])}"/>')
        keys = ",".join(state["key"] for state in model["workflow"])
        form_head = ("            <header>\n" + "\n".join(buttons)
                     + f'\n                <field name="state" widget="statusbar" statusbar_visible="{keys}"/>\n'
                     "            </header>\n")
    simple = [field for field in _simple(model, spec, 12) if field["name"] != "name"]
    half = (len(simple) + 1) // 2
    left = [f'                    <field name="{field["name"]}"/>' for field in simple[:half]]
    right = [f'                    <field name="{field["name"]}"/>' for field in simple[half:]]
    title = ('                <h1><field name="name" placeholder="Name"/></h1>\n'
             if any(field["name"] == "name" for field in model["fields"]) else "")
    pages = []
    for field in model["fields"]:
        if field["type"] == "one2many":
            target = _model(spec, field["relation"])
            if target is not None:
                inner = [f'                                <field name="{other["name"]}"/>'
                         for other in _simple(target, spec, 5) if other["name"] != field["inverse"]]
                pages.append(
                    f'                    <page string={quoteattr(field["string"])}>\n'
                    f'                        <field name="{field["name"]}">\n'
                    f'                            <{tree}>\n' + "\n".join(inner) + f'\n                            </{tree}>\n'
                    '                        </field>\n                    </page>')
            else:
                pages.append(f'                    <page string={quoteattr(field["string"])}>\n'
                             f'                        <field name="{field["name"]}"/>\n                    </page>')
        elif field["type"] in ("text", "html", "many2many"):
            pages.append(f'                    <page string={quoteattr(field["string"])}>\n'
                         f'                        <field name="{field["name"]}"/>\n                    </page>')
    currency = ('                <field name="currency_id" invisible="1"/>\n'
                if any(f["name"] == "currency_id" for f in model["fields"]) else "")
    notebook = ("                <notebook>\n" + "\n".join(pages) + "\n                </notebook>\n") if pages else ""
    if spec["chatter"]:
        chatter = ("        <chatter/>\n" if version != "17.0" else
                   '        <div class="oe_chatter">\n'
                   '            <field name="message_follower_ids"/>\n'
                   '            <field name="activity_ids"/>\n'
                   '            <field name="message_ids"/>\n'
                   '        </div>\n')
    else:
        chatter = ""
    return f'''    <record id="view_{name}_search" model="ir.ui.view">
        <field name="name">{model["name"]}.search</field>
        <field name="model">{model["name"]}</field>
        <field name="arch" type="xml">
            <search>
{chr(10).join(search)}
                {group_tag}
{chr(10).join(groupby)}
                </group>
            </search>
        </field>
    </record>

    <record id="view_{name}_{tree}" model="ir.ui.view">
        <field name="name">{model["name"]}.{tree}</field>
        <field name="model">{model["name"]}</field>
        <field name="arch" type="xml">
            <{tree}>
{chr(10).join(cells)}
            </{tree}>
        </field>
    </record>

    <record id="view_{name}_form" model="ir.ui.view">
        <field name="name">{model["name"]}.form</field>
        <field name="model">{model["name"]}</field>
        <field name="arch" type="xml">
            <form>
{form_head}                <sheet>
{title}                    <group>
                        <group>
{chr(10).join(left)}
                        </group>
                        <group>
{chr(10).join(right)}
                        </group>
                    </group>
{notebook}{currency}                </sheet>
{chatter}            </form>
        </field>
    </record>

    <record id="action_{name}" model="ir.actions.act_window">
        <field name="name">{escape(model["description"])}</field>
        <field name="res_model">{model["name"]}</field>
        <field name="view_mode">{tree},form</field>
    </record>'''


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "menu"


def _menus_xml(spec):
    module = spec["name"]
    lines = [f'    <menuitem id="menu_{module}_root" name={quoteattr(spec["title"])} sequence="100" '
             f'groups="group_{module}_{spec["groups"][0]}"/>']
    ids = {}
    used = {f"menu_{module}_root"}
    for menu in spec["menus"]:
        xmlid = f"menu_{_slug(menu['name'])}"
        while xmlid in used:
            xmlid += "_x"
        used.add(xmlid)
        ids[menu["name"]] = xmlid
    for sequence, menu in enumerate(spec["menus"], start=1):
        parent = ids[menu["parent"]] if menu.get("parent") else f"menu_{module}_root"
        action = f' action="action_{_under(menu["model"])}"' if menu.get("model") else ""
        lines.append(f'    <menuitem id="{ids[menu["name"]]}" name={quoteattr(menu["name"])} parent="{parent}"{action} '
                     f'sequence="{sequence * 10}"/>')
    return "\n".join(lines)


def _demo_order(spec):
    """:return: the models, those that others point to first"""
    names = {model["name"] for model in spec["models"]}
    ordered, left = [], list(spec["models"])
    while left:
        ready = [model for model in left if all(
            field["relation"] not in names or field["relation"] == model["name"]
            or any(done["name"] == field["relation"] for done in ordered)
            for field in model["fields"] if field["type"] == "many2one" and field.get("required"))]
        if not ready:
            ready = left[:1]
        ordered.append(ready[0])
        left.remove(ready[0])
    return ordered


def _demo_value(field, index):
    kind = field["type"]
    if kind == "char":
        return ["Raghad", "Ebrahim"][index - 1] if field["name"] == "name" else f"{field['string']} {index}"
    return {"text": f"Demo text {index}", "html": f"<p>Demo text {index}</p>", "integer": str(index * 10),
            "float": f"{index * 10.5}", "monetary": str(index * 100), "boolean": "True",
            "date": f"2026-01-{10 + index}", "datetime": f"2026-01-{10 + index} 09:00:00"}.get(kind)


def _demo_xml(spec):
    names = {model["name"] for model in spec["models"]}
    rows, made = [], set()
    for model in _demo_order(spec):
        external = [f for f in model["fields"] if f["type"] == "many2one" and f.get("required")
                    and f["relation"] not in names and not f.get("_auto")]
        if external:
            continue
        for index in (1, 2):
            fields_ = []
            for field in model["fields"]:
                kind = field["type"]
                if field.get("compute") or field.get("_auto") or kind in ("one2many", "many2many"):
                    continue
                if kind == "many2one":
                    if field.get("required") and field["relation"] in made:
                        fields_.append(f'        <field name="{field["name"]}" ref="demo_{_under(field["relation"])}_{index}"/>')
                    continue
                if kind == "selection":
                    value = field["selection"][0]["key"]
                else:
                    value = _demo_value(field, index)
                if value is not None:
                    fields_.append(f'        <field name="{field["name"]}">{escape(value)}</field>')
            rows.append(f'    <record id="demo_{_under(model["name"])}_{index}" model="{model["name"]}">\n'
                        + "\n".join(fields_) + "\n    </record>")
        made.add(model["name"])
    return "\n".join(rows)


def _test_source(spec):
    names = {model["name"] for model in spec["models"]}
    body = []
    for model in _demo_order(spec):
        stem = _under(model["name"])
        values = []
        skip = None
        for field in model["fields"]:
            if field.get("compute") or field.get("_auto") or not field.get("required"):
                if not (field["type"] == "char" and field["name"] == "name"):
                    continue
            kind = field["type"]
            if kind == "many2one":
                if field["relation"] in names:
                    values.append(f'            "{field["name"]}": self._{_under(field["relation"])}().id,')
                elif field["relation"] == "res.partner":
                    values.append(f'            "{field["name"]}": self.env.user.partner_id.id,')
                elif field["relation"] in ("res.users",):
                    values.append(f'            "{field["name"]}": self.env.user.id,')
                elif field["relation"] == "res.company":
                    values.append(f'            "{field["name"]}": self.env.company.id,')
                elif field["relation"] == "res.currency":
                    values.append(f'            "{field["name"]}": self.env.company.currency_id.id,')
                else:
                    skip = field["relation"]
            elif kind == "selection":
                values.append(f'            "{field["name"]}": {_py(field["selection"][0]["key"])},')
            elif kind in ("one2many", "many2many"):
                continue
            else:
                value = _demo_value(field, 1)
                if kind == "char":
                    value = f"Test {stem} {field['name']}"
                if kind in ("integer", "float", "monetary"):
                    values.append(f'            "{field["name"]}": {value},')
                elif kind == "boolean":
                    values.append(f'            "{field["name"]}": True,')
                else:
                    values.append(f'            "{field["name"]}": {_py(value)},')
        maker = [f"    def _{stem}(self):"]
        if skip:
            maker.append(f"        self.skipTest({_py('needs a ' + skip + ' record')})")
        else:
            maker += [f'        return self.env["{model["name"]}"].create({{', *values, "        })"]
        body.append("\n".join(maker))
        test = [f"    def test_{stem}_can_be_created(self):", f"        record = self._{stem}()",
                "        self.assertTrue(record.id)",
                f'        self.assertIn(record, self.env["{model["name"]}"].search([]))']
        workflow = model.get("workflow") or []
        for following in workflow[1:]:
            test += [f"        record.action_set_{following['key']}()",
                     f"        self.assertEqual(record.state, {_py(following['key'])})"]
        body.append("\n".join(test))
    return "\n\n".join(body)


def _po(spec, version):
    module = spec["name"]
    entries = []

    def add(reference, text):
        entries.append(f'#. module: {module}\n#: {reference}\nmsgid {_po_quote(text)}\nmsgstr ""\n')

    add(f"model:ir.module.category,name:{module}.module_category_{module}", spec["title"])
    for group in spec["groups"]:
        add(f"model:res.groups,name:{module}.group_{module}_{group}", _label(group))
    for model in spec["models"]:
        stem = _under(model["name"])
        add(f"model:ir.model,name:{module}.model_{stem}", model["description"])
        add(f"model:ir.actions.act_window,name:{module}.action_{stem}", model["description"])
        for field in model["fields"]:
            add(f"model:ir.model.fields,field_description:{module}.field_{stem}__{field['name']}", field["string"])
            for option in field.get("selection") or []:
                add(f"model:ir.model.fields.selection,name:{module}.selection__{stem}__{field['name']}__{option['key']}",
                    option["label"])
        for state in model.get("workflow") or []:
            add(f"model:ir.model.fields.selection,name:{module}.selection__{stem}__state__{state['key']}", state["label"])
    for menu in spec["menus"]:
        add(f"model:ir.ui.menu,name:{module}.menu_{_slug(menu['name'])}", menu["name"])
    series = version
    return render(PO_HEADER, module=module, series=series) + "\n" + "\n".join(entries)


def _po_quote(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _readme(spec, version):
    return f"""# {spec['title']}

Drafted by Nudge for Odoo {version}. Read it and test it before it goes anywhere near production.

## What to do next

1. Unzip this folder into one of the addons paths of your server (`addons_path` in its configuration).
2. Restart the server, then Apps > Update Apps List (Developer mode), and install it on a test database first.
3. Read the models, the security and the views; the Arabic skeleton is in `i18n/ar.po`.
4. Run its tests: `odoo-bin -d <test db> -i {spec['name']} --test-enable --test-tags /{spec['name']} --stop-after-init`.
"""


def build_files(spec, version):
    """:return: ``{path: text}`` of the module, built from the spec for ``version``"""
    module = spec["name"]
    data = ["security/security.xml", "security/ir.model.access.csv"]
    files = {
        f"{module}/__init__.py": "from . import models\n",
        f"{module}/models/__init__.py": "".join(f"from . import {model['file']}\n" for model in spec["models"]),
        f"{module}/security/security.xml": render(VIEW_FILE, records=_groups_xml(spec, version)),
        f"{module}/security/ir.model.access.csv": _access_csv(spec),
    }
    for model in spec["models"]:
        files[f"{module}/models/{model['file']}.py"] = render(
            MODEL_FILE, klass=_klass(model["name"]), model=model["name"], description=_py(model["description"]),
            body=_model_source(model, spec, version))
        path = f"views/{model['file']}_views.xml"
        files[f"{module}/{path}"] = render(VIEW_FILE, records=_views_xml(model, spec, version))
        data.append(path)
    files[f"{module}/views/menus.xml"] = render(VIEW_FILE, records=_menus_xml(spec))
    data.append("views/menus.xml")
    demo = _demo_xml(spec)
    demo_files = []
    if demo:
        files[f"{module}/data/demo.xml"] = ('<?xml version="1.0" encoding="utf-8"?>\n<odoo noupdate="1">\n'
                                            + demo + "\n</odoo>\n")
        demo_files.append("data/demo.xml")
    files[f"{module}/__manifest__.py"] = render(
        MANIFEST, title=_py(spec["title"]), summary=_py(spec["summary"]), version=f"{version[:2]}.0.1.0.0",
        depends=repr(["base", "mail"] if spec["chatter"] else ["base"]), data=repr(data), demo=repr(demo_files))
    files[f"{module}/i18n/ar.po"] = _po(spec, version)
    files[f"{module}/tests/__init__.py"] = f"from . import test_{module}\n"
    files[f"{module}/tests/test_{module}.py"] = render(
        TEST_FILE, klass=_klass(module), body=_test_source(spec))
    files[f"{module}/NEXT_STEPS.md"] = _readme(spec, version)
    return files


# ------------------------------------------------------------ static checks

def check_files(spec, files):
    """Whether what was built can be trusted.
    :return: the list of what is wrong; empty when it passes"""
    problems = []
    module = spec["name"]
    xmlids = {}
    view_models = []
    for path, text in sorted(files.items()):
        if path.endswith(".py"):
            try:
                compile(text, path, "exec")
            except SyntaxError as error:
                problems.append(f"{path}: line {error.lineno}: {error.msg}")
            for used in sorted(set(re.findall(r"fields\.([A-Za-z0-9_]+)\(", text))):
                if not hasattr(odoo_fields, used):
                    problems.append(f"{path}: fields.{used} does not exist in Odoo")
        elif path.endswith(".xml"):
            try:
                root = etree.fromstring(text.encode())
            except etree.XMLSyntaxError as error:
                problems.append(f"{path}: {error}")
                continue
            for node in root.iter("record", "menuitem", "template"):
                xmlid = node.get("id")
                if xmlid in xmlids:
                    problems.append(f"{path}: the xml id {xmlid} is also in {xmlids[xmlid]}")
                xmlids[xmlid] = path
                if node.tag == "record" and node.get("model") == "ir.ui.view":
                    view_models.append((path, node))
    manifest_path = f"{module}/__manifest__.py"
    try:
        manifest = ast.literal_eval(files[manifest_path])
    except (ValueError, SyntaxError) as error:
        problems.append(f"{manifest_path}: not a dictionary: {error}")
        manifest = {}
    for key in manifest:
        if key not in VALID_MANIFEST_KEYS:
            problems.append(f"{manifest_path}: the key {key} is not a manifest key")
    for key in ("name", "version", "depends", "data", "license"):
        if key not in manifest:
            problems.append(f"{manifest_path}: the key {key} is missing")
    for listed in list(manifest.get("data", [])) + list(manifest.get("demo", [])):
        if f"{module}/{listed}" not in files:
            problems.append(f"{manifest_path}: {listed} is listed but not in the module")
    rows = list(csv.DictReader(io.StringIO(files[f"{module}/security/ir.model.access.csv"])))
    for model in spec["models"]:
        if not any(row["model_id:id"] == f"model_{_under(model['name'])}" for row in rows):
            problems.append(f"security/ir.model.access.csv: no access rule for {model['name']}")
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        problems.append("security/ir.model.access.csv: an access id is twice")
    for path, record in view_models:
        arch = record.find("field[@name='arch']")
        model = _model(spec, record.findtext("field[@name='model']") or "")
        if arch is None or model is None:
            continue
        problems += _view_problems(path, spec, model, arch)
    po = files.get(f"{module}/i18n/ar.po", "")
    if po.count("\nmsgid ") != po.count("\nmsgstr "):
        problems.append("i18n/ar.po: a msgid has no msgstr")
    return problems


def _view_problems(path, spec, model, arch):
    problems = []
    known = {field["name"] for field in model["fields"]} | {"state", "display_name", "id"} | set(CHATTER_FIELDS)
    for node in arch.iter("field"):
        if node is arch:
            continue
        name = node.get("name")
        parent = node.getparent()
        scope = model
        # A field inside the list of a one2many is a field of the other model
        while parent is not None and parent is not arch:
            if parent.tag in ("list", "tree", "form") and parent.getparent() is not None \
                    and parent.getparent() is not arch and parent.getparent().tag == "field":
                outer = parent.getparent().get("name")
                relation = next((f.get("relation") for f in model["fields"] if f["name"] == outer), None)
                scope = _model(spec, relation or "")
                break
            parent = parent.getparent()
        if scope is None:
            continue
        names = {field["name"] for field in scope["fields"]} | {"state", "display_name", "id"} | set(CHATTER_FIELDS)
        if scope is model:
            names = known
        if name not in names:
            problems.append(f"{path}: the view of {model['name']} shows the field {name}, which {scope['name']} does not have")
    return problems


def build_and_check(spec, version):
    """:return: ``(files, problems)``"""
    files = build_files(spec, version)
    return files, check_files(spec, files)


def make_zip(files):
    """:return: the bytes of the ZIP; the same spec gives the same bytes"""
    out = io.BytesIO()
    for path in files:
        parts = str(path).split("/")
        if not path or path.startswith(("/", "\\")) or "\\" in path or ".." in parts or "" in parts or ":" in parts[0]:
            raise ValueError(f"A file of the module has an unsafe path: {path!r}")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, text in sorted(files.items()):
            info = zipfile.ZipInfo(path, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, text.encode("utf-8"))
    return out.getvalue()


def default_version():
    major = f"{release.version_info[0]}.0"
    return major if major in VERSIONS else VERSIONS[-1]


# --------------------------------------------------------------------- tool

@base.register
class PrepareModuleScaffold(Building):
    name = SCAFFOLD_TOOL
    kind = "show"
    description = (
        "Draft a complete Odoo module for a developer, as a ZIP to download: manifest, models with fields, "
        "constraints and computed fields, security groups and access rights, list, form and search views, "
        "menus, demo data, an Arabic translation skeleton and tests, with the conventions of the Odoo "
        "version chosen (17.0, 18.0 or 19.0). Use it when the person asks for a module for something "
        "(\"build me a module for X\", in any dialect): write the spec from their words, with every model, "
        "its fields (types, relations, a one2many with its inverse many2one), groups, menus and states of a "
        "workflow when they speak of stages. Nothing is built by this call: the person reads the spec on a "
        "card and says yes or no, and can ask for changes, which gives a new card. After the yes the files "
        "are built in memory and checked; nothing is written on the server and Nudge installs nothing: the "
        "person downloads the ZIP and tests it on a test database. Trying the module here is not offered."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["spec"],
        "properties": {
            "spec": SPEC_SCHEMA,
            "version": {"type": "string", "enum": list(VERSIONS), "description": "Target Odoo version; default: this server's."},
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        version = arguments.get("version") or default_version()
        spec = read_spec(env, arguments["spec"], version)
        files, problems = build_and_check(spec, version)
        if problems:
            raise Refused("The module drafted from this spec fails its own checks: " + "; ".join(problems[:6]))
        lines = [
            {"label": _("Module"), "value": f"{spec['name']}: {spec['title']}"},
            {"label": _("Odoo version"), "value": _("%s (say 17, 18 or 19 to change it)", version)},
        ]
        for model in spec["models"]:
            shown = []
            for field in model["fields"]:
                text = f"{field['name']} ({field['type']}" + (f" → {field['relation']}" if field.get("relation") else "")
                text += ", required" if field.get("required") else ""
                text += ", computed" if field.get("compute") else ""
                shown.append(text + ")")
            extra = []
            if model.get("workflow"):
                extra.append(_("states: %s", " → ".join(state["label"] for state in model["workflow"])))
            for constraint in model.get("sql_constraints") or []:
                extra.append(constraint["definition"])
            for constraint in model.get("python_constraints") or []:
                extra.append(_("rule: %s", constraint["message"]))
            lines.append({"label": f"{model['name']}", "value": cut("; ".join(shown + extra), 600)})
        lines += [
            {"label": _("Groups"), "value": ", ".join(spec["groups"])},
            {"label": _("Menus"), "value": ", ".join(menu["name"] for menu in spec["menus"])},
            {"label": _("Files"), "value": _("%(count)s files, checked: they compile, parse and agree with each other",
                                                    count=len(files))},
            {"label": _("Try it here"), "value": _(
                "Not available: Nudge never installs the module; unzip it into an addons path and install it on a test database")},
            {"label": _("Server"), "value": _("Nothing is written on the server")},
        ]
        return base.ToolResult(
            "The person is being shown the spec of the module.",
            pending={
                "summary": _("Draft the module “%s” for Odoo %s", spec["name"], version),
                "res_model": "nudge.run",
                "res_ids": [],
                "record_names": spec["name"],
                "values": {"spec": spec, "version": version},
                "is_sensitive": False,
                "preview": {"records": [], "lines": lines},
            },
        )

    def execute(self, env, step):
        check_developing(env)
        values = step.values or {}
        spec, version = values.get("spec"), values.get("version")
        if not spec or version not in VERSIONS:
            return base.ToolResult("This module cannot be drafted.", is_error=True)
        files, problems = build_and_check(spec, version)
        if problems:
            return base.ToolResult("The checks failed: " + "; ".join(problems[:6]), is_error=True)
        step_id = getattr(step, "id", None)
        link = f"/nudge/step/{step_id}/module.zip" if step_id else ""
        steps = _(
            "What to do next: 1. unzip it into an addons path of your server; 2. restart the server and update the "
            "apps list (Developer mode); 3. install it on a test database first, and read the code, the security "
            "and i18n/ar.po.")
        told = (f"The module {spec['name']} for Odoo {version} was drafted in memory: {len(files)} files, all checks "
                f"passed. Download the ZIP: {link or 'from the step'}. {steps} Nothing was written on the server "
                "and the module was not installed.")
        return base.ToolResult(
            told,
            card_lines=[
                {"label": _("Download"), "value": link or _("the ZIP of the step")},
                {"label": _("Next"), "value": steps},
            ],
        )
