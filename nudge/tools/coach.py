"""The coach of a new user: which screens matter for their role, how far they are, what is next.

Nothing here calls an AI. The screens of a role are the rows of ``nudge.coach.screen`` whose action is in a
menu the person can open (their groups decide which menus those are), grouped by app. What was opened is a
set of action ids; no time is kept for a visit, and nothing of anybody else.
"""
import math
from datetime import timedelta

from odoo import fields

WINDOW_DAYS = 14
MAX_SEEN = 300
MAX_VISITS = 50


def first_login(env, user):
    """:return: when the user came in for the first time, or None when no login is on record"""
    # sudo: a plain user may not read the login log, and only the date of the first row of their own is read
    log = env["res.users.log"].sudo().search([("create_uid", "=", user.id)], order="id", limit=1)
    return log.create_date or None


def is_new(env, user, days=WINDOW_DAYS):
    """:return: whether the first login of the user is within the last ``days`` days"""
    first = first_login(env, user)
    return bool(first) and first >= fields.Datetime.now() - timedelta(days=days)


def _groups_of(record):
    """:return: the groups of a user or a menu: ``group_ids`` from Odoo 19, ``groups_id`` before"""
    return record.group_ids if "group_ids" in record._fields else record.groups_id


def visible_apps(env, user):
    """:return: ``{action id: name of the app}`` of the menus ``user`` can open

    A menu counts only when every menu above it is open to the person too: Odoo 17 keeps a menu without
    groups of its own even under an app the person has no right to (Accounting for a salesman)."""
    Menu = env["ir.ui.menu"].with_user(user)
    mine = _groups_of(user.sudo())
    allowed = {}

    def open_to_me(menu):
        if menu.id not in allowed:
            groups = _groups_of(menu.sudo())
            allowed[menu.id] = (not groups or bool(groups & mine)) and (not menu.parent_id or open_to_me(menu.parent_id))
        return allowed[menu.id]

    out = {}
    for menu in Menu.browse(Menu._visible_menu_ids()).filtered("action"):
        if open_to_me(menu):
            out.setdefault(menu.action.id, (menu.complete_name or "").split("/")[0].strip())
    return out


def key_screens(env, user, screens):
    """:param screens: ``nudge.coach.screen`` records, in order
    :return: the screens of the role of ``user``: ``[{"id", "name", "app", "action_id", "xmlid", "module"}]``"""
    apps = visible_apps(env, user)
    out = []
    for screen in screens:
        action = env.ref(screen.action_xmlid, raise_if_not_found=False) if screen.action_xmlid else None
        if not action or action.id not in apps:
            continue
        out.append({
            "id": screen.id, "name": screen.name, "app": apps[action.id], "action_id": action.id,
            "xmlid": screen.action_xmlid, "module": screen.module_name or "",
        })
    return out


def progress(seen, screens):
    """:return: ``(opened, total, percent)``: how many of the key screens were opened"""
    ids = {screen["action_id"] for screen in screens}
    opened = len(ids & set(seen or ()))
    total = len(ids)
    return opened, total, round(100 * opened / total) if total else 100


def minutes_of(tour):
    """:return: the minutes a tour takes, about: three steps a minute"""
    return max(1, math.ceil((tour.get("steps") or 1) / 3))


def suggest(screens, seen, required, done, tours):
    """What to do next, in this order: a tour the manager required and not yet taken; the first key
    screen not yet opened, with the tour of that screen when there is one, else the tour of its app.

    :param screens: from :func:`key_screens`
    :param seen: action ids opened
    :param required: tour ids required, in order
    :param done: tour ids taken
    :param tours: ``[{"id", "name", "steps", "xmlid", "module"}]`` the tours the person can replay
    :return: ``{"kind": "tour"|"screen", ...}`` or None when there is nothing left
    """
    by_id = {tour["id"]: tour for tour in tours}
    for tour_id in required:
        if tour_id in by_id and tour_id not in done:
            return {"kind": "tour", "tour_id": tour_id, "tour": by_id[tour_id], "screen": None, "required": True}
    seen = set(seen or ())
    for screen in screens:
        if screen["action_id"] in seen:
            continue
        pool = [tour for tour in tours if tour["id"] not in done]
        tour = next((t for t in pool if t["xmlid"] and t["xmlid"] == screen["xmlid"]), None) or next(
            (t for t in pool if t["module"] and t["module"] == screen["module"]), None)
        return {"kind": "screen", "tour_id": tour["id"] if tour else 0, "tour": tour, "screen": screen,
                "required": False}
    return None
