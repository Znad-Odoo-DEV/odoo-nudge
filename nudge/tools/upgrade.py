"""The check before an upgrade: what in this database will break or needs a look
when it goes to the next series. Deterministic, no AI, read-only.

It uses the framework of the health check (``tools.health``): the same
``Check`` classes, findings, time budget and report, run as an administrator
and only over the records a person made (Studio, custom modules); what Odoo's
own modules own is theirs to migrate. The notable renames come from the bundled
``data/upgrade_rules.json``; the rest are patterns that are true of a series.

The key of a finding is ``kind|detail...``, the sentence is written from the key
in the language of whoever reads the report.
"""
import json
import logging
import os
import re

from odoo.modules.module import get_module_path

from . import health
from .health import CRITICAL, INFO, WARNING, Check, finding, scan

_logger = logging.getLogger(__name__)

# Target series -> the series it leaves
TARGETS = {"18.0": "17.0", "19.0": "18.0", "20.0": "19.0"}
RULES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "upgrade_rules.json")
# Owners of the standard modules: anything else is custom
ODOO_AUTHOR = re.compile(r"^\s*odoo(\s+s\.?\s?a\.?)?\s*$", re.IGNORECASE)
# The module that holds what Studio creates
STUDIO_MODULE = "studio_customization"
# Custom records whose screens are opened to find the broken ones
SCREENS = 300

_rules_cache = {}


def rules_for(target):
    """:return: the notable changes known for the target series (a list)"""
    if not _rules_cache:
        with open(RULES_FILE, encoding="utf-8") as handle:
            _rules_cache.update(json.load(handle))
    return list(_rules_cache.get(target) or [])


def known_target(target):
    return target in TARGETS


def default_target(series):
    """:return: the target a person most likely wants, for the series they run"""
    for target, source in TARGETS.items():
        if source == series:
            return target
    return "19.0"


def standard_modules(env):
    """:return: names of the installed modules of Odoo itself"""
    modules = env["ir.module.module"].search([("state", "=", "installed")])
    return [m.name for m in modules if ODOO_AUTHOR.match(m.author or "") and m.name != STUDIO_MODULE]


def custom_modules(env):
    """:return: the installed modules that are not Odoo's (not Studio's records)"""
    modules = env["ir.module.module"].search([("state", "=", "installed")], order="name")
    return modules.filtered(lambda m: not ODOO_AUTHOR.match(m.author or "") and m.name != STUDIO_MODULE)


def has_migration(module_name, target):
    """:return: whether the module ships a migration folder for the target series"""
    path = get_module_path(module_name)
    folder = os.path.join(path, "migrations") if path else ""
    if not folder or not os.path.isdir(folder):
        return False
    return any(name.startswith(f"{target}.") or name == target for name in os.listdir(folder))


# ----------------------------------------------------------------------
# Where a person's records keep text that can name a field
# ----------------------------------------------------------------------
class Place:
    """A kind of record that holds text (arch, code, domain) and the SQL to
    reach it. ``custom`` is the condition that the record is not the standard
    modules' (needs the list of their names as its parameter)"""

    def __init__(self, key, model, model_col, texts, custom=None, extra="TRUE"):
        self.key, self.model, self.model_col, self.texts = key, model, model_col, texts
        self.extra = extra
        self.custom = custom or (
            f"NOT EXISTS (SELECT 1 FROM ir_model_data d WHERE d.model = '{model}' "
            "AND d.res_id = t.id AND d.module = ANY(%s))")

    def available(self, env):
        return self.model in env.registry

    def text_sql(self, env):
        out = []
        model = env[self.model]
        for name in self.texts:
            if name.startswith("("):  # a sub-query
                out.append(name)
            elif name in model._fields and model._fields[name].store:
                out.append(health.text_of(env, model, name))
        return out


_MODEL_OF_ACTION = "(SELECT m.model FROM ir_model m WHERE m.id = t.model_id)"
_CRON_ACTION = "(SELECT s.code FROM ir_act_server s WHERE s.id = t.ir_actions_server_id)"
_CRON_MODEL = ("(SELECT m.model FROM ir_model m JOIN ir_act_server s ON s.model_id = m.id "
               "WHERE s.id = t.ir_actions_server_id)")

PLACES = {
    "views": Place("views", "ir.ui.view", "t.model", ["arch_db"]),
    "windows": Place("windows", "ir.actions.act_window", "t.res_model", ["domain", "context"]),
    "server": Place("server", "ir.actions.server", _MODEL_OF_ACTION, ["code"],
                    extra="NOT EXISTS (SELECT 1 FROM ir_cron c WHERE c.ir_actions_server_id = t.id)"),
    "crons": Place("crons", "ir.cron", _CRON_MODEL, [_CRON_ACTION]),
    "rules": Place("rules", "ir.rule", _MODEL_OF_ACTION, ["domain_force"]),
    "templates": Place("templates", "mail.template", "t.model", ["body_html", "subject"]),
    "fields": Place("fields", "ir.model.fields", "t.model", ["compute", "related", "depends", "domain"],
                    custom="t.state = 'manual'"),
}


def place_names(env):
    """:return: ``{place key: what a plural of those records is called}``, in the language of ``env``"""
    return {
        "views": env._("custom views"),
        "windows": env._("window actions"),
        "server": env._("server actions"),
        "crons": env._("scheduled actions"),
        "rules": env._("record rules"),
        "templates": env._("email templates"),
        "fields": env._("custom fields"),
    }


def scan_place(env, place, condition, params, cap=health.COUNT_CAP):
    """:return: ``(count, capped, ids)`` of the custom records of ``place`` for
    which ``condition`` holds"""
    standard = [] if place.key == "fields" else [standard_modules(env)]
    where = f"({place.custom}) AND ({place.extra}) AND ({condition})"
    return scan(env, env[place.model], where, [*standard, *params], cap=cap)


def any_text(env, place, regex):
    """:return: ``(sql, params)`` true when any text of the place matches ``regex``"""
    sqls = place.text_sql(env)
    if not sqls:
        return "FALSE", []
    return "(" + " OR ".join(f"{sql} ~ %s" for sql in sqls) + ")", [regex] * len(sqls)


def rule_condition(env, place, rule):
    """:return: ``(sql, params)`` true for a record of ``place`` the rule is about. Model-aware:
    a field name alone says nothing, the record must be on the model of the rule
    or name that model next to a path ending in the field."""
    models = rule["models"]
    fields = [name for name in rule["fields"] if name != "*"]
    quoted = "(" + "|".join(re.escape(m) for m in models) + ")"
    quoted = f"['\"]{quoted}['\"]"
    if not fields:  # the whole model
        text, text_params = any_text(env, place, quoted)
        return f"(({place.model_col}) = ANY(%s) OR {text})", [models, *text_params]
    names = "|".join(re.escape(name) for name in fields)
    plain, plain_params = any_text(env, place, rf"\y({names})\y")
    dotted, dotted_params = any_text(env, place, rf"\.({names})\y")
    if place.key == "fields":  # a path from another model: only its last step can be told
        return f"({plain} AND (({place.model_col}) = ANY(%s))) OR ({dotted})", [*plain_params, models, *dotted_params]
    mention, mention_params = any_text(env, place, quoted)
    if place.key in ("views", "windows", "rules", "templates"):
        return f"(({place.model_col}) = ANY(%s) AND {plain})", [models, *plain_params]
    return (f"((({place.model_col}) = ANY(%s) AND {plain}) OR ({dotted} AND {mention}))",
            [models, *plain_params, *dotted_params, *mention_params])


# ----------------------------------------------------------------------
# The checks
# ----------------------------------------------------------------------
class UpgradeCheck(Check):
    """A check of the upgrade: it knows the target series from ``env.context['nudge_target']``"""

    @staticmethod
    def target(env):
        return env.context.get("nudge_target") or "19.0"


class StudioObjects(UpgradeCheck):
    """What Studio and people made, and the four places where its logic hides"""

    key = "up_studio"
    needs = ("ir.model", "ir.model.fields", "ir.ui.view")

    def run(self, env, clock):
        out = []
        studio = (f"EXISTS (SELECT 1 FROM ir_model_data d WHERE d.model = '%s' AND d.res_id = t.id "
                  f"AND d.module = '{STUDIO_MODULE}')")
        def add(kind, severity, model, where, params=()):
            if model not in env.registry:
                return
            count, capped, ids = scan(env, env[model], where, params)
            if count:
                out.append(finding(f"{kind}|", severity, count, capped, model, ids=ids))
        add("models", INFO, "ir.model", "t.state = 'manual'")
        add("fields", INFO, "ir.model.fields", "t.state = 'manual' AND t.model !~ '^x_'")
        add("views", INFO, "ir.ui.view", studio % "ir.ui.view")
        add("automations", INFO, "base.automation", "TRUE")
        add("actions", INFO, "ir.actions.server",
            "t.state = 'code' AND NOT EXISTS (SELECT 1 FROM ir_cron c WHERE c.ir_actions_server_id = t.id) AND "
            "NOT EXISTS (SELECT 1 FROM ir_model_data d WHERE d.model = 'ir.actions.server' AND d.res_id = t.id "
            "AND d.module = ANY(%s))", [standard_modules(env)])
        add("reports", INFO, "ir.actions.report", studio % "ir.actions.report")
        # The hidden logic: compute code and related paths on fields a person made
        add("compute", WARNING, "ir.model.fields", "t.state = 'manual' AND t.compute IS NOT NULL")
        add("related", INFO, "ir.model.fields",
            "t.state = 'manual' AND t.related IS NOT NULL AND t.related <> ''")
        return out

    def texts(self, env, key, count, sample):
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        kind = key.split("|")[0]
        return {
            "models": env._("%(count)s models made by hand (x_). Each one is yours to carry: nothing upgrades it for you "
                            "but a copy of the database, so open its screens on a staging copy."),
            "fields": env._("%(count)s custom fields (x_) on standard models. Fix: check each one still has a place on the "
                            "new series' form; the upgrade keeps the column and drops a view that cannot find its field."),
            "views": env._("%(count)s views made with Studio. Fix: open them on a staging copy of the target series; "
                           "an inherited view whose anchor moved is dropped or refused."),
            "automations": env._("%(count)s automation rules. Their code and filters are logic that no screen shows. Fix: "
                                 "read each one on a staging copy and fire it once."),
            "actions": env._("%(count)s server actions made by hand, each a piece of Python or a set of steps that no "
                             "module owns. Fix: run each once on a staging copy."),
            "reports": env._("%(count)s reports made with Studio. Fix: print one of each on a staging copy."),
            "compute": env._("%(count)s custom fields computed by code. Hidden logic: it is not in a view or an action. "
                             "Fix: read the code for removed fields and methods, and check the totals after the upgrade."),
            "related": env._("%(count)s custom fields that follow a related path. A renamed field on the way breaks them "
                             "silently. Fix: compare the values before and after on a staging copy."),
        }[kind] % {"count": count}


class RenamedReferences(UpgradeCheck):
    """Custom records that name a field the target series renamed, moved or removed"""

    key = "up_renamed"
    needs = ("ir.model", "ir.ui.view")

    def run(self, env, clock):
        out = []
        for rule in rules_for(self.target(env)):
            if rule["models"][0] not in env.registry and not any(m in env.registry for m in rule["models"]):
                pass  # the model may be gone for good: code naming it is still worth a look
            for place in PLACES.values():
                if clock.over() or not place.available(env):
                    continue
                condition, params = rule_condition(env, place, rule)
                count, capped, ids = scan_place(env, place, condition, params)
                if count:
                    out.append(finding(f"{place.key}|{rule['id']}", rule.get("severity", WARNING), count, capped,
                                       place.model, ids=ids))
        return out

    def texts(self, env, key, count, sample):
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        place, rule_id = key.split("|", 1)
        target = self.target(env)
        rule = next((r for r in rules_for(target) if r["id"] == rule_id), None)
        note = ""
        if rule:
            arabic = (env.lang or "").startswith("ar")
            note = (rule.get("note_ar") if arabic else None) or rule["note"]
        return env._("%(count)s %(where)s refer to %(what)s. Fix: %(note)s.",
                     count=count, where=place_names(env).get(place, place),
                     what=", ".join(f"{m}.{f}" if f != "*" else m for m in (rule or {}).get("models", [])[:2]
                                    for f in (rule or {}).get("fields", [])[:3]) or rule_id,
                     note=note)


class MarkupAndApis(UpgradeCheck):
    """Constructs that a series no longer reads: attrs/states, <tree>, /web# URLs, removed methods"""

    key = "up_markup"
    needs = ("ir.ui.view",)

    # (name, regex, places, first series where it hurts, severity)
    API = (
        ("name_get", r"\yname_get\(", "18.0", WARNING),
        ("fields_view_get", r"\yfields_view_get\(", "18.0", CRITICAL),
        ("user_has_groups", r"\yuser_has_groups\(", "19.0", CRITICAL),
        ("check_access_rights", r"\y(check_access_rights|check_access_rule)\(", "19.0", WARNING),
        ("read_group", r"\yread_group\(", "19.0", INFO),
    )

    def run(self, env, clock):
        target = self.target(env)
        out = []

        def add(key, severity, place, regex, extra=None):
            if not place.available(env) or clock.over():
                return
            text, params = any_text(env, place, regex)
            condition = text if not extra else f"({text}) AND ({extra})"
            count, capped, ids = scan_place(env, place, condition, params)
            if count:
                out.append(finding(key, severity, count, capped, place.model, ids=ids))

        views = PLACES["views"]
        add("attrs|views", CRITICAL, views, r"\s(attrs|states)\s*=\s*[\"']")
        tree_severity = WARNING if target == "18.0" else CRITICAL
        add("tree|views", tree_severity, views, r"<tree[\s>]")
        if "ir.actions.act_window" in env.registry and not clock.over():
            window = PLACES["windows"]
            count, capped, ids = scan_place(env, window, "t.view_mode ~ '(^|,)tree(,|$)'", [])
            if count:
                out.append(finding("tree|windows", tree_severity, count, capped, window.model, ids=ids))
        hash_severity = WARNING if target != "18.0" else INFO
        for key in ("server", "crons", "templates"):
            add(f"webhash|{key}", hash_severity, PLACES[key], r"/web#")
        if "ir.actions.act_url" in env.registry and not clock.over():
            count, capped, ids = scan(env, env["ir.actions.act_url"], "t.url ~ %s", ["/web#"])
            if count:
                out.append(finding("webhash|urls", hash_severity, count, capped, "ir.actions.act_url", ids=ids))
        for name, regex, since, severity in self.API:
            if target < since:
                continue
            for key in ("server", "crons", "fields"):
                add(f"api|{name}|{key}", severity, PLACES[key], regex)
        return out

    def texts(self, env, key, count, sample):
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        parts = key.split("|")
        where = place_names(env)
        kind, place = parts[0], parts[-1]
        names = {**where, "urls": env._("URL actions")}
        if kind == "attrs":
            return env._("%(count)s custom views still use attrs= or states= in their markup, which the new series refuses. "
                         "Fix: write the conditions as invisible=, readonly= or required= with a Python expression.",
                         count=count)
        if kind == "tree":
            return env._("%(count)s %(where)s still say tree. Fix: write list (<list> in views, list in the view mode).",
                         count=count, where=names.get(place, place))
        if kind == "webhash":
            return env._("%(count)s of %(where)s link to /web#, the old address of the backend. Fix: use /odoo/... "
                         "(for example /odoo/action-<id>/<record id>).", count=count, where=names.get(place, place))
        name = parts[1]
        hints = {
            "name_get": env._("name_get( is removed: read display_name"),
            "fields_view_get": env._("fields_view_get( is removed: use get_view("),
            "user_has_groups": env._("user_has_groups( is removed: use has_group( on the user"),
            "check_access_rights": env._("check_access_rights( and check_access_rule( are replaced by check_access("),
            "read_group": env._("read_group( is deprecated: use _read_group( or formatted_read_group("),
        }
        return env._("%(count)s of %(where)s call a method the new series no longer has. Fix: %(hint)s.",
                     count=count, where=names.get(place, place), hint=hints.get(name, name))


class CustomModules(UpgradeCheck):
    """Modules that are not Odoo's: what version, and whether a migration folder exists for the target"""

    key = "up_modules"
    needs = ("ir.module.module",)

    def run(self, env, clock):
        target = self.target(env)
        modules = custom_modules(env)
        out = []
        if modules:
            out.append(finding("modules|", INFO, len(modules), model="ir.module.module", ids=modules.ids))
        for module in modules:
            version = module.latest_version or module.installed_version or "?"
            if version.startswith(f"{target}.") or has_migration(module.name, target):
                continue
            out.append(finding(f"nomigration|{module.name}|{version}", WARNING, 1, model="ir.module.module",
                               ids=module.ids))
        out.append(finding("platform|", INFO, 1))
        return out

    def texts(self, env, key, count, sample):
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        parts = key.split("|")
        if parts[0] == "modules":
            return env._("%(count)s modules that are not Odoo's are installed. Each one must be ported to the new series "
                         "by its author; the upgrade does not touch them.", count=count)
        if parts[0] == "nomigration":
            return env._("The custom module %(name)s (version %(version)s) has no migration folder for the target series. "
                         "Fix: port it, give it a migrations/<series>.* folder when the data needs moving, and raise "
                         "its version.", name=parts[1], version=parts[2])
        return env._("An Odoo.sh upgrade works on the last daily backup of production, not on the branch's database, and "
                     "this report cannot know what the new series does to your data. Fix: test the upgrade on a staging "
                     "copy restored from that backup, and read this report again there.")


class DataQuality(UpgradeCheck):
    """What the upgrade platform refuses or trips on: dangling references and broken screens"""

    key = "up_data"
    needs = ("ir.model", "ir.model.data")

    def run(self, env, clock):
        out = []

        def add(key, severity, model, where):
            count, capped, ids = scan(env, env[model], where)
            if count:
                out.append(finding(key, severity, count, capped, model, ids=ids))

        add("views_nomodel", CRITICAL, "ir.ui.view",
            "t.model IS NOT NULL AND t.model <> '' AND NOT EXISTS (SELECT 1 FROM ir_model m WHERE m.model = t.model)")
        add("windows_nomodel", CRITICAL, "ir.actions.act_window",
            "t.res_model IS NOT NULL AND NOT EXISTS (SELECT 1 FROM ir_model m WHERE m.model = t.res_model)")
        add("fields_norelation", CRITICAL, "ir.model.fields",
            "t.relation IS NOT NULL AND NOT EXISTS (SELECT 1 FROM ir_model m WHERE m.model = t.relation)")
        add("xmlid_nomodule", WARNING, "ir.model.data",
            "t.module NOT IN ('__export__', 'base') AND NOT EXISTS (SELECT 1 FROM ir_module_module mm "
            "WHERE mm.name = t.module)")
        count, capped, ids = health.groups(
            env, env["ir.model.data"], "t.model || ':' || t.res_id",
            "t.module = ANY(ARRAY['__export__', %s])", [STUDIO_MODULE])
        if count:
            out.append(finding("xmlid_several", INFO, count, capped, "ir.model.data", ids=ids))
        # External ids whose record is gone: the tables of the models a person's modules own
        env.cr.execute("SELECT model, count(*) FROM ir_model_data WHERE module = ANY(%s) GROUP BY model "
                       "ORDER BY 2 DESC LIMIT 25", [[STUDIO_MODULE, "__export__"]])
        gone, gone_ids = 0, []
        for model, _n in env.cr.fetchall():
            if clock.over():
                break
            if model not in env.registry or not env[model]._auto:
                continue
            env.cr.execute(
                f'SELECT d.id FROM ir_model_data d WHERE d.model = %s AND d.module = ANY(%s) AND '
                f'NOT EXISTS (SELECT 1 FROM "{env[model]._table}" r WHERE r.id = d.res_id) LIMIT 200',
                [model, [STUDIO_MODULE, "__export__"]])
            rows = [r[0] for r in env.cr.fetchall()]
            gone += len(rows)
            gone_ids += rows
        if gone:
            out.append(finding("xmlid_gone", WARNING, gone, False, "ir.model.data", ids=gone_ids))
        broken = self._broken_screens(env, clock)
        if broken:
            out.append(finding("screens", CRITICAL, len(broken), False, "ir.actions.act_window", ids=broken,
                               sample=SCREENS))
        return out

    @staticmethod
    def _broken_screens(env, clock):
        """Open the screens of the custom window actions, as the memory of the
        pre-flight does: a list, form or other mode that cannot be built"""
        Action = env["ir.actions.act_window"]
        standard = standard_modules(env)
        env.cr.execute(
            "SELECT t.id FROM ir_act_window t WHERE NOT EXISTS (SELECT 1 FROM ir_model_data d WHERE "
            "d.model = 'ir.actions.act_window' AND d.res_id = t.id AND d.module = ANY(%s)) "
            "AND t.res_model IS NOT NULL ORDER BY t.id DESC LIMIT %s", [standard, SCREENS])
        broken = []
        for action in Action.browse([row[0] for row in env.cr.fetchall()]):
            if clock.over():
                break
            model = action.res_model
            if model not in env.registry:
                continue
            for mode in (action.view_mode or "list").split(","):
                mode = "list" if mode == "tree" else mode.strip()
                try:
                    with env.cr.savepoint():
                        env[model].get_view(view_type=mode)
                except Exception:  # noqa: BLE001 - any failure is the finding
                    broken.append(action.id)
                    break
        return broken

    def texts(self, env, key, count, sample):
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        return {
            "views_nomodel": env._("%(count)s views point at a model that no longer exists. The upgrade platform refuses "
                                   "them. Fix: archive or delete them after reading what they were for."),
            "windows_nomodel": env._("%(count)s window actions point at a model that no longer exists. Fix: delete them "
                                     "and the menus that open them."),
            "fields_norelation": env._("%(count)s fields link to a model that no longer exists. Fix: remove the field or "
                                       "restore the model."),
            "xmlid_nomodule": env._("%(count)s external ids belong to a module that is not in the database. Fix: remove "
                                    "the leftovers (technical menu Settings / Technical / External Identifiers)."),
            "xmlid_several": env._("%(count)s records carry several external ids of Studio or imports. Fix: keep the one "
                                   "a module needs and remove the others."),
            "xmlid_gone": env._("%(count)s external ids name a record that was deleted. Fix: remove them."),
            "screens": env._("%(count)s custom window actions already fail to open a screen (a sample of %(sample)s was "
                             "opened). Fix: repair or delete them first: the upgrade will not do it, and they would be "
                             "blamed on it."),
        }[key.split("|")[0]] % {"count": count, "sample": sample}


UPGRADE_CHECKS = (StudioObjects(), RenamedReferences(), MarkupAndApis(), CustomModules(), DataQuality())


def labels(env):
    return {
        "up_studio": env._("Studio and hand-made objects"),
        "up_renamed": env._("Renamed or removed fields"),
        "up_markup": env._("Markup and methods the new series refuses"),
        "up_modules": env._("Custom modules"),
        "up_data": env._("Data the upgrade platform rejects"),
    }


health.BY_KEY.update({check.key: check for check in UPGRADE_CHECKS})
health.LABEL_PROVIDERS.append(labels)


def run_upgrade(env, target):
    """:return: ``(findings, skipped)`` of the upgrade checks for the target series"""
    return health.run_all(env(context=dict(env.context, nudge_target=target)), checks=UPGRADE_CHECKS)
