"""Approval rules on the buttons of a form, the way Studio makes them.

A rule says who approves before a button does its work: some users, or a
group, in a step, exclusive or not, under conditions. Nudge reads the rules
of a model, makes one after a yes, and the person switches it off or deletes
it like anything else it made.
"""
from lxml import etree

from odoo import Command

from odoo.addons.nudge.tools import base
from odoo.addons.nudge.tools.developer import DELETABLE, Building, check_developing, in_words, ir_model
from odoo.addons.nudge.tools.records import (
    CONDITION_SCHEMA_ITEMS, MODEL_PATTERN, Refused, build_domain, cut, get_model, get_records, model_label,
    reference_names, refusing,
)

RULE = "studio.approval.rule"
MAX_LISTED = 25
MAX_USERS = 10
MAX_CONDITIONS = 8

# What Nudge makes here, it may delete when asked
if RULE not in DELETABLE:
    DELETABLE.append(RULE)


def form_buttons(env, model):
    """:return: the buttons of the form of ``model``, as this person has it:
    ``[{"label", "method", "action_id"}]``"""
    arch = model.get_view(False, "form")["arch"]
    buttons = []
    for node in etree.fromstring(arch.encode("utf-8")).iter("button"):
        kind, name = node.get("type"), node.get("name")
        if not name or kind not in ("object", "action"):
            continue
        label = node.get("string") or (node.text or "").strip() or name
        action_id = None
        if kind == "action":
            try:
                action_id = int(name)
            except ValueError:
                action_id = env["ir.model.data"].sudo()._xmlid_to_res_id(name, raise_if_not_found=False) or None
        buttons.append({"label": label.strip(), "method": name if kind == "object" else None, "action_id": action_id})
    return buttons


def find_users(env, names):
    """:return: the users named by their name or login
    :raise Refused: when one is not found, or found twice"""
    found = env["res.users"]
    for text in names:
        text = text.strip()
        users = env["res.users"].search(["|", ("login", "=ilike", text), ("name", "=ilike", text)], limit=2)
        if not users:
            users = env["res.users"].search([("name", "ilike", text)], limit=2)
        if len(users) != 1:
            raise Refused(f"{'No user' if not users else 'More than one user'} is named {text!r}. Give the exact name or the login.")
        found |= users
    return found


def button_of(rule):
    """:return: the button a rule is on, in words. What it points at may be
    gone: an action deleted since (rule #56 on purchase.order pointed at
    action #3375), a method its model no longer has."""
    if rule.method:
        model = rule.env[rule.model_name] if rule.model_name in rule.env else None
        if model is None or not callable(getattr(type(model), rule.method, None)):
            return f"{rule.method}, a method {rule.model_name} no longer has"
        return rule.method
    if rule.action_id:
        name = reference_names(rule.action_id, "")[0]
        return (f"action #{rule.action_id.id} {name!r}" if name
                else f"action #{rule.action_id.id}, a button action that no longer exists")
    return "?"


def describe_rule(rule):
    who = []
    if rule.approver_ids:
        who.append("approvers " + ", ".join(reference_names(rule.approver_ids, "a user deleted since")))
    if rule.approval_group_id:
        group = reference_names(rule.approval_group_id, "")[0]
        who.append(f"group {group}" if group else "a group that no longer exists")
    line = (f"- #{rule.id} on {rule.model_name}, button {button_of(rule)}: {' and '.join(who) or 'nobody yet'}, "
            f"step {rule.notification_order or '1'}")
    if rule.exclusive_user:
        line += ", exclusive"
    if rule.domain and rule.domain.strip() not in ("", "[]"):
        line += f", only when {rule.domain}"
    if rule.users_to_notify:
        line += ", notifies " + ", ".join(reference_names(rule.users_to_notify, "a user deleted since"))
    if rule.message:
        line += f" — {cut(rule.message, 120)!r}"
    if not rule.active:
        line += " (off)"
    return line


def describe_rules(rules):
    """:return: a line for each rule; one that cannot be read is said so, and
    never keeps the others from being listed"""
    lines = []
    for rule in rules:
        try:
            lines.append(describe_rule(rule))
        except Exception as error:  # noqa: BLE001
            lines.append(f"- #{rule.id}: cannot be read ({cut(' '.join(str(error).split()), 160)})")
    return lines


@base.register
class ReadApprovalRules(base.Tool):
    name = "read_approval_rules"
    modes = (base.MODE_DEV,)
    group = "base.group_system"
    summary = "Read the approval rules"
    description = (
        "The approval rules of a model, as Studio holds them: on which button, who approves "
        "(users or a group), in which step, exclusive or not, under which conditions, who is "
        "notified; and the buttons of its form a rule can go on. Read it before adding a rule."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["model"],
        "properties": {"model": {"type": "string", "pattern": MODEL_PATTERN}},
    }

    def is_available(self, env, mode=base.MODE_TALK):
        return super().is_available(env, mode) and env["nudge.conversation"]._can_develop()

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        model = get_model(env, arguments["model"])
        rules = env[RULE].with_context(active_test=False).search([("model_name", "=", model._name)], limit=MAX_LISTED)
        lines = [f"Approval rules on {model._name}: {len(rules)}"]
        lines += describe_rules(rules)
        buttons = form_buttons(env, model)
        lines.append("Buttons of the form a rule can go on: " + ("; ".join(
            f"{button['label']} ({'method ' + button['method'] if button['method'] else 'action #' + str(button['action_id'])})"
            for button in buttons) if buttons else "none"))
        return base.ToolResult("\n".join(lines))


@base.register
class PrepareApprovalRule(Building):
    name = "prepare_approval_rule"
    kind = "create"
    description = (
        "Make an approval rule on a button of a form, the way Studio does: before the button "
        "does its work, the users named or a user of the group must approve; several rules on "
        "one button are steps, in order; an exclusive rule keeps the one who approved it from "
        "approving another step. Give the button by its method (from read_approval_rules), or "
        "its action_id for a button that runs an action. Nothing is made by this call: the "
        "person is shown the rule in full and says yes or no; you read their answer as the result."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["model"],
        "properties": {
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "method": {"type": "string", "pattern": r"[a-z_][a-z0-9_]*", "description": "The button's method."},
            "action_id": {"type": "integer", "minimum": 1, "description": "For a button that runs an action."},
            "approvers": {
                "type": "array", "maxItems": MAX_USERS,
                "items": {"type": "string", "minLength": 1, "maxLength": 80},
                "description": "Users, by name or login.",
            },
            "group": {"type": "string", "maxLength": 120, "description": "A group whose users may approve, by name."},
            "step": {"type": "integer", "minimum": 1, "maximum": 9, "description": "The order among the rules of the button. Default: 1."},
            "exclusive": {"type": "boolean"},
            "notify": {"type": "array", "maxItems": MAX_USERS, "items": {"type": "string", "minLength": 1, "maxLength": 80}},
            "message": {"type": "string", "maxLength": 300, "description": "Shown on the button, what the approval is for."},
            "conditions": {
                "type": "array", "maxItems": MAX_CONDITIONS, "items": CONDITION_SCHEMA_ITEMS,
                "description": "The rule holds only for records that match.",
            },
            "match": {"type": "string", "enum": ["all", "any"]},
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        model = get_model(env, arguments["model"])
        buttons = form_buttons(env, model)
        method, action_id = arguments.get("method"), arguments.get("action_id")
        if bool(method) == bool(action_id):
            raise Refused("Give the button by its method, or by its action_id, not both.")
        button = next((b for b in buttons if (method and b["method"] == method) or (action_id and b["action_id"] == action_id)), None)
        if not button:
            listed = "; ".join(f"{b['label']} ({'method ' + b['method'] if b['method'] else 'action #' + str(b['action_id'])})" for b in buttons)
            raise Refused(f"The form of {model._name} has no such button. Its buttons: {listed or 'none'}.")
        approvers = find_users(env, arguments.get("approvers") or [])
        group = env["res.groups"]
        if (arguments.get("group") or "").strip():
            text = arguments["group"].strip()
            group = env["res.groups"].search([("name", "=ilike", text)], limit=2) or env["res.groups"].search([("name", "ilike", text)], limit=2)
            if len(group) != 1:
                raise Refused(f"{'No group' if not group else 'More than one group'} is named {text!r}.")
        if not approvers and not group:
            raise Refused("A rule names who approves: approvers, a group, or both.")
        notify = find_users(env, arguments.get("notify") or [])
        values = {
            "model_id": ir_model(env, model).id,
            "method": method or False,
            "action_id": action_id or False,
            "approver_ids": approvers.ids,
            "approval_group_id": group.id or False,
            "notification_order": str(arguments.get("step") or 1),
            "exclusive_user": bool(arguments.get("exclusive")),
            "users_to_notify": notify.ids,
            "message": (arguments.get("message") or "").strip() or False,
            "name": (arguments.get("message") or "").strip() or f"Approval: {button['label']}",
        }
        lines = [
            {"label": env._("Model"), "value": model_label(model)},
            {"label": env._("Button"), "value": button["label"]},
            {"label": env._("Approvers"), "value": ", ".join(approvers.mapped("name")) or None},
            {"label": env._("Approval group"), "value": group.display_name if group else None},
            {"label": env._("Step"), "value": values["notification_order"]},
        ]
        if values["exclusive_user"]:
            lines.append({"label": env._("Exclusive"), "value": True})
        if notify:
            lines.append({"label": env._("Notifies"), "value": ", ".join(notify.mapped("name"))})
        if arguments.get("conditions"):
            domain = build_domain(model, arguments["conditions"], arguments.get("match", "all"))
            values.update(conditional=True, domain=str(list(domain)))
            lines.append({"label": env._("Only for"), "value": in_words(model, arguments["conditions"], arguments.get("match", "all"))})
        if values["message"]:
            lines.append({"label": env._("Shown on the button"), "value": values["message"]})
        return base.ToolResult(
            "The person is being shown the approval rule, in full.",
            pending={
                "summary": env._("Create: approval rule on “%s”", button["label"]),
                "res_model": RULE,
                "res_ids": [],
                "record_names": values["name"],
                "values": {"rule": values},
                "is_sensitive": False,
                "preview": {"records": [], "lines": lines},
            },
        )

    def execute(self, env, step):
        check_developing(env)
        values = dict((step.values or {}).get("rule") or {})
        if not values.get("model_id") or not (values.get("method") or values.get("action_id")):
            return base.ToolResult("This rule cannot be made.", is_error=True)
        # Shown yesterday, applied today: what was named may be gone since
        if not env["ir.model"].browse(values["model_id"]).exists():
            return base.ToolResult(env._(
                "The model this rule was prepared for does not exist any more. Nothing was made. "
                "Ask again."), is_error=True)
        approvers = env["res.users"].browse(values.get("approver_ids") or []).exists()
        group = env["res.groups"].browse(values.get("approval_group_id") or []).exists()
        if not approvers and not group:
            return base.ToolResult(env._(
                "Every approver this rule named has been deleted since. Nothing was made. "
                "Ask again and name who approves."), is_error=True)
        values["approval_group_id"] = group.id or False
        values["approver_ids"] = [Command.set(approvers.ids)]
        values["users_to_notify"] = [Command.set(
            env["res.users"].browse(values.get("users_to_notify") or []).exists().ids)]
        rule = env[RULE].create(values)
        return base.ToolResult(
            f"Made: approval rule #{rule.id} on {rule.model_name}, button {button_of(rule)}. "
            "It shows on the button in Studio ‣ Properties, and the approvals asked for in the chatter of each record.",
            event={"type": "changed", "model": RULE, "ids": rule.ids, "action": {"type": "ir.actions.client", "tag": "reload"}},
            records=(RULE, rule.ids, [rule.display_name]),
        )
