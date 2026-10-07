"""Knowing the database Nudge is installed on."""
from datetime import timedelta

from psycopg2 import errors

from odoo import fields

from ..profile import activity, retrieval
from . import base

MAX_FIELDS = 80
# Counting as the person, for one kind of record
LIVE_MS = 2000
# Under the size a result may have, with room for what comes around the fields
MAX_FIELDS_CHARS = 4000
ATTRIBUTES = ["string", "type", "help", "required", "readonly", "relation", "selection"]


@base.register
class SearchDatabaseProfile(base.Tool):
    name = "search_database_profile"
    description = (
        "Look up how this particular database is built and configured: its custom modules and "
        "what they are for, the fields and buttons they add to standard screens, how each app is "
        "set up (steps of deliveries, approvals, options switched on), its automation. Use it "
        "whenever the answer depends on this database rather than on standard Odoo, and before "
        "saying that something is or is not possible here. Search with English words or technical "
        "names, such as \"maintenance\", \"delivery steps\", \"sale.order\": the profile is "
        "written in English."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 2, "maxLength": 120},
            "kind": {
                "type": "string",
                "enum": ["module", "model", "configuration", "automation", "security"],
                "description": "To look in one part of the profile only.",
            },
        },
    }

    def run(self, env, arguments, screen):
        profile = env["nudge.profile"]._ensure_current()
        pieces = retrieval.search(env, profile, arguments["query"], arguments.get("kind"))
        if not pieces:
            return base.ToolResult(
                "Nothing in the profile of this database matches, among what this person may "
                "know. It may be standard Odoo, which the profile does not repeat. Try other "
                "words, or describe_model for the fields of a model.")
        return base.ToolResult(retrieval.render(pieces))


def _live(env, model, since):
    """:return: (records, changed since, last change) as the person sees
    them, or None when counting takes too long"""
    cr = env.cr
    env.flush_all()
    savepoint = cr.savepoint(flush=False)
    try:
        cr.execute("SET LOCAL statement_timeout = %s", (LIVE_MS,))
        records = model.search_count([])
        changed = model.search_count([("write_date", ">=", since)])
        last = model.search_read([], ["write_date"], order="write_date desc", limit=1)
        return records, changed, last[0]["write_date"] if last else False
    except errors.QueryCanceled:
        return None
    finally:
        savepoint.close(rollback=True)


@base.register
class LocateData(base.Tool):
    name = "locate_data"
    description = (
        "Find where a topic lives in THIS database: the kinds of records that hold it, with "
        "how much each is used (records, changes in the last 90 days, last change, counted as "
        "the person sees them), the module they come from and the menus that open them. A topic "
        "is often covered twice, by a standard app and by a custom module, and only one is "
        "used: this tells which. Call it before answering about the data of this database, "
        "before reading records of a topic, and before naming a menu or a model. Give the "
        "topic in a few words, in English and in the person's words: \"payroll salary راتب\", "
        "\"attendance حضور\", or a technical name."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["topic"],
        "properties": {
            "topic": {"type": "string", "minLength": 2, "maxLength": 120},
        },
    }

    def run(self, env, arguments, screen):
        profile = env["nudge.profile"]._ensure_current()
        pieces, strong = retrieval.locate(env, profile, arguments["topic"])
        if not pieces:
            return base.ToolResult(
                "No kind of record this person may read matches. Try other words, in English "
                "and in the words of the interface, or search_database_profile.")
        since = fields.Datetime.now() - timedelta(days=activity.RECENT_DAYS)
        visible = set(env["ir.ui.menu"]._visible_menu_ids())
        Menu = env["ir.ui.menu"]
        found = []
        for piece in pieces:
            data = piece.data or {}
            model = env[data["model"]]
            entry = {
                "model": data["model"],
                "label": retrieval.label_of(env, data),
                "module": data.get("module_title") or data.get("module") or "",
            }
            if data.get("custom"):
                entry["custom_module"] = True
            live = _live(env, model, since)
            if live:
                records, changed, last = live
                entry.update(records=records, changed_last_90_days=changed,
                             last_change=fields.Date.to_string(last) if last else "")
            else:
                entry["use"] = activity.level(data.get("changed") or 0)
            menus = [menu["id"] for menu in data.get("menus") or [] if menu["id"] in visible]
            if menus:
                entry["menus"] = [
                    {"menu_id": menu.id, "path": menu.complete_name} for menu in Menu.browse(menus)]
            found.append(entry)
        result = {"found": found, "note": self._note(found, strong)}
        return base.ToolResult(base.dump(result))

    @staticmethod
    def _note(found, strong):
        """:param strong: how many of the first ones match the topic best: only
            those are compared"""
        counted = [entry for entry in found[:strong] if "changed_last_90_days" in entry]
        first = found[0]
        note = (
            f"Answer from {first['label']} ({first['model']}) unless the question names another, "
            "and say which one you read.")
        if len(counted) >= 2:
            counted.sort(key=lambda entry: -entry["changed_last_90_days"])
            used, other = counted[0], counted[1]
            if used["changed_last_90_days"] and used["changed_last_90_days"] >= 3 * other["changed_last_90_days"]:
                note = (
                    f"The work on this is in {used['label']} ({used['model']}): answer from it, "
                    f"and say so. {other['label']} ({other['model']}) is little used here.")
            elif used["changed_last_90_days"]:
                note = (
                    "Several of these are in use: pick the one the question is about, read it, "
                    "and say which one you read.")
            else:
                note = "None of these changed in 90 days: the business may keep this elsewhere."
        return note + (
            " Before saying that something does not exist, look in the next one. To show the "
            "way there, show_menu_path with a menu_id.")


@base.register
class DescribeModel(base.Tool):
    name = "describe_model"
    description = (
        "List the fields of a kind of record, by its technical name such as \"sale.order\" or "
        "\"maintenance.request\": label, type, help, choices, as this person sees them, and which "
        "ones were added by custom modules. Use it to explain a field or to find where a piece of "
        "information is kept."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["model"],
        "properties": {
            "model": {"type": "string", "pattern": r"[a-z][a-z0-9_.]{1,80}"},
            "words": {
                "type": "string",
                "maxLength": 80,
                "description": "To keep only the fields whose name or label has one of these words.",
            },
        },
    }

    def run(self, env, arguments, screen):
        name = arguments["model"]
        if name not in env or not env[name].has_access("read"):
            # The same answer in both cases: the models a person cannot read
            # are not revealed by their name
            return base.ToolResult(
                f"There is no model {name!r} that this person can read.", is_error=True)
        model = env[name]
        profile = env["nudge.profile"]._ensure_current().sudo()
        piece = profile.slice_ids.filtered(lambda piece: piece.key == f"model:{name}")
        added = {entry["name"]: entry.get("added_by") for entry in (piece.data or {}).get("fields", [])}

        words = (arguments.get("words") or "").lower().split()
        fields = []
        # fields_get leaves out what this person cannot read
        for field_name, info in sorted(model.fields_get(attributes=ATTRIBUTES).items()):
            label = info.get("string") or field_name
            if words and not any(word in f"{field_name} {label}".lower() for word in words):
                continue
            entry = {"name": field_name, "label": label, "type": info["type"]}
            if info.get("help"):
                entry["help"] = " ".join(info["help"].split())[:200]
            if info.get("required"):
                entry["required"] = True
            if info.get("relation"):
                entry["relation"] = info["relation"]
            if info.get("selection"):
                entry["choices"] = [label for _key, label in info["selection"]][:15]
            if field_name in added:
                entry["added_by"] = added[field_name]
            fields.append(entry)
        # What the database adds is what cannot be known otherwise: it goes first
        fields.sort(key=lambda entry: "added_by" not in entry)
        shown, size = [], 0
        for entry in fields[:MAX_FIELDS]:
            size += len(base.dump(entry))
            if size > MAX_FIELDS_CHARS:
                break
            shown.append(entry)
        result = {
            "model": name,
            "label": model._description,
            "fields_found": len(fields),
            "fields": shown,
        }
        if len(shown) < len(fields):
            result["note"] = (
                f"{len(shown)} of {len(fields)} fields are listed. "
                "Give words to list the ones you are looking for.")
        if piece:
            result["in_this_database"] = piece._get_summary()[:1200]
        return base.ToolResult(base.dump(result))
