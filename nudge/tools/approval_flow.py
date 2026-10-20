"""Approval flows from a sentence, on Community: no Studio.

"Any purchase order above 50,000 needs the finance manager before it is
confirmed": Nudge makes, with the builders of Developer mode and nothing
else, the fields that hold the approval, a request, an approve and a refuse
action with their buttons, and an automation rule that stops the
confirmation of what is not approved yet. Several people, in the order
said, are several levels. With Studio's approval rules installed (Enterprise)
these are used instead, as ``prepare_approval_rule`` makes them.

The set is made after one yes, tried on a dummy record, and undone as a
set by ``prepare_undo``: every part is logged as a step of the same request.
"""
from odoo import _
import re
import secrets
from types import SimpleNamespace

from . import approval_sentence, base
from .developer import Building, automations_installed, check_developing, ir_model
from .records import MODEL_PATTERN, Refused, get_model, model_label, refusing

APPROVAL_TAG = "[Nudge approval]"
NAME_PREFIX = "Nudge approval"
STATE_FIELD = "x_nudge_approval_state"
APPROVER_FIELD = "x_nudge_approver_id"
LEVEL_FIELD = "x_nudge_approval_level"
MAX_LEVELS = 4
MAX_NOTIFIED = 10
GENERAL = "CEO"

# What a document is, the amount it is judged by, the states that mean "confirmed", the button
PROFILES = {
    "purchase_order": {
        "model": "purchase.order", "amount": "amount_total", "states": ["to approve", "purchase"],
        "conditions": [], "button": "button_confirm",
        "lines": ("order_line", "product_qty", "price_unit")},
    "sales_order": {
        "model": "sale.order", "amount": "amount_total", "states": ["sale"],
        "conditions": [], "button": "action_confirm",
        "lines": ("order_line", "product_uom_qty", "price_unit")},
    "vendor_bill": {
        "model": "account.move", "amount": "amount_total", "states": ["posted"],
        "conditions": [{"field": "move_type", "operator": "in", "value": "in_invoice,in_refund"}],
        "button": "action_post", "lines": ("invoice_line_ids", "quantity", "price_unit")},
    "customer_invoice": {
        "model": "account.move", "amount": "amount_total", "states": ["posted"],
        "conditions": [{"field": "move_type", "operator": "in", "value": "out_invoice,out_refund"}],
        "button": "action_post", "lines": ("invoice_line_ids", "quantity", "price_unit")},
}
BY_MODEL = {"purchase.order": "purchase_order", "sale.order": "sales_order", "account.move": "vendor_bill"}


def doc_label(env, kind):
    return {
        "purchase_order": _("purchase order"), "sales_order": _("sales order"),
        "vendor_bill": _("vendor bill"), "customer_invoice": _("customer invoice"),
    }[kind]


def verb_label(env, kind):
    return {
        "purchase_order": _("confirmed"), "sales_order": _("confirmed"),
        "vendor_bill": _("posted"), "customer_invoice": _("posted"),
    }[kind]


def money(amount):
    text = f"{float(amount):,.2f}"
    return text[:-3] if text.endswith(".00") else text


def users_field(env):
    """The groups hold their users in ``user_ids`` from Odoo 19, in ``users`` before."""
    return "user_ids" if "user_ids" in env["res.groups"]._fields else "users"


def groups_field(env):
    return "group_ids" if "group_ids" in env["res.users"]._fields else "groups_id"


# How people name a role: the rank (manager ~ administrator ~ مدير) and the app (the words of its privilege or category)
RANK_WORDS = {
    "manager": ("manager", "managers", "administrator", "administrators", "admin", "head", "director", "officer in charge",
                "مدير", "مديره", "مسؤول", "مسوول", "مسئول", "رئيس"),
    "user": ("user", "users", "staff", "employee", "مستخدم", "موظف"),
}
FILLER_WORDS = {"the", "a", "an", "of", "for", "s", "approval", "approvals", "approve", "approver", "team", "role", "group",
                "and", "or", "by", "في", "من", "على", "موافقه", "ال"}
APP_WORDS_AR = {
    "مشتريات": "purchase", "شراء": "purchase", "مبيعات": "sales", "بيع": "sales", "محاسبه": "accounting",
    "حسابات": "accounting", "ماليه": "accounting", "مخزون": "inventory", "مخازن": "inventory", "مشاريع": "project",
    "موظفين": "employees", "تصنيع": "manufacturing", "صيانه": "maintenance", "اجازات": "time off",
}


def group_scope_field(env):
    """The record that names the app of a group: ``privilege_id`` from Odoo 19, ``category_id`` before."""
    return "privilege_id" if "privilege_id" in env["res.groups"]._fields else "category_id"


def _words(text):
    return re.findall(r"\w+", approval_sentence._fold(text or "").lower())


def _split_role(text):
    """:return: ``(rank, app words)`` of "purchase manager", "manager of sales", "the purchase administrator"
    ("مدير المشتريات"); ``rank`` is ``"manager"``, ``"user"`` or ``""``"""
    ranks = {approval_sentence._fold(word): key for key, words in RANK_WORDS.items() for word in words}
    rank, app = "", []
    for word in _words(text):
        bare = word[2:] if word.startswith("ال") and len(word) > 4 else word
        if word in ranks or bare in ranks:
            rank = rank or ranks.get(word) or ranks[bare]
        elif word in FILLER_WORDS or bare in FILLER_WORDS:
            continue
        else:
            app.append(APP_WORDS_AR.get(bare, bare))
    return rank, app


def _stem(word):
    return word[:5] if len(word) > 5 else word


def find_group_by_role(env, text):
    """:return: the one group that "<app> manager" / "manager of <app>" / "<app> user" names, by the privilege
    (the category before Odoo 19) of the group and its own name, or an empty recordset"""
    rank, app = _split_role(text)
    Groups = env["res.groups"]
    if not rank or not app:
        return Groups
    scope = group_scope_field(env)
    ranked = {key: {approval_sentence._fold(word) for word in words if word.isascii()} for key, words in RANK_WORDS.items()}
    found = Groups
    for group in Groups.with_context(lang="en_US").search([(scope, "!=", False)]):
        label = set(_words(group[scope].name))
        label |= {_stem(word) for word in label}
        if not all(_stem(word) in label or word in label for word in app):
            continue
        name = set(_words(group.name))
        if rank == "manager" and name & ranked["manager"]:
            found |= group
        elif rank == "user" and name & ranked["user"] and not name & ranked["manager"]:
            found |= group
    return found if len(found) == 1 else Groups


def find_group(env, text, role_xmlid=None):
    """:return: ``(group, note)``: the group a level names, by external id, by role or by name;
    ``note`` is what the person is told when the group had to be chosen"""
    Groups = env["res.groups"]
    text = (text or "").strip()
    if role_xmlid:
        group = env.ref(role_xmlid, raise_if_not_found=False)
        if group and group._name == "res.groups":
            return group, ""
    if "." in text and " " not in text:
        group = env.ref(text, raise_if_not_found=False)
        if group and group._name == "res.groups":
            return group, ""
    if text:
        found = Groups.search([("name", "=ilike", text)], limit=2) or Groups.search([("name", "ilike", text)], limit=2)
        if len(found) == 1:
            return found, ""
        group = find_group_by_role(env, text)
        if group:
            return group, ""
        roles = approval_sentence._roles(approval_sentence._fold(text))
        for _position, role, xmlid in roles:
            if xmlid:
                group = env.ref(xmlid, raise_if_not_found=False)
                if group:
                    return group, ""
            names = {"general": ("General Manager", "CEO", "Managing Director")}.get(role, ())
            for name in names:
                found = Groups.search([("name", "ilike", name)], limit=2)
                if len(found) == 1:
                    return found, ""
    admin = env.ref("base.group_system")
    return admin, _("No group is named “%(name)s”: the administrators (%(group)s) approve instead. "
                        "Change it on the server action if someone else should.",
                        name=text or _("the manager"), group=admin.display_name)


# ------------------------------------------------------------------ the code that is made
def _levels_literal(levels):
    return "[" + ", ".join(
        f"[{float(level['amount'])!r}, {level['group'].id}, {level['group'].display_name!r}]" for level in levels) + "]"


def _needed(amount):
    return f"len([level for level in LEVELS if (record.{amount} or 0) > level[0]])"


def block_code(env, profile, levels, kind):
    words = {
        "refused": _("was refused. Change it, then press Request approval again."),
        "needs": _("needs the approval of"),
        "above": _("(above"),
        "go_on": _("before it can be %(done)s. Press Request approval; they approve it from the form.",
                       done=verb_label(env, kind)),
    }
    return (
        f"LEVELS = {_levels_literal(levels)}\n"
        "for record in records:\n"
        f"    needed = {_needed(profile['amount'])}\n"
        f"    done = record.{LEVEL_FIELD} or 0\n"
        "    if needed and done < needed:\n"
        f"        if record.{STATE_FIELD} == 'refused':\n"
        f"            raise UserError(record.display_name + ' ' + {words['refused']!r})\n"
        f"        raise UserError(record.display_name + ' ' + {words['needs']!r} + ' ' + LEVELS[done][2] + ' ' "
        f"+ {words['above']!r} + ' ' + '{{:,.2f}}'.format(LEVELS[done][0]) + ') ' + {words['go_on']!r})\n")


def guard_code(env, token):
    """Who wrote the approval is the buttons' business alone: a write of the three fields that does not carry
    the secret of the flow (kept in the code of the actions, which only an administrator reads) is refused."""
    refusal = _("is approved with the buttons Request approval and Approve, not by writing the fields.")
    return (
        "for record in records:\n"
        f"    if env.context.get('nudge_approval_step') != {token!r} and (\n"
        f"            record.{STATE_FIELD} or record.{LEVEL_FIELD} or record.{APPROVER_FIELD}):\n"
        f"        raise UserError(record.display_name + ' ' + {refusal!r})\n")


def _schedule(env, who, profile, indent="    "):
    """The lines that ask the users of the group of level ``who`` (a python expression) to approve."""
    users = users_field(env)
    lines = [
        f"for person in env['res.groups'].browse(LEVELS[{who}][1])['{users}'][:{MAX_NOTIFIED}]:",
        "    record.activity_schedule(",
        "        'mail.mail_activity_data_todo', user_id=person.id,",
        f"        summary={APPROVAL_TAG!r} + ' ' + record.display_name + ' ' + SUMMARY + ' ' + "
        f"'{{:,.2f}}'.format(record.{profile['amount']} or 0),",
        "        note=NOTE + ' ' + env.user.name)",
    ]
    return "".join(f"{indent}{line}" + chr(10) for line in lines)


def _lock():
    """The lines that open the loop of a generated action: the row is locked by writing to it, so of two people
    pressing at the same instant the second waits and then reads what the first did"""
    return (
        f"    record.write({{'{STATE_FIELD}': record.{STATE_FIELD}}})\n"
        "    record.invalidate_recordset()\n")


def request_code(env, profile, levels, token=""):
    words = {
        "summary": _("needs your approval:"),
        "note": _("Approval asked by"),
        "not_needed": _("does not need an approval."),
        "already": _("is approved already."),
        "waiting": _("is waiting for an approval already."),
        "nobody": _("has nobody in the group that approves it."),
        "posted": _("Approval asked of"),
    }
    users = users_field(env)
    return (
        f"LEVELS = {_levels_literal(levels)}\n"
        f"SUMMARY = {words['summary']!r}\n"
        f"NOTE = {words['note']!r}\n"
        "for record in records:\n"
        + _lock()
        + f"    needed = {_needed(profile['amount'])}\n"
        f"    done = record.{LEVEL_FIELD} or 0\n"
        "    if not needed:\n"
        f"        raise UserError(record.display_name + ' ' + {words['not_needed']!r})\n"
        "    if done >= needed:\n"
        f"        raise UserError(record.display_name + ' ' + {words['already']!r})\n"
        f"    if record.{STATE_FIELD} == 'pending':\n"
        f"        raise UserError(record.display_name + ' ' + {words['waiting']!r})\n"
        f"    if not env['res.groups'].browse(LEVELS[done][1])['{users}']:\n"
        f"        raise UserError(record.display_name + ' ' + {words['nobody']!r})\n"
        + _schedule(env, "done", profile)
        + f"    record.with_context(nudge_approval_step={token!r}).write({{'{STATE_FIELD}': 'pending'}})\n"
        f"    record.message_post(body={words['posted']!r} + ' ' + LEVELS[done][2] + ' (' "
        f"+ '{{:,.2f}}'.format(record.{profile['amount']} or 0) + ')')\n")


def approve_code(env, profile, levels, token=""):
    words = {
        "summary": _("needs your approval:"),
        "note": _("Approval asked by"),
        "none": _("was not sent for approval. Press Request approval first."),
        "only": _("Only the group %s approves this step."),
        "approved": _("Approved by"),
        "done": _("Approved"),
        "feedback": _("Approved"),
    }
    groups = groups_field(env)
    return (
        f"LEVELS = {_levels_literal(levels)}\n"
        f"SUMMARY = {words['summary']!r}\n"
        f"NOTE = {words['note']!r}\n"
        "for record in records:\n"
        + _lock()
        + f"    needed = {_needed(profile['amount'])}\n"
        f"    done = record.{LEVEL_FIELD} or 0\n"
        f"    if record.{STATE_FIELD} != 'pending' or done >= needed:\n"
        f"        raise UserError(record.display_name + ' ' + {words['none']!r})\n"
        f"    if LEVELS[done][1] not in env.user['{groups}'].ids:\n"
        f"        raise UserError({words['only']!r} % LEVELS[done][2])\n"
        "    done += 1\n"
        "    last = done >= needed\n"
        f"    record.with_context(nudge_approval_step={token!r}).write({{\n"
        f"        '{LEVEL_FIELD}': done, '{APPROVER_FIELD}': env.user.id,\n"
        f"        '{STATE_FIELD}': 'approved' if last else 'pending'}})\n"
        "    mine = env['mail.activity'].search([\n"
        "        ('res_model', '=', record._name), ('res_id', '=', record.id), ('user_id', '=', env.user.id),\n"
        f"        ('summary', '=like', {APPROVAL_TAG + '%'!r})])\n"
        f"    mine.action_feedback(feedback={words['feedback']!r})\n"
        f"    record.message_post(body={words['approved']!r} + ' ' + env.user.name + ' (' + LEVELS[done - 1][2] + ')')\n"
        "    if not last:\n"
        + _schedule(env, "done", profile, indent="        "))


def refuse_code(env, profile, levels, token=""):
    words = {
        "none": _("was not sent for approval."),
        "only": _("Only the group %s approves this step."),
        "refused": _("Refused by"),
        "feedback": _("Refused"),
    }
    groups = groups_field(env)
    return (
        f"LEVELS = {_levels_literal(levels)}\n"
        "for record in records:\n"
        + _lock()
        + f"    done = record.{LEVEL_FIELD} or 0\n"
        f"    if record.{STATE_FIELD} != 'pending' or done >= len(LEVELS):\n"
        f"        raise UserError(record.display_name + ' ' + {words['none']!r})\n"
        f"    if LEVELS[done][1] not in env.user['{groups}'].ids:\n"
        f"        raise UserError({words['only']!r} % LEVELS[done][2])\n"
        f"    record.with_context(nudge_approval_step={token!r}).write({{\n"
        f"        '{LEVEL_FIELD}': 0, '{APPROVER_FIELD}': env.user.id, '{STATE_FIELD}': 'refused'}})\n"
        "    asked = env['mail.activity'].search([\n"
        "        ('res_model', '=', record._name), ('res_id', '=', record.id), ('user_id', '=', env.user.id),\n"
        f"        ('summary', '=like', {APPROVAL_TAG + '%'!r})])\n"
        f"    asked.action_feedback(feedback={words['feedback']!r})\n"
        f"    record.message_post(body={words['refused']!r} + ' ' + env.user.name + ' (' + LEVELS[done][2] + ')')\n")


# ------------------------------------------------------------------ reading what is asked
def read_request(env, arguments):
    """:return: ``(kind, profile, levels, notes)`` of what the sentence or the arguments ask;
    each level is ``{"amount", "group"}``
    :raise Refused: when it is not clear enough"""
    parsed = None
    if (arguments.get("sentence") or "").strip():
        parsed = approval_sentence.parse(arguments["sentence"])
        if not parsed and not arguments.get("model"):
            raise Refused(
                "The sentence was not read as an approval rule: say what is approved (a purchase order, "
                "a sales order, a vendor bill), from which amount, and who approves, such as “a purchase "
                "order above 50,000 needs the finance manager”. Or give model and levels.")
    kind = arguments.get("kind") or (parsed or {}).get("kind")
    model_name = arguments.get("model") or (parsed or {}).get("model")
    kind = kind or BY_MODEL.get(model_name)
    if kind not in PROFILES or (model_name and PROFILES[kind]["model"] != model_name):
        raise Refused(
            "Approval flows are made for: purchase orders, sales orders, vendor bills and customer invoices. "
            "Other documents: make the fields, the actions and the rule with the builders one by one.")
    profile = PROFILES[kind]
    if profile["model"] not in env:
        raise Refused(f"{profile['model']} is not installed here.")
    given = arguments.get("levels")
    if not given:
        given = [{"amount": level["amount"], "group": GENERAL if level["role"] == "general" else level["role"],
                  "xmlid": level["group"]} for level in (parsed or {}).get("levels", [])]
    if not given:
        raise Refused("Who approves? Give levels: an amount and the group that approves above it.")
    if len(given) > MAX_LEVELS:
        raise Refused(f"At most {MAX_LEVELS} levels.")
    levels, notes = [], []
    for level in sorted(given, key=lambda item: float(item.get("amount") or 0)):
        group, note = find_group(env, level.get("group") or "", level.get("xmlid"))
        if note:
            notes.append(note)
        levels.append({"amount": float(level.get("amount") or 0), "group": group})
    return kind, profile, levels, notes


def studio_here(env):
    return "studio.approval.rule" in env and "prepare_approval_rule" in base.TOOLS


def _field_builds(env, model, profile):
    fields_ = [
        (STATE_FIELD, {"model": model._name, "label": _("Approval"), "type": "selection", "name": STATE_FIELD,
                       "selection": [{"value": "pending", "label": _("Waiting")},
                                     {"value": "approved", "label": _("Approved")},
                                     {"value": "refused", "label": _("Refused")}],
                       "readonly": True, "copy": False}),
        (APPROVER_FIELD, {"model": model._name, "label": _("Approved by"), "type": "many2one", "name": APPROVER_FIELD,
                          "relation": "res.users", "readonly": True, "copy": False}),
        (LEVEL_FIELD, {"model": model._name, "label": _("Approval level"), "type": "integer", "name": LEVEL_FIELD,
                       "readonly": True, "copy": False}),
    ]
    return [(name, arguments) for name, arguments in fields_ if name not in model._fields]


# ------------------------------------------------------------------ the tool
@base.register
class PrepareApprovalFlow(Building):
    name = "prepare_approval_flow"
    kind = "create"
    description = (
        "Make an approval flow for a kind of document from a sentence, on Community (Studio's approval "
        "rules are used instead when they are installed): before a purchase order, a sales order, a vendor "
        "bill or a customer invoice above an amount is confirmed or posted, the group named must approve; "
        "several people in the order said are several levels (“then the CEO above 200,000”). Give the "
        "person's sentence as sentence, or model (and kind for account.move: vendor_bill or "
        "customer_invoice) with levels: an amount and the group, by name or external id. It makes the "
        "fields that hold the approval, an automation rule that stops the confirmation with a clear message, "
        "the actions Request approval, Approve and Refuse as buttons on the form (Approve and Refuse only for "
        "the approvers), an activity for the approvers and a note in the chatter at each step, all with the "
        "builders of Developer mode. Nothing is made by this call: the person is shown the whole flow in "
        "words and says yes or no; it is tried on a dummy record after, and undone as one set with "
        "prepare_undo."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "sentence": {"type": "string", "maxLength": 600, "description": "The rule as the person said it."},
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "kind": {"type": "string", "enum": ["vendor_bill", "customer_invoice", "purchase_order", "sales_order"]},
            "levels": {
                "type": "array", "minItems": 1, "maxItems": MAX_LEVELS,
                "items": {
                    "type": "object", "additionalProperties": False, "required": ["amount", "group"],
                    "properties": {
                        "amount": {"type": "number", "minimum": 0, "description": "Above this amount the group approves."},
                        "group": {"type": "string", "maxLength": 120, "description": "A group, by name or external id."},
                    },
                },
            },
        },
    }

    # ---- what is made, in order, as the builders take it
    def _plan(self, env, model, kind, profile, levels, token=""):
        """:return: ``[{"tool", "arguments", "what"}]``; the buttons are completed after the actions exist"""
        if studio_here(env):
            builds = []
            for step, level in enumerate(levels, start=1):
                builds.append({
                    "tool": "prepare_approval_rule",
                    "arguments": {
                        "model": model._name, "method": profile["button"], "group": level["group"].name, "step": step,
                        "conditions": profile["conditions"] + [
                            {"field": profile["amount"], "operator": ">", "value": str(level["amount"])}],
                        "match": "all",
                        "message": _("Approval of %(group)s above %(amount)s", group=level["group"].display_name,
                                         amount=money(level["amount"]))},
                    "what": _("Studio approval rule, step %(step)s: %(group)s above %(amount)s",
                                  step=step, group=level["group"].display_name, amount=money(level["amount"]))})
            return builds
        builds = [{"tool": "prepare_field", "arguments": arguments, "what": _("Field “%s”", arguments["label"])}
                  for _name, arguments in _field_builds(env, model, profile)]
        label = doc_label(env, kind)
        actions = (
            ("request", _("%(prefix)s: request approval of a %(doc)s", prefix=NAME_PREFIX, doc=label),
             request_code(env, profile, levels, token)),
            ("approve", _("%(prefix)s: approve a %(doc)s", prefix=NAME_PREFIX, doc=label),
             approve_code(env, profile, levels, token)),
            ("refuse", _("%(prefix)s: refuse a %(doc)s", prefix=NAME_PREFIX, doc=label),
             refuse_code(env, profile, levels, token)),
        )
        for key, name, code in actions:
            builds.append({
                "tool": "prepare_server_action", "key": key,
                "arguments": {"name": name, "model": model._name, "action": {"kind": "code", "name": name, "code": code}},
                "what": name})
        rule_name = _("%(prefix)s: no %(doc)s above %(amount)s is %(done)s before it is approved",
                          prefix=NAME_PREFIX, doc=label, amount=money(levels[0]["amount"]), done=verb_label(env, kind))
        builds.append({
            "tool": "prepare_automation",
            "arguments": {
                "name": rule_name, "model": model._name, "trigger": "on_create_or_write",
                "fields": ["state"],
                "conditions": profile["conditions"] + [
                    {"field": "state", "operator": "in", "value": ",".join(profile["states"])}],
                "match": "all",
                "actions": [{"kind": "code", "name": rule_name, "code": block_code(env, profile, levels, kind)}]},
            "what": rule_name, "key": "rule"})
        guard_name = _("%(prefix)s: the approval is written by the buttons only", prefix=NAME_PREFIX)
        builds.append({
            "tool": "prepare_automation",
            "arguments": {
                "name": guard_name, "model": model._name, "trigger": "on_create_or_write",
                "fields": [STATE_FIELD, LEVEL_FIELD, APPROVER_FIELD],
                "match": "all",
                "actions": [{"kind": "code", "name": guard_name, "code": guard_code(env, token)}]},
            "what": guard_name, "key": "guard"})
        groups = [self._xmlid(level["group"]) for level in levels]
        restricted = [xmlid for xmlid in groups if xmlid]
        only_approvers = {"groups": sorted(set(restricted))} if restricted and len(restricted) == len(groups) else {}
        targets = ",".join(repr(state) for state in profile["states"])
        for key, text, invisible, extra in (
            ("request", _("Request approval"), f"{STATE_FIELD} == 'pending' or state in ({targets})", {}),
            ("approve", _("Approve"), f"{STATE_FIELD} != 'pending'", only_approvers),
            ("refuse", _("Refuse"), f"{STATE_FIELD} != 'pending'", only_approvers),
        ):
            builds.append({
                "tool": "prepare_button", "key": f"button_{key}",
                "arguments": dict({"model": model._name, "label": text, "invisible": invisible, "action_id": 0},
                                  primary=(key == "approve"), **extra),
                "what": _("Button “%s”", text), "action": key})
        return builds

    @staticmethod
    def _xmlid(group):
        return group.get_external_id().get(group.id) or ""

    def _words(self, env, model, kind, profile, levels, notes):
        label, done = doc_label(env, kind), verb_label(env, kind)
        lines = []
        if studio_here(env):
            lines.append({"label": _("How"), "value": _(
                "Studio’s approval rules are installed here: Nudge makes one Studio approval rule for each level "
                "on the button %(button)s, in place of the fields and the rule of Community.", button=profile["button"])})
        lines.append({"label": _("Model"), "value": model_label(model)})
        for number, level in enumerate(levels, start=1):
            above = _("above %s", money(level["amount"])) if level["amount"] else _("any amount")
            lines.append({"label": _("Level %s", number), "value": _(
                "A %(doc)s %(above)s needs the approval of %(group)s before it is %(done)s.",
                doc=label, above=above, group=level["group"].display_name, done=done)})
        if not studio_here(env):
            fields_ = [name for name, _arguments in _field_builds(env, model, profile)]
            if kind in ("purchase_order", "sales_order"):
                stops = _("Confirming without the approval is refused with a clear message, on the form, in a list and in an import.")
            else:
                stops = _("Posting without the approval is refused with a clear message.")
            lines += [
                {"label": _("Stops"), "value": stops},
                {"label": _("Asks"), "value": _(
                    "Request approval puts an activity, with the amount, on each user of the group of the level, "
                    "and a note in the chatter. Approve and Refuse close it and write a note too.")},
                {"label": _("Buttons"), "value": _(
                    "Request approval (for everyone who edits the %(doc)s), Approve and Refuse (shown to the "
                    "approvers only).", doc=label)},
                {"label": _("Fields added"), "value": ", ".join(fields_) or _("none: they are there already")},
                {"label": _("Note"), "value": _(
                    "Everything is written as the person who presses the button. Odoo lets a user press "
                    "Approve only when they may edit a %(doc)s: the approvers need that right, and the "
                    "action itself checks that they are in the group of the level.", doc=label)},
                {"label": _("After the yes"), "value": _(
                    "It is tried on a dummy record, and undone with everything it made in one go (Undo).")},
            ]
        for note in notes:
            lines.append({"label": _("Careful"), "value": note})
        return lines

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        kind, profile, levels, notes = read_request(env, arguments)
        model = get_model(env, profile["model"])
        if "activity_ids" not in model._fields or not hasattr(model, "message_post"):
            raise Refused(f"{model._name} has no chatter, where the approval is written and asked.")
        if not studio_here(env):
            automations_installed(env)
            if "mail.activity" not in env:
                raise Refused("The app Discuss (mail) is needed to ask the approvers.")
            existing = env["base.automation"].with_context(active_test=False).search_count([
                ("model_id", "=", ir_model(env, model).id), ("name", "=like", f"{NAME_PREFIX}:%")])
            if existing:
                raise Refused(
                    f"{model._name} has an approval flow made by Nudge already. Undo it first (prepare_undo), "
                    "then make the new one.")
        builds = self._plan(env, model, kind, profile, levels)
        return base.ToolResult(
            "The person is being shown the approval flow, in words.",
            pending={
                "summary": _("Create: approval flow for %(model)s", model=model_label(model)),
                "res_model": "studio.approval.rule" if studio_here(env) else "base.automation",
                "res_ids": [],
                "record_names": ", ".join(build["what"] for build in builds)[:500],
                "values": {"kind": kind, "model": model._name, "token": secrets.token_hex(12), "levels": [
                    {"amount": level["amount"], "group": level["group"].id} for level in levels]},
                "is_sensitive": True,
                "preview": {"records": [{"id": index, "name": build["what"]} for index, build in enumerate(builds, start=1)],
                            "lines": self._words(env, model, kind, profile, levels, notes)},
            },
        )

    # ---- after the yes
    def execute(self, env, step):
        check_developing(env)
        values = step.values or {}
        try:
            with env.cr.savepoint():
                return self._make(env, step, values)
        except Refused as refusal:
            return base.ToolResult(str(refusal), is_error=True)

    def _make(self, env, step, values):
        kind = values["kind"]
        profile = PROFILES[kind]
        model = get_model(env, profile["model"])
        groups = env["res.groups"].browse([level["group"] for level in values["levels"]]).exists()
        if len(groups) != len({level["group"] for level in values["levels"]}):
            raise Refused("A group of the flow was deleted since: ask for the flow again.")
        levels = [{"amount": level["amount"], "group": env["res.groups"].browse(level["group"])}
                  for level in values["levels"]]
        builds = self._plan(env, model, kind, profile, levels, values.get("token") or secrets.token_hex(12))
        run = getattr(step, "run_id", None)
        made = {}
        main = None
        for number, build in enumerate(builds, start=1):
            arguments = build["arguments"]
            if build["tool"] == "prepare_button":
                arguments = dict(arguments, action_id=made[build["action"]])
            result = base.call(env, build["tool"], arguments, None, base.MODE_DEV)
            if result.is_error or not result.pending:
                raise Refused(f"{build['what']}: {result.content}")
            tool = base.TOOLS[build["tool"]]
            done = tool.execute(env, SimpleNamespace(values=result.pending["values"], res_model=result.pending["res_model"],
                                                     res_ids=[], run_id=run))
            if done.is_error:
                raise Refused(f"{build['what']}: {done.content}")
            record_model, ids, names = done.records
            if build.get("key") in ("request", "approve", "refuse"):
                made[build["key"]] = ids[0]
            if build.get("key") == "rule":
                main = (record_model, ids, names)
            elif run:
                env["nudge.step"].sudo().create({
                    "run_id": run.id, "tool": build["tool"], "kind": "create", "state": "done",
                    "summary": result.pending["summary"], "res_model": record_model, "res_ids": ids,
                    "record_names": ", ".join(map(str, names))[:500], "call_id": f"{step.call_id or 'flow'}_{number}"
                    if getattr(step, "call_id", None) else f"flow_{number}"})
        told = [f"Made the approval flow for {model._name}: " + "; ".join(build["what"] for build in builds) + "."]
        lines = [{"label": _("Made"), "value": ", ".join(build["what"] for build in builds)}]
        if main:
            tried = self._try(env, profile, main[1][0], levels, model)
            told.append(tried)
            lines.append({"label": _("Tried on a dummy record"), "value": tried[:300]})
        return base.ToolResult(
            " ".join(told),
            event={"type": "changed", "model": model._name, "ids": [], "action": {"type": "ir.actions.client", "tag": "reload"}},
            records=main or ("studio.approval.rule", [], []),
            card_lines=lines,
        )

    def _try(self, env, profile, rule_id, levels, model):
        """Try the rule on a dummy record above the first amount: it has to refuse."""
        amount = levels[0]["amount"] + 1
        entries = []
        if "partner_id" in model._fields:
            entries.append({"field": "partner_id", "value": str(env.user.partner_id.id)})
        if profile["conditions"]:
            entries.append({"field": "move_type", "value": profile["conditions"][0]["value"].split(",")[0]})
        field, quantity, price = profile["lines"]
        values = [{"field": "name", "value": _("Nudge trial")}, {"field": quantity, "value": "1"},
                  {"field": price, "value": str(amount)}]
        line_model = env.get(model._fields[field].comodel_name) if field in model._fields else None
        if line_model is not None and "product_id" in line_model._fields:
            # some versions refuse a line without a product (a CHECK on the table): one that exists is used
            product = env["product.product"].search([], limit=1) if "product.product" in env else None
            if product:
                values.append({"field": "product_id", "value": str(product.id)})
        lines = [{"field": field, "values": values}]
        change = [{"field": "state", "value": profile["states"][-1]}]
        try:
            report = base.TOOLS["try_automation"]._trial(env, "rule_id", {"rule_id": rule_id, "record": entries, "change": change, "lines": lines})
        except Refused as refusal:
            return _("Could not be tried: %s", str(refusal))
        text = report.content
        if "It failed" in text and "approval" in text.lower():
            return _("Blocked, as it should be: a %(doc)s of %(amount)s cannot be confirmed before it is approved.",
                         doc=model._description, amount=money(amount))
        return _("Tried on a dummy record, and it was not blocked: look at the rule. %s", text[:300])
