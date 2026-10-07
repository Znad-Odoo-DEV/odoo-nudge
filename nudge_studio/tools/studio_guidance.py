"""Pointing inside Studio's editor.

The model says what to point at by its name: a tab of the sidebar, a type of
field to drag, an entry of Studio's top menu, a control. The browser finds the
element, as it does on any screen; what is not found is told.
"""
from odoo.addons.nudge.tools import base

TABS = ("new", "view", "properties")
COMPONENTS = (
    "char", "text", "integer", "float", "html", "monetary", "date", "datetime", "boolean",
    "selection", "file", "lines", "one2many", "many2one", "many2many", "picture", "tags",
    "priority", "signature", "related", "ai",
)
MENUS = ("views", "reports", "automations", "actions", "webhooks", "access_control", "model_pages", "filter_rules")
CONTROLS = ("add_button", "leave", "undo", "redo", "new_model", "edit_menu")
TARGETS = {"studio_tab": TABS, "studio_component": COMPONENTS, "studio_menu": MENUS, "studio_control": CONTROLS}
MAX_STEPS = 8


@base.register
class ShowInStudio(base.Tool):
    name = "show_in_studio"
    terminal = True
    summary = "Pointed inside Studio"
    description = (
        "Give the steps to do something inside Studio's editor, shown on the screen by Nudge's "
        "pointer step by step as the person clicks, or in writing: a tab of the sidebar "
        "(studio_tab: new, view, properties), a type of field to drag in (studio_component: "
        + ", ".join(COMPONENTS) + "), an entry of Studio's top menu (studio_menu: "
        + ", ".join(MENUS) + "), or a control (studio_control: add_button, leave, undo, redo, "
        "new_model, edit_menu). The fields and buttons of the form being edited are pointed at "
        "with show_on_screen's field and button targets, from the snapshot. Use it when the "
        "person is in Studio (the snapshot says so) and asks how to do something there. Each "
        "text says what to do and why, in the language they wrote in."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["steps"],
        "properties": {
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_STEPS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["target_type", "target_name", "text"],
                    "properties": {
                        "target_type": {"type": "string", "enum": list(TARGETS)},
                        "target_name": {"type": "string", "pattern": r"[a-z0-9_]{1,40}"},
                        "text": {"type": "string", "minLength": 1, "maxLength": 400},
                        "advance": {"type": "string", "enum": ["auto", "manual"]},
                    },
                },
            },
        },
    }

    def run(self, env, arguments, screen):
        steps = []
        for position, step in enumerate(arguments["steps"], start=1):
            kind, name = step["target_type"], step["target_name"]
            if name not in TARGETS[kind]:
                return base.ToolResult(
                    f"Step {position}: {kind} has no {name!r}. Known: {', '.join(TARGETS[kind])}.",
                    is_error=True)
            steps.append({
                "target": {"type": kind, "name": name},
                "text": step["text"].strip(),
                "advance": step.get("advance", "auto"),
            })
        return base.ToolResult(
            f"{len(steps)} step(s) are given to the person, to read or to be shown inside Studio.",
            event={"type": "guidance", "steps": steps},
        )
