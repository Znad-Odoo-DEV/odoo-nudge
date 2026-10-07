"""Where the work is: how much each kind of record is used.

Read with the rest of the profile, once a day, by counting in SQL: how many
records, how many changed lately, when last, by how many people. A topic is
often covered twice, by a standard app and by a custom module; these numbers
tell which one the business works in.

Numbers only, nothing of what the records hold. Who may know them is decided
when they are read (``retrieval.can_read``), and what the person is told in a
conversation is counted again as them (``tools/profile.py``).
"""
import logging
import re
import time
from datetime import timedelta

from psycopg2 import errors

from odoo import SUPERUSER_ID, fields
from odoo.tools import SQL

_logger = logging.getLogger(__name__)

RECENT_DAYS = 90
NEW_DAYS = 30
# A table that takes longer is left to the estimate of PostgreSQL
STATEMENT_MS = 3000
# For the whole reading; what is left after it is estimated
BUDGET_SECONDS = 60
MAX_MENUS = 4
# The traces of the work rather than the work, and Odoo's own machinery: the
# same models as those Nudge never reads (tools/records.py)
TECHNICAL = re.compile(
    r"^(ir|base|bus|nudge|auth|iap|fetchmail|web|web_editor|web_tour|digest|snailmail|sms|"
    r"mail|res\.users|res\.groups|res\.config|res\.device|payment\.(provider|token|method))"
    r"(\.|_|$)"
)


def _candidates(env):
    """:return: {model: table} of the business models that keep who changed
    them and when"""
    found = {}
    for name, model_class in env.registry.items():
        if (model_class._abstract or model_class._transient or not model_class._auto
                or not model_class._log_access or name not in env or TECHNICAL.match(name)):
            continue
        found[name] = model_class._table
    return found


def _estimates(cr, tables):
    """:return: {table: rows as PostgreSQL estimates them}, for the tables
    that exist; -1 when it never looked"""
    cr.execute(SQL(
        """SELECT c.relname, c.reltuples::bigint
             FROM pg_class c
             JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema()
              AND c.relkind IN ('r', 'p')
              AND c.relname = ANY(%s)""",
        sorted(tables),
    ))
    return dict(cr.fetchall())


def _count(cr, table, recent, new):
    """:return: (records, changed lately, created lately, last change, people
    who changed them lately), or None when the table takes too long. The time
    limit lives in a savepoint that is always rolled back."""
    savepoint = cr.savepoint(flush=False)
    try:
        cr.execute("SET LOCAL statement_timeout = %s", (STATEMENT_MS,))
        cr.execute(SQL(
            """SELECT count(*),
                      count(*) FILTER (WHERE write_date >= %(recent)s),
                      count(*) FILTER (WHERE create_date >= %(new)s),
                      max(write_date),
                      count(DISTINCT write_uid) FILTER (
                          WHERE write_date >= %(recent)s AND write_uid != %(root)s)
                 FROM %(table)s""",
            recent=recent, new=new, root=SUPERUSER_ID, table=SQL.identifier(table),
        ))
        return cr.fetchone()
    except errors.QueryCanceled:
        _logger.info("Nudge: counting %s took too long, its size is estimated", table)
        return None
    finally:
        savepoint.close(rollback=True)


def _languages(env):
    """:return: the languages in use, and English, in which every name is
    written first and which the model searches with"""
    return sorted({"en_US", *env["res.lang"].sudo().search([("active", "=", True)]).mapped("code")})


def _labels(env, names):
    """:return: {model: {language: label}}, in every language in use"""
    labels = {name: {} for name in names}
    Model = env["ir.model"].sudo()
    for code in _languages(env):
        for row in Model.with_context(lang=code).search_read([("model", "in", names)], ["model", "name"]):
            labels[row["model"]][code] = row["name"]
    return labels


def _menus(env, names):
    """:return: {model: [{"id", "paths": {language: path}}]}, the menus that
    open a list of the model, shortest first"""
    actions = env["ir.actions.act_window"].sudo().search([("res_model", "in", names)])
    if not actions:
        return {}
    model_of = {action.id: action.res_model for action in actions}
    Menu = env["ir.ui.menu"].sudo()
    menus = Menu.search([("action", "in", [f"ir.actions.act_window,{id_}" for id_ in actions.ids])])
    languages = _languages(env)
    paths = {menu.id: {} for menu in menus}
    for code in languages:
        for menu in menus.with_context(lang=code):
            # complete_name is kept once for every language: the names are not
            names, parent = [], menu
            while parent:
                names.append(parent.name or "")
                parent = parent.parent_id
            paths[menu.id][code] = "/".join(reversed(names))
    found = {}
    for menu in sorted(menus, key=lambda menu: (len(menu.complete_name or ""), menu.id)):
        model = model_of.get(menu.action.id)
        entries = found.setdefault(model, [])
        if model and len(entries) < MAX_MENUS:
            entries.append({"id": menu.id, "paths": paths[menu.id]})
    return found


def level(changed):
    """:return: how much a kind of record is used, in words: the overview
    says this rather than numbers that not everybody may know"""
    if changed >= RECENT_DAYS:
        return "used every day"
    if changed >= RECENT_DAYS // 7:
        return "used every week"
    if changed:
        return "used now and then"
    return "not used lately"


def activity(data):
    """:return: how much work a kind of record sees, to rank them"""
    return (data.get("changed") or 0) + (data.get("created") or 0)


def collect_activity(env, custom):
    from .collectors import OWN_MODULE, _slice

    tables = _candidates(env)
    cr = env.cr
    estimates = _estimates(cr, set(tables.values()))
    now = fields.Datetime.now()
    recent, new = now - timedelta(days=RECENT_DAYS), now - timedelta(days=NEW_DAYS)
    started = time.monotonic()
    numbers = {}
    for name, table in sorted(tables.items()):
        estimate = estimates.get(table)
        if estimate is None:
            continue
        row = None
        if time.monotonic() - started < BUDGET_SECONDS:
            row = _count(cr, table, recent, new)
        if row is None:
            if estimate > 0:
                numbers[name] = {"records": estimate, "estimated": True}
            continue
        records, changed, created, last, people = row
        if records:
            numbers[name] = {
                "records": records,
                "changed": changed,
                "created": created,
                "last_change": fields.Date.to_string(last) if last else "",
                "people": people,
            }
    if not numbers:
        return []

    names = sorted(numbers)
    labels = _labels(env, names)
    menus = _menus(env, names)
    titles = dict(
        (module["name"], module["shortdesc"])
        for module in env["ir.module.module"].sudo().search_read(
            [("state", "=", "installed")], ["name", "shortdesc"])
    )
    business = custom - {OWN_MODULE}
    slices = []
    for name in names:
        data = numbers[name]
        module = getattr(env.registry[name], "_original_module", "") or ""
        label = labels[name].get("en_US") or env[name]._description or name
        others = sorted({value for value in labels[name].values() if value and value != label})
        data.update(
            model=name,
            label=label,
            labels=labels[name],
            module=module,
            module_title=titles.get(module, module),
            custom=module in business,
            menus=menus.get(name, []),
        )
        origin = f"{data['module_title']} ({module})" if module else "the database"
        lines = [
            f"{label} ({name}), from {origin}" + (", a custom module." if data["custom"] else "."),
            f"Also called: {', '.join(others)}." if others else "",
        ]
        if data.get("estimated"):
            lines.append(f"About {data['records']:,} records (estimated).")
        else:
            lines.append(
                f"{data['records']:,} records; {data['changed']:,} changed in the last "
                f"{RECENT_DAYS} days, {data['created']:,} created in the last {NEW_DAYS}; "
                f"last change {data['last_change'] or 'unknown'}; {level(data['changed'])}.")
        slices.append(_slice("activity", f"activity:{name}", f"{label} ({name})", lines, data,
                             model_name=name))
    _logger.info("Nudge: use of %s kinds of records read in %.1f s",
                 len(slices), time.monotonic() - started)
    return slices
