"""The server actions the company marked "Use in AI", at the agent's hand.

The AI app lets an administrator mark a server action as a tool for its
agents, with a description and a schema of its arguments. Nudge's agent
sees the same tools: it lists them, and runs one only once the person has
seen which, with which arguments, and said yes. The action runs as the
person, as the AI app runs it.
"""
import json

from odoo.addons.ai.utils.tools_schema.validators import validate_params_llm_values_with_schema
from odoo.addons.nudge.tools import base
from odoo.addons.nudge.tools.records import Refused, cut, refusing

MAX_ACTIONS = 30
# Under the size of a tool result, with room for the note
MAX_LIST_CHARS = 5000
MAX_RESULT_CHARS = 1500


def _name_of(action, xml_ids):
    """The name the AI app gives a tool: its external id, or action_<id>."""
    xml_id = xml_ids.get(action.id)
    return xml_id.split(".")[1] if xml_id else f"action_{action.id}"


def _allowed(env, actions):
    """:return: the actions this person may use: marked Use in AI, of code,
    and with no group or one of theirs"""
    groups = getattr(env.user, "all_group_ids", env.user.group_ids)
    return actions.filtered(lambda action: (
        action.use_in_ai and action.state == "code"
        and (not action.group_ids or action.group_ids & groups)))


def find_ai_action(env, name):
    """:return: the action named ``name`` as list_ai_actions names it, if
    this person may use it; an empty recordset otherwise"""
    Action = env["ir.actions.server"].sudo()
    action = Action.browse()
    if name.startswith("action_") and name[len("action_"):].isdigit():
        action = Action.browse(int(name[len("action_"):])).exists()
    else:
        found = env["ir.model.data"].sudo().search(
            [("model", "=", "ir.actions.server"), ("name", "=", name)], limit=1)
        if found:
            action = Action.browse(found.res_id).exists()
    return _allowed(env, action)


def ai_actions(env, words=None):
    """:return: {name: action} of the server actions this person may use as tools,
    those whose name or description holds ``words`` when given

    Server actions are readable by system administrators only: their
    definitions are read in sudo, as the AI app reads them for its agents,
    and kept to those whose groups include this person. Only actions of
    code are offered: the AI app runs them with an environment without
    sudo (``env(su=False)``), so the code acts as the person. Actions that
    write values through the ORM directly would run as the superuser.
    """
    domain = [("use_in_ai", "=", True), ("state", "=", "code")]
    for word in (words or "").split():
        domain += ["|", ("name", "ilike", word), ("ai_tool_description", "ilike", word)]
    actions = env["ir.actions.server"].sudo().search(domain, limit=MAX_ACTIONS)
    allowed = _allowed(env, actions)
    xml_ids = allowed.get_external_id()
    return {_name_of(action, xml_ids): action for action in allowed}


def _schema(action):
    try:
        return json.loads(action.ai_tool_schema) if action.ai_tool_schema else {}
    except ValueError:
        return {}


@base.register
class ListAiActions(base.Tool):
    name = "list_ai_actions"
    group = "nudge.group_nudge_agent"
    modes = (base.MODE_AGENT,)
    description = (
        "The server actions the company set up as AI tools (marked Use in AI in the AI app): "
        "their names, what they do, the kind of record they work on and the arguments they take. "
        "Give words to find the ones about a subject. Run one with run_ai_action."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "words": {
                "type": "string",
                "maxLength": 80,
                "description": "Words of the name or the description of the actions looked for.",
            },
        },
    }

    def run(self, env, arguments, screen):
        actions = ai_actions(env, arguments.get("words"))
        if not actions:
            return base.ToolResult("No server action set up as an AI tool matches, for this person.")
        listed, size = [], 0
        for name, action in actions.items():
            schema = _schema(action)
            entry = {
                "name": name,
                "description": cut(action.ai_tool_description or action.name, 200),
                "model": action.model_id.model,
                # Each argument by its type and what it is for, briefly
                "arguments": {
                    key: cut(f"{spec.get('type', '')}: {spec.get('description', '')}".strip(": "), 120)
                    for key, spec in (schema.get("properties") or {}).items()
                    if isinstance(spec, dict)
                },
                "required": schema.get("required", []),
            }
            size += len(base.dump(entry))
            # A result that is cut is not JSON any more: stop before the limit
            if size > MAX_LIST_CHARS:
                break
            listed.append(entry)
        result = {"actions": listed}
        if len(listed) < len(actions):
            result["note"] = (
                f"{len(listed)} of {len(actions)} actions are listed. Give words to find the others.")
        return base.ToolResult(base.dump(result))


@base.register
class RunAiAction(base.Action):
    name = "run_ai_action"
    kind = "method"
    description = (
        "Run a server action the company set up as an AI tool, by its name from "
        "list_ai_actions, with its arguments. The person is shown which action and which "
        "arguments, and says yes or no. When the action works on the kind of record on the "
        "person's screen, it runs on that record."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "minLength": 1, "maxLength": 120},
            "arguments": {
                "type": "object",
                "description": "The arguments of the action, as list_ai_actions describes them.",
            },
        },
    }

    @staticmethod
    def _find(env, name):
        action = find_ai_action(env, name)
        if not action:
            raise Refused(f"There is no AI tool named {name!r} for this person. list_ai_actions names them.")
        return action

    @staticmethod
    def _record_of(env, action, screen):
        """:return: the record of the screen when it is of the action's model, else the empty model"""
        model = env[action.model_id.model]
        record_id = ((screen or {}).get("record") or {}).get("id")
        if (screen or {}).get("model") == model._name and isinstance(record_id, int):
            record = model.browse(record_id).exists()
            if record and record.has_access("read"):
                return record
        return model

    @refusing
    def run(self, env, arguments, screen):
        action = self._find(env, arguments["action"])
        values = arguments.get("arguments") or {}
        schema = _schema(action)
        if schema:
            try:
                values = validate_params_llm_values_with_schema(
                    values, schema.get("properties", {}), schema.get("required", []), env)
            except Exception as error:  # noqa: BLE001
                raise Refused(f"The arguments do not fit the action: {cut(str(error), 300)}") from None
        record = self._record_of(env, action, screen)
        records = record if record.ids else record.browse()
        return base.ToolResult(
            "The person is being shown which action is about to run.",
            pending={
                "summary": env._("Run the AI action “%(action)s”", action=action.name),
                "res_model": action.model_id.model,
                "res_ids": records.ids,
                "record_names": ", ".join(records.mapped("display_name")),
                "method": self.name,
                "is_sensitive": True,
                "preview": {
                    "records": [{"id": item.id, "name": item.display_name} for item in records],
                    "lines": [{"label": key, "value": value} for key, value in values.items()],
                },
            },
        )

    def execute(self, env, step):
        name = (step.arguments or {}).get("action")
        action = self._find(env, name)
        values = (step.arguments or {}).get("arguments") or {}
        model = env[action.model_id.model]
        record = model.browse(step.res_ids or []).exists()
        context = {
            "active_model": model._name,
            "active_id": record.id if len(record) == 1 else False,
            "active_ids": record.ids,
        } if record else {}
        result = action.with_context(**context)._ai_tool_run(record or model, values)
        told = f"Ran the AI action {action.name!r}."
        if result is not None:
            told += f" Result: {cut(str(result), MAX_RESULT_CHARS)}"
        event = {"type": "changed", "model": model._name, "ids": record.ids} if record else None
        return base.ToolResult(
            told, event=event,
            records=(model._name, record.ids, record.mapped("display_name")) if record else None)
