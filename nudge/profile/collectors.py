"""Reading of what makes this database what it is: which apps and custom
modules it has, what they add to the standard models, how the apps are
configured, who is allowed what.

Only metadata and configuration are read, never business records. Each
collector returns *slices*: small pieces that can be looked up one by one,
so that an answer carries the piece it needs and not the whole database::

    {"kind": ..., "key": ..., "title": ..., "summary": ..., "data": {...},
     "model_name": ...,   # reading the slice needs read access to this model
     "restricted": bool}  # for managers of Nudge only: its data carries in
                          # "public" the numbers everybody may read, no names

Collectors run as superuser: they describe the database, not what one user
sees of it. Who may read a slice is decided when it is looked up.
"""
import logging
import os

from lxml import etree

from odoo import release
from odoo.modules.module import get_module_path

from . import activity

_logger = logging.getLogger(__name__)

OWN_MODULE = "nudge"
MAX_FIELDS = 60
MAX_TEXT = 300
# How many records of one kind are read for their names: the count is counted
# apart, so that a database of thousands is not scanned and is still counted
MAX_LISTED = 200
MAGIC_FIELDS = frozenset({
    "id", "display_name", "create_uid", "create_date", "write_uid", "write_date",
})

# Settings kept on the company, by the app that owns them. Read only when the
# app is installed and the field exists in this version.
COMPANY_SETTINGS = {
    "sale": ["quotation_validity_days", "portal_confirmation_sign", "portal_confirmation_pay",
             "prepayment_percent"],
    "purchase": ["po_lock", "po_double_validation", "po_double_validation_amount"],
    "account": ["chart_template", "fiscalyear_last_day", "fiscalyear_last_month",
                "tax_calculation_rounding_method", "anglo_saxon_accounting", "tax_exigibility",
                "account_price_include"],
    "stock": ["annual_inventory_day", "annual_inventory_month", "horizon_days"],
    "hr_timesheet": ["timesheet_encode_method"],
}


def _text(value, size=MAX_TEXT):
    value = " ".join(str(value or "").split())
    return value if len(value) <= size else value[:size - 1] + "…"


def _slice(kind, key, title, summary, data, model_name=None, restricted=False):
    return {
        "kind": kind,
        "key": key,
        "title": _text(title, 120),
        "summary": "\n".join(line for line in summary if line),
        "data": data,
        "model_name": model_name,
        "restricted": restricted,
    }


def _core_directories():
    directories = set()
    for name in ("base", "web"):
        path = get_module_path(name, display_warning=False)
        if path:
            directories.add(os.path.normcase(os.path.dirname(os.path.realpath(path))))
    return directories


def is_enterprise(env):
    """:return: whether this database runs Enterprise: its ``web_enterprise`` module is installed. The
    licence of a module says nothing (Community ships modules under OEEL-1, such as project_hr_skills)."""
    return bool(env["ir.module.module"].sudo().search_count(
        [("name", "=", "web_enterprise"), ("state", "=", "installed")]))


def custom_module_names(env):
    """:return: installed modules that are not part of Odoo itself"""
    core = _core_directories()
    names = set()
    modules = env["ir.module.module"].sudo().search([("state", "=", "installed")])
    for module in modules:
        path = get_module_path(module.name, display_warning=False)
        directory = path and os.path.normcase(os.path.dirname(os.path.realpath(path)))
        if directory in core or module.license == "OEEL-1":
            continue
        names.add(module.name)
    return names


def _selection_label(field, value, env):
    if field.type != "selection" or value in (False, None):
        return value
    try:
        return dict(field._description_selection(env)).get(value, value)
    except Exception:  # noqa: BLE001
        return value


def _describe_field(name, field, env):
    entry = {"name": name, "label": _text(field.string, 80), "type": field.type}
    if field.help:
        entry["help"] = _text(field.help)
    if field.required:
        entry["required"] = True
    if field.comodel_name:
        entry["relation"] = field.comodel_name
    if field.type == "selection":
        try:
            entry["choices"] = [_text(label, 60) for _key, label in field._description_selection(env)][:20]
        except Exception:  # noqa: BLE001
            pass
    if field.compute and not field.store:
        entry["computed"] = True
    return entry


# ----------------------------------------------------------------------
# Modules
# ----------------------------------------------------------------------

def collect_modules(env, custom):
    Module = env["ir.module.module"].sudo()
    modules = Module.search([("state", "=", "installed")], order="name")
    apps = modules.filtered("application")
    enterprise = is_enterprise(env)
    companies = env["res.company"].sudo().search([])
    languages = env["res.lang"].sudo().search([("active", "=", True)])

    business = sorted(custom - {OWN_MODULE})
    custom_modules = modules.filtered(lambda module: module.name in business)
    data = {
        "odoo_version": release.major_version,
        "edition": "Enterprise" if enterprise else "Community",
        "installed_modules": len(modules),
        "apps": [{"name": app.name, "title": app.shortdesc} for app in apps if app.name != OWN_MODULE],
        "custom_modules": [
            {"name": module.name, "title": module.shortdesc, "author": module.author or ""}
            for module in custom_modules
        ],
        "companies": companies.mapped("name"),
        "languages": languages.mapped("name"),
    }
    summary = [
        f"Odoo {data['odoo_version']} {data['edition']}, {len(modules)} modules installed.",
        "Apps: " + ", ".join(app["title"] for app in data["apps"]) + ".",
        (
            "Custom modules (not part of standard Odoo): "
            + ", ".join(f"{module.shortdesc} ({module.name})" for module in custom_modules) + "."
            if custom_modules else "No custom module: this is standard Odoo."
        ),
        # The companies stay in the data: a person is told those they work in
        f"Languages: {', '.join(data['languages'])}.",
    ]
    slices = [_slice("overview", "overview", "This database", summary, data)]

    for module in custom_modules:
        depends = module.dependencies_id.mapped("name")
        introduced = sorted(
            name for name, model in env.registry.items()
            if getattr(model, "_original_module", None) == module.name and not model._abstract
        )
        lines = [
            f"{module.shortdesc} ({module.name}) is a custom module by {module.author or 'unknown'}, "
            f"version {module.latest_version or '?'}.",
            _text(module.summary) if module.summary else "",
            f"It builds on: {', '.join(depends)}." if depends else "",
            (
                "It adds these kinds of records: "
                + ", ".join(f"{env[name]._description} ({name})" for name in introduced[:30]) + "."
                if introduced else ""
            ),
        ]
        slices.append(_slice("module", f"module:{module.name}", module.shortdesc, lines, {
            "name": module.name,
            "author": module.author or "",
            "version": module.latest_version or "",
            "application": module.application,
            "depends": depends,
            "models": introduced,
        }))
    return slices


# ----------------------------------------------------------------------
# Models and fields
# ----------------------------------------------------------------------

def _field_module(field):
    modules = getattr(field, "_modules", ()) or ()
    return modules[0] if modules else getattr(field, "_module", None)


def _view_changes(env, custom):
    """:return: {model: [what custom views add to it]}"""
    View = env["ir.ui.view"].sudo()
    views = View.search([("mode", "=", "extension"), ("type", "in", ("form", "list", "kanban", "search"))])
    origins = {
        data.res_id: data.module
        for data in env["ir.model.data"].sudo().search(
            [("model", "=", "ir.ui.view"), ("res_id", "in", views.ids)])
    }
    changes = {}
    for view in views:
        module = origins.get(view.id)
        if module is not None and module not in custom:
            continue
        if module == OWN_MODULE or not view.model:
            continue
        try:
            arch = etree.fromstring((view.arch_db or "").encode())
        except etree.XMLSyntaxError:
            continue
        fields = sorted({node.get("name") for node in arch.iter("field") if node.get("name")})
        buttons = sorted({node.get("name") for node in arch.iter("button") if node.get("name")})
        if not fields and not buttons:
            continue
        changes.setdefault(view.model, []).append({
            "view": view.name,
            "type": view.type,
            "by": module or "edited in the database",
            "fields": fields[:30],
            "buttons": buttons[:20],
        })
    return changes


def collect_models(env, custom):
    view_changes = _view_changes(env, custom)
    business = custom - {OWN_MODULE}
    slices = []
    for name, model_class in sorted(env.registry.items()):
        if model_class._abstract or model_class._transient or name not in env:
            continue
        model = env[name]
        origin = getattr(model_class, "_original_module", None)
        is_custom = bool(getattr(model_class, "_custom", False)) or origin in business
        if origin == OWN_MODULE:
            continue
        added = []
        for field_name, field in model._fields.items():
            if field_name in MAGIC_FIELDS:
                continue
            module = _field_module(field)
            if field.manual or module in business:
                entry = _describe_field(field_name, field, env)
                entry["added_by"] = "created in the database" if field.manual else module
                added.append(entry)
        changes = view_changes.get(name, [])
        if not (is_custom or added or changes):
            continue

        label = model._description or name
        if is_custom:
            lines = [f"{label} ({name}) is a custom kind of record, added by {origin or 'the database'}."]
            if added:
                shown = ", ".join(f"{entry['label']} ({entry['name']})" for entry in added[:25])
                lines.append(f"Its fields: {shown}.")
        else:
            lines = [f"{label} ({name}) is a standard Odoo model that this database extends."]
            by_module = {}
            for entry in added:
                by_module.setdefault(entry["added_by"], []).append(entry)
            for module, entries in sorted(by_module.items()):
                shown = ", ".join(f"{entry['label']} ({entry['name']})" for entry in entries[:25])
                lines.append(f"{module} adds the fields: {shown}.")
        for change in changes[:8]:
            parts = []
            if change["fields"]:
                parts.append("fields " + ", ".join(change["fields"][:12]))
            if change["buttons"]:
                parts.append("buttons " + ", ".join(change["buttons"][:8]))
            lines.append(f"{change['by']} changes its {change['type']} view: {'; '.join(parts)}.")

        slices.append(_slice("model", f"model:{name}", label, lines, {
            "model": name,
            "label": label,
            "module": origin or "",
            "custom": is_custom,
            "fields": added[:MAX_FIELDS],
            "view_changes": changes[:12],
        }, model_name=name))
    return slices


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

def _enabled_features(env):
    """Options switched on for every internal user: most settings of the apps
    are groups added to the group of internal users."""
    base_user = env.ref("base.group_user", raise_if_not_found=False)
    if not base_user:
        return {}
    groups = base_user.sudo().all_implied_ids - base_user
    origins = {
        data.res_id: data.module
        for data in env["ir.model.data"].sudo().search(
            [("model", "=", "res.groups"), ("res_id", "in", groups.ids)])
    }
    features = {}
    for group in groups:
        module = origins.get(group.id)
        if module and module not in ("base", OWN_MODULE):
            features.setdefault(module, []).append(_text(group.name, 80))
    return features


def _company_settings(env, module):
    Company = env["res.company"]
    names = [name for name in COMPANY_SETTINGS.get(module, ()) if name in Company._fields]
    settings = []
    for company in Company.sudo().search([], limit=5):
        for name in names:
            field = Company._fields[name]
            value = company[name]
            if value in (False, None, ""):
                continue
            settings.append({
                "company": company.name,
                "setting": _text(field.string, 80),
                "name": name,
                "value": _text(_selection_label(field, value, env), 80),
            })
    return settings


def _warehouses(env):
    if "stock.warehouse" not in env:
        return []
    Warehouse = env["stock.warehouse"]
    warehouses = []
    for warehouse in Warehouse.sudo().search([], limit=20):
        entry = {"name": warehouse.name, "code": warehouse.code, "company": warehouse.company_id.name}
        for name in ("reception_steps", "delivery_steps", "manufacture_steps"):
            if name in Warehouse._fields:
                entry[name] = _text(_selection_label(Warehouse._fields[name], warehouse[name], env), 80)
        warehouses.append(entry)
    return warehouses


def collect_configuration(env, custom):
    installed = set(env["ir.module.module"].sudo().search([("state", "=", "installed")]).mapped("name"))
    titles = {
        module.name: module.shortdesc
        for module in env["ir.module.module"].sudo().search([("name", "in", list(installed))])
    }
    features = _enabled_features(env)
    slices = []
    for module in sorted(installed & (set(features) | set(COMPANY_SETTINGS) | {"stock"})):
        title = titles.get(module, module)
        enabled = sorted(features.get(module, []))
        settings = _company_settings(env, module)
        warehouses = _warehouses(env) if module == "stock" else []
        if not (enabled or settings or warehouses):
            continue
        lines = [f"How {title} is set up in this database."]
        if enabled:
            lines.append("Options switched on: " + "; ".join(enabled) + ".")
        for setting in settings:
            lines.append(f"{setting['setting']}: {setting['value']} ({setting['company']}).")
        for warehouse in warehouses:
            steps = [
                f"{label} {warehouse[name]}"
                for name, label in (("reception_steps", "receipts:"), ("delivery_steps", "deliveries:"),
                                    ("manufacture_steps", "manufacturing:"))
                if warehouse.get(name)
            ]
            lines.append(f"Warehouse {warehouse['name']} ({warehouse['code']}): " + "; ".join(steps) + ".")
        slices.append(_slice("configuration", f"configuration:{module}", f"{title}: configuration", lines, {
            "module": module,
            "options": enabled,
            "settings": settings,
            "warehouses": warehouses,
        }))
    return slices


# ----------------------------------------------------------------------
# Automation and security: for managers
# ----------------------------------------------------------------------

def collect_automation(env, custom):
    """The numbers are for everybody, in ``public``; the names for the managers."""
    data = {}
    Cron = env["ir.cron"].sudo()
    cron_count = Cron.search_count([("active", "=", True)])
    crons = Cron.search([("active", "=", True)], order="cron_name", limit=MAX_LISTED)
    data["scheduled_actions"] = [
        {"name": _text(cron.cron_name, 100), "every": f"{cron.interval_number} {cron.interval_type}"}
        for cron in crons
    ]
    public = [f"{cron_count} scheduled actions are active."]
    lines = list(public)
    if "base.automation" in env:
        Rule = env["base.automation"].sudo()
        rule_count = Rule.search_count([("active", "=", True)])
        rules = Rule.search([("active", "=", True)], limit=MAX_LISTED)
        data["automation_rules"] = [
            {"name": _text(rule.name, 100), "model": rule.model_name, "trigger": rule.trigger}
            for rule in rules
        ]
        public.append(f"{rule_count} automation rules are active.")
        named = ", ".join(f"{rule.name} on {rule.model_name}" for rule in rules[:15])
        lines.append(f"{rule_count} automation rules are active" + (f": {named}." if named else "."))
    else:
        public.append("Automation rules are not installed.")
        lines.append(public[-1])
    if "mail.template" in env:
        Template = env["mail.template"].sudo()
        template_count = Template.search_count([])
        templates = Template.search([], limit=MAX_LISTED)
        by_model = {}
        for template in templates:
            by_model.setdefault(template.model or "", []).append(_text(template.name, 80))
        data["email_templates"] = {model: names[:20] for model, names in sorted(by_model.items()) if model}
        public.append(f"{template_count} email templates on {len(data['email_templates'])} kinds of records.")
        lines.append(public[-1])
    data["public"] = " ".join(public)
    return [_slice("automation", "automation", "Automation", lines, data, restricted=True)]


def collect_security(env, custom):
    """The numbers are for everybody, in ``public``; the names for the managers."""
    business = custom - {OWN_MODULE}
    Data = env["ir.model.data"].sudo()
    group_domain = [("model", "=", "res.groups"), ("module", "in", list(business))]
    group_count = Data.search_count(group_domain)
    origins = {data.res_id: data.module for data in Data.search(group_domain, limit=MAX_LISTED)}
    custom_groups = [
        {"name": _text(group.full_name, 100), "module": origins[group.id],
         "users": len(group.all_user_ids), "description": _text(group.comment)}
        for group in env["res.groups"].sudo().browse(list(origins)).exists()
    ]
    internal = env["res.users"].sudo().search_count([("share", "=", False)])
    public = [f"{internal} internal users."]
    lines = list(public)
    if custom_groups:
        public.append(f"{group_count} group(s) added by custom modules.")
        lines.append("Groups added by custom modules: " + "; ".join(
            f"{group['name']} ({group['users']} users)" for group in custom_groups[:20]) + ".")
    custom_models = [
        name for name, model in env.registry.items()
        if getattr(model, "_original_module", None) in business
    ]
    Rule = env["ir.rule"].sudo()
    rule_domain = [("active", "=", True), ("model_id.model", "in", custom_models)]
    rule_count = Rule.search_count(rule_domain) if custom_models else 0
    custom_rules = [
        {"name": _text(rule.name, 100), "model": rule.model_id.model,
         "applies_to": ", ".join(rule.groups.mapped("full_name")) or "everybody",
         "condition": _text(rule.domain_force, 200)}
        for rule in (Rule.search(rule_domain, limit=MAX_LISTED) if custom_models else Rule)
    ]
    if rule_count:
        public.append(f"{rule_count} record rules restrict what people see of the custom records.")
        lines.append(public[-1])
    return [_slice("security", "security", "Users and access", lines, {
        "internal_users": internal,
        "custom_groups": custom_groups,
        "custom_record_rules": custom_rules[:40],
        "public": " ".join(public),
    }, restricted=True)]


COLLECTORS = (
    collect_modules,
    collect_models,
    collect_configuration,
    collect_automation,
    collect_security,
    activity.collect_activity,
)


def collect(env):
    """:return: every slice describing the database"""
    custom = custom_module_names(env)
    slices = []
    for collector in COLLECTORS:
        try:
            with env.cr.savepoint():
                slices.extend(collector(env, custom))
        except Exception:  # noqa: BLE001
            # One part of the database that cannot be read does not hide the rest
            _logger.exception("Nudge: %s failed", collector.__name__)
    return slices
