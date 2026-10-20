"""The numbers of the home page of each person, counted by Odoo as them.

The tiles are the lines of the morning digest (``digest.py``), kept even
when they are at zero, with a short history behind each: the records of the
same kind that came in each day of the last two weeks. Nothing here calls an
AI, and nothing is read with more rights than the person has.
"""
import time
from datetime import timedelta

from odoo import _, fields

from . import digest
from .analytics import TooBig

CACHE_SECONDS = 300
DAYS = 7
# The pages kept for one person: one for each set of companies and language they looked with
MAX_CACHED = 4
MAX_LAYOUT_KEYS = 100
MAX_KEY_CHARS = 80
APPROVAL_TAG = "[Nudge approval]"
MAX_SAYS = 3



def clear_cache(env):
    """Forget what every person was shown: the next opening of the home page counts again. The page is kept in
    the settings of each person, so every worker agrees on what is fresh."""
    env.cr.execute("UPDATE res_users_settings SET nudge_home_cache = NULL WHERE nudge_home_cache IS NOT NULL")
    env["res.users.settings"].invalidate_model(["nudge_home_cache"])


def _history(env, line):
    """:return: ``(series, trend)``: the records of the line that came in each of the last
    7 days, and the change of this week against the week before, in percent (None when
    nothing came in the week before)"""
    model = line.get("model")
    if not model or model not in env or "create_date" not in env[model]._fields:
        return [0] * DAYS, None
    today = fields.Date.context_today(env["res.users"])
    start = today - timedelta(days=2 * DAYS - 1)
    try:
        rows = env[model]._read_group(
            list(line["domain"]) + [("create_date", ">=", fields.Datetime.to_string(start))],
            ["create_date:day"], ["__count"])
    except Exception:  # noqa: BLE001 - a tile without its history is still a tile
        return [0] * DAYS, None
    per_day = {str(day)[:10]: count for day, count in rows if day}
    days = [fields.Date.to_string(start + timedelta(days=index)) for index in range(2 * DAYS)]
    counts = [per_day.get(day, 0) for day in days]
    before, now = sum(counts[:DAYS]), sum(counts[DAYS:])
    trend = round((now - before) / before * 100) if before else None
    return counts[DAYS:], trend


def _tile(env, block, line):
    series, trend = _history(env, line)
    if line.get("change") is not None:
        trend = line["change"]
    return {
        "key": f"{block['key']}.{line.get('key') or line['model']}",
        "section": block["key"],
        "label": line["label"],
        "name": line["name"],
        "count": line["count"],
        "amount": line["amount"],
        "trend": trend,
        "series": series,
        "model": line["model"],
    }


def _pending_approvals(env):
    if "mail.activity" not in env or not digest.can_read(env, "mail.activity"):
        return []
    activities = env["mail.activity"].search([
        ("user_id", "=", env.uid), ("summary", "=like", f"{APPROVAL_TAG}%")], limit=10)
    out = []
    for activity in activities:
        record = env[activity.res_model].browse(activity.res_id) if activity.res_model in env else None
        # asked of someone else too, and answered since: not waiting any more
        if record is None or not record.exists() or (
                "x_nudge_approval_state" in record._fields and record.x_nudge_approval_state != "pending"):
            continue
        out.append({
            "kind": "approval", "text": (activity.summary or "")[len(APPROVAL_TAG):].strip() or activity.res_name,
            "model": activity.res_model, "res_id": activity.res_id})
    return out[:MAX_SAYS]


def _proposals(env):
    if "nudge.memory" not in env or not digest.can_read(env, "nudge.memory"):
        return []
    if not env.user.has_group("nudge.group_nudge_manager"):
        return []
    memory = env["nudge.memory"].search([("state", "=", "proposed")], order="id desc", limit=MAX_SAYS)
    return [{"kind": "proposal", "text": item.question, "model": "nudge.memory", "res_id": item.id}
            for item in memory]


def _findings(env):
    if "nudge.health.report" not in env or not digest.can_read(env, "nudge.health.report"):
        return []
    report = env["nudge.health.report"].search(
        [("company_id", "=", env.company.id), ("scope", "=", "health")], order="id desc", limit=1)
    if not report or not (report.critical_count or report.warning_count):
        return []
    text = _("Last health check: %(critical)s critical, %(warning)s warnings",
                 critical=report.critical_count, warning=report.warning_count)
    return [{"kind": "health", "text": text, "model": report._name, "res_id": report.id}]


def says(env):
    """:return: what Nudge has to say: approvals waiting, questions asked again and again,
    what the last health check found"""
    out = []
    for build in (_pending_approvals, _proposals, _findings):
        try:
            out += build(env)
        except Exception:  # noqa: BLE001 - one source failing never costs the person the others
            continue
    return out


def _blocks(env):
    keep = env(context=dict(env.context, nudge_keep_zero=True))
    return keep, [block for block in digest.gather(keep) if block["key"] != "overview"]


REPORT_SERIES = 12


def report_tiles(env):
    """:return: the section of the reports the person kept for their home page: the total of
    the first measure and its first groups, read through the saved report as them"""
    if "nudge.report" not in env or not digest.can_read(env, "nudge.report"):
        return []
    out = []
    for report in env["nudge.report"].search([("home", "=", True)]):
        try:
            data = report.numbers()
        except TooBig:
            out.append({
                "key": f"reports.{report.id}", "section": "reports", "kind": "report", "report_id": report.id,
                "label": report.name, "name": report.name, "count": _("Too big: open the report"),
                "amount": "", "trend": None, "series": [], "model": report.model_name,
            })
            continue
        except Exception:  # noqa: BLE001 - a report that cannot be read now does not cost the person their page
            continue
        out.append({
            "key": f"reports.{report.id}", "section": "reports", "kind": "report", "report_id": report.id,
            "label": report.name, "name": report.name, "count": data["text"], "amount": data["measure"],
            "trend": None, "series": data["values"][:REPORT_SERIES], "model": report.model_name,
        })
    return [{"key": "reports", "title": _("My Reports"), "tiles": out}] if out else []


def tiles(env):
    """:return: ``[{"key", "title", "tiles"}]``, a section for each app the person has the group of"""
    keep, blocks = _blocks(env)
    return [{
        "key": block["key"], "title": block["title"],
        "tiles": [_tile(keep, block, line) for line in block["lines"]],
    } for block in blocks] + report_tiles(env)


def payload(env, force=False):
    """:return: what the home page shows, kept five minutes for the person"""
    key = "%s|%s" % (",".join(str(company_id) for company_id in sorted(env.companies.ids)), env.lang)
    # sudo: the page is kept in the settings of the person who looks at it, and nowhere else
    settings = env["res.users.settings"].sudo()._find_or_create_for_user(env.user)
    kept = settings.nudge_home_cache if isinstance(settings.nudge_home_cache, dict) else {}
    now = time.time()
    cached = kept.get(key)
    if cached and not force and now - cached.get("at", 0) < CACHE_SECONDS:
        return cached["data"]
    data = {"sections": tiles(env), "says": says(env),
            "generated": fields.Datetime.to_string(fields.Datetime.now())}
    kept = {name: page for name, page in kept.items() if name != key and now - page.get("at", 0) < CACHE_SECONDS}
    kept = dict(list(kept.items())[-(MAX_CACHED - 1):])
    kept[key] = {"at": now, "data": data}
    settings.nudge_home_cache = kept
    return data


def tile_lines(env):
    """:return: ``{key: line}`` of the digest lines, to open the list behind a tile"""
    _keep, blocks = _blocks(env)
    return {f"{block['key']}.{line.get('key') or line['model']}": line
            for block in blocks for line in block["lines"]}


def clean_layout(value):
    """:return: a layout as it is kept: the order of the tiles and the ones hidden, keys only"""
    value = value if isinstance(value, dict) else {}

    def keys(items):
        out = []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, str) and 0 < len(item) <= MAX_KEY_CHARS and item not in out:
                out.append(item)
        return out[:MAX_LAYOUT_KEYS]

    return {"order": keys(value.get("order")), "hidden": keys(value.get("hidden"))}
