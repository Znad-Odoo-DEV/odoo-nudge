"""PDF reports, in Developer mode: a report on a model, printed from its
records, made of the fields the person names or of a template they asked for.

The template is a QWeb view of its own and the report an action in the Print
menu, the way Studio makes them. Before the card, both are made in a
savepoint that is rolled back and the report is rendered for one record:
what Odoo would refuse is told, and the person is shown the text it prints.
"""
from odoo import _
import re

from lxml import etree
from markupsafe import escape

from odoo.exceptions import UserError, ValidationError
from odoo.tools import html2plaintext

from . import base
from .building import CUSTOM_MODULE, DryRun, RELOAD, register
from .developer import Building, check_developing, ir_model
from .records import (
    MODEL_PATTERN, PATH_PATTERN, Refused, cut, field_label, get_model, model_label, refusing,
    target_field,
)

MAX_FIELDS = 30
MAX_COLUMNS = 10
MAX_TEMPLATE_CHARS = 8000
MAX_PREVIEW_CHARS = 900
FORBIDDEN_TAGS = ("script", "iframe", "object", "embed")
# What an expression of a template made here may not reach: the environment,
# other rights, writing, the secrets of the database
FORBIDDEN_EXPRESSION = re.compile(
    r"\benv\b|\bsudo\b|with_user|with_context|with_company|__|\brequest\b|\beval\b|\bexec\b"
    r"|\.(write|unlink|create|copy|search|search_read|read_group|browse|execute|_[a-z])\b"
    r"|ir\.config_parameter|password|secret|token|api_key",
    re.IGNORECASE,
)
FIELD_SCHEMA = {"type": "string", "pattern": PATH_PATTERN}


def slug_of(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "report"


def free_key(env, name):
    """:return: a key for the template that no view holds yet"""
    stem = f"{CUSTOM_MODULE}.report_{slug_of(name)}"
    Views = env["ir.ui.view"].sudo().with_context(active_test=False)
    key, number = stem, 1
    while Views.search_count([("key", "=", key)]) or env["ir.actions.report"].sudo().search_count([("report_name", "=", key)]):
        number += 1
        key = f"{stem}_{number}"
    return key


def field_rows(model, paths):
    """:return: ``[(label, path)]`` for the fields shown, checked for this person"""
    rows = []
    for path in paths:
        field = target_field(model, path)
        if field.type in ("binary", "one2many", "many2many", "json", "properties"):
            raise Refused(f"{path} is a {field.type}: it is not printed as a value. Lines print a one2many.")
        label = field_label(field, model.env)
        if "." in path:
            head = model._fields[path.split(".")[0]]
            label = f"{field_label(head, model.env)} › {label}"
        rows.append((label, path))
    return rows


def build_template(env, key, model, title, rows, lines):
    """:return: the arch of a clean report: a title, the fields as rows, the lines as a table"""
    parts = [
        f'<t t-name="{key}">',
        '  <t t-call="web.html_container">',
        '    <t t-foreach="docs" t-as="o">',
        '      <t t-call="web.external_layout">',
        '        <div class="page">',
        f'          <h2><span>{escape(title)}</span> <span t-out="o.display_name"/></h2>',
        '          <table class="table table-sm o_main_table mt-3">',
    ]
    for label, path in rows:
        parts.append(f'            <tr><th class="w-25">{escape(label)}</th><td><span t-field="o.{path}"/></td></tr>')
    parts.append("          </table>")
    if lines:
        parts.append(f'          <h3 class="mt-4">{escape(lines["label"])}</h3>')
        parts.append('          <table class="table table-sm">')
        parts.append("            <thead><tr>" + "".join(f"<th>{escape(label)}</th>" for label, _path in lines["columns"]) + "</tr></thead>")
        parts.append("            <tbody>")
        parts.append(f'              <t t-foreach="o.{lines["field"]}" t-as="line">')
        parts.append("                <tr>" + "".join(f'<td><span t-field="line.{path}"/></td>' for _label, path in lines["columns"]) + "</tr>")
        parts.append("              </t>")
        parts.append("            </tbody>")
        parts.append("          </table>")
    parts += ["        </div>", "      </t>", "    </t>", "  </t>", "</t>"]
    return "\n".join(parts)


def check_template(key, arch):
    """:return: the arch, named after the key
    :raise Refused: when it is not a template"""
    try:
        root = etree.fromstring(arch.encode("utf-8"))
    except etree.XMLSyntaxError as error:
        raise Refused(f"The template is not well-formed XML: {cut(str(error), 200)}") from None
    for element in root.iter():
        if isinstance(element.tag, str) and element.tag.lower() in FORBIDDEN_TAGS:
            raise Refused(f"<{element.tag}> has no place in a report.")
        for name, value in element.attrib.items():
            if name.startswith("t-") and name not in ("t-name", "t-call", "t-as") and FORBIDDEN_EXPRESSION.search(value):
                raise Refused(
                    f"The expression {value!r} reaches beyond the records printed: a report made here "
                    "reads the fields of o and of its lines, nothing else.")
    if root.tag != "t":
        wrapper = etree.Element("t")
        wrapper.append(root)
        root = wrapper
    root.set("t-name", key)
    if root.find(".//*[@t-foreach='docs']") is None and "docs" not in arch:
        raise Refused('A report template goes through its records: <t t-foreach="docs" t-as="o">.')
    return etree.tostring(root, encoding="unicode")


def preview_report(env, model, values, arch):
    """Make the template and the report in a savepoint that is rolled back,
    and render the report for one record.
    :return: the text printed, or an empty string when there is no record"""
    sample = model.search([], order="id desc", limit=1)
    text = ""
    try:
        with env.cr.savepoint():
            try:
                env["ir.ui.view"].create({
                    "name": values["name"], "type": "qweb", "key": values["report_name"],
                    "mode": "primary", "arch_db": arch})
                report = env["ir.actions.report"].create(values)
                if sample:
                    html, _kind = env["ir.actions.report"]._render_qweb_html(report, sample.ids)
                    html = html.decode("utf-8") if isinstance(html, bytes) else html
                    # The logo and the pictures are not text to show
                    html = re.sub(r"<img\b[^>]*>", " ", html, flags=re.IGNORECASE)
                    text = " ".join(html2plaintext(html).split())
            except (ValidationError, UserError, ValueError) as error:
                raise Refused(f"Odoo refuses this report: {cut(' '.join(str(error).split()), 500)}") from None
            except Exception as error:  # noqa: BLE001
                raise Refused(f"The report cannot be rendered: {cut(' '.join(str(error).split()), 500)}") from None
            raise DryRun()
    except DryRun:
        pass
    finally:
        env.invalidate_all()
        env.registry.clear_cache("templates")
    return text


@base.register
class PrepareReport(Building):
    name = "prepare_report"
    kind = "create"
    description = (
        "Make a PDF report on a model, printed from the Print menu of its records: give the "
        "fields it shows (technical names, paths such as partner_id.name for a related value) "
        "and, for the lines of the record, the one2many field and the columns; Nudge lays "
        "them out with the company's header and footer, the way Studio does. Or give a QWeb "
        "template of your own. Nothing is made by this call: the report is made and rendered "
        "for one record in a trial that is discarded, what Odoo refuses is told, and the "
        "person is shown the template and the text it prints, then says yes or no; you read "
        "their answer as the result."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["model", "name"],
        "properties": {
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "name": {"type": "string", "minLength": 2, "maxLength": 80, "description": "As it reads in the Print menu."},
            "title": {"type": "string", "maxLength": 80, "description": "Printed at the top; the name otherwise."},
            "fields": {"type": "array", "maxItems": MAX_FIELDS, "items": FIELD_SCHEMA},
            "lines": {
                "type": "object",
                "additionalProperties": False,
                "required": ["field", "columns"],
                "properties": {
                    "field": {"type": "string", "pattern": r"[a-z_][a-z0-9_]*", "description": "The one2many field of the lines."},
                    "columns": {"type": "array", "minItems": 1, "maxItems": MAX_COLUMNS, "items": FIELD_SCHEMA},
                },
            },
            "template": {
                "type": "string",
                "maxLength": MAX_TEMPLATE_CHARS,
                "description": "Instead of fields: the QWeb template, going through docs as o.",
            },
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        model = get_model(env, arguments["model"])
        name = arguments["name"].strip()
        title = (arguments.get("title") or "").strip() or name
        key = free_key(env, name)
        lines_shown = []
        if arguments.get("template"):
            arch = check_template(key, arguments["template"].strip())
            lines_shown.append({"label": _("Made of"), "value": _("a template of its own")})
        else:
            paths = arguments.get("fields") or []
            if not paths:
                raise Refused("Give the fields the report shows, or a template.")
            rows = field_rows(model, paths)
            lines = None
            if arguments.get("lines"):
                field = model._fields.get(arguments["lines"]["field"])
                if field is None or field.type != "one2many":
                    raise Refused(f"{arguments['lines']['field']!r} is not a one2many field of {model._name}.")
                comodel = get_model(env, field.comodel_name)
                lines = {
                    "field": field.name,
                    "label": field_label(field, env),
                    "columns": field_rows(comodel, arguments["lines"]["columns"]),
                }
            arch = build_template(env, key, model, title, rows, lines)
            lines_shown.append({"label": _("Shows"), "value": ", ".join(label for label, _path in rows)})
            if lines:
                lines_shown.append({"label": _("Lines"), "value": f"{lines['label']}: " + ", ".join(label for label, _path in lines["columns"])})
        values = {
            "name": name,
            "model": model._name,
            "report_type": "qweb-pdf",
            "report_name": key,
            "print_report_name": f"'{name.replace(chr(39), '')} - %s' % (object.display_name)",
            "binding_model_id": ir_model(env, model).id,
            "binding_type": "report",
        }
        printed = preview_report(env, model, values, arch)
        card = [
            {"label": _("Report"), "value": name},
            {"label": _("Model"), "value": model_label(model)},
            *lines_shown,
            {"label": _("Where"), "value": _("the Print menu of %s", model_label(model))},
            {"label": _("Template"), "value": arch, "code": True},
            {"label": _("Prints, for one record"), "value": cut(printed, MAX_PREVIEW_CHARS) or _("No record to print yet"), "code": True},
        ]
        return base.ToolResult(
            "The person is being shown the report, its template and what it prints.",
            pending={
                "summary": _("Create: report “%s”", name),
                "res_model": "ir.actions.report",
                "res_ids": [],
                "record_names": name,
                "values": {"report": values, "arch": arch},
                "is_sensitive": bool(arguments.get("template")),
                "preview": {"records": [], "lines": card},
            },
        )

    def execute(self, env, step):
        check_developing(env)
        values = dict((step.values or {}).get("report") or {})
        arch = (step.values or {}).get("arch")
        if not values.get("report_name") or not arch:
            return base.ToolResult("This report cannot be made.", is_error=True)
        if env["ir.ui.view"].sudo().with_context(active_test=False).search_count([("key", "=", values["report_name"])]):
            values["report_name"] = free_key(env, values["name"])
            arch = check_template(values["report_name"], arch)
        view = env["ir.ui.view"].create({
            "name": values["name"], "type": "qweb", "key": values["report_name"], "mode": "primary", "arch_db": arch})
        register(env, view, f"template_{values['report_name'].split('.')[-1]}")
        report = env["ir.actions.report"].create(values)
        register(env, report, f"report_{values['report_name'].split('.')[-1]}")
        env.registry.clear_cache("templates")
        return base.ToolResult(
            f"Made: report #{report.id} {report.name!r} on {report.model}, in the Print menu of its records; "
            f"template view #{view.id} ({view.key}). It is in Settings ‣ Technical ‣ Reports.",
            event={"type": "changed", "model": "ir.actions.report", "ids": report.ids, "action": RELOAD},
            records=("ir.actions.report", report.ids, [report.name]),
        )
