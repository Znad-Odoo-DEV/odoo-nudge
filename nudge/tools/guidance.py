"""Pointing at the screen.

The model says *what* to point at: a field, a button, a tab, by the name
found in the screen snapshot. It never says *where*: the browser finds the
element. A model has no means to make the browser look for an arbitrary
selector.
"""
from . import base

TARGET_TYPES = ("field", "button", "tab", "control")
CONTROLS = ("new", "save", "discard", "search", "filters", "pager_next", "pager_previous", "actions")
ADVANCE = ("auto", "manual")

MAX_STEPS = 8
NAME_PATTERN = r"[A-Za-z0-9_.%()\- ]{1,120}"


@base.register
class ShowOnScreen(base.Tool):
    name = "show_on_screen"
    terminal = True
    description = (
        "Give the steps to do something on the screen the person is on. They choose how to get "
        "them: in writing, in the conversation, or shown on the screen, where Nudge's pointer "
        "goes to each element with its text, and the next step comes when they have done the "
        "current one. Use it whenever the answer is about where something is or how to do "
        "something on this screen: steps beat prose. Only elements listed in the "
        "screen snapshot can be pointed at; use their \"name\", not their label. An element with "
        "\"on_screen\": false can be pointed at too: the tab that hides it is opened first. "
        "The steps are all the person sees: write in each text what to do and why, in one or two "
        "sentences, in the language of their message, which may differ from the language of the "
        "labels on screen."
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
                        "target_type": {
                            "type": "string",
                            "enum": list(TARGET_TYPES),
                            "description": (
                                "field, button and tab are elements of the snapshot. control is a "
                                "standard control of the view: " + ", ".join(CONTROLS) + "."
                            ),
                        },
                        "target_name": {
                            "type": "string",
                            "pattern": NAME_PATTERN,
                            "description": "The \"name\" of the element in the snapshot, or a control.",
                        },
                        "text": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 400,
                            "description": "In the language the person wrote in.",
                        },
                        "advance": {
                            "type": "string",
                            "enum": list(ADVANCE),
                            "description": (
                                "auto (default): the next step comes when the person has clicked "
                                "the element or changed the field. manual: they press Next; for a "
                                "step that only explains."
                            ),
                        },
                    },
                },
            },
        },
    }

    def run(self, env, arguments, screen):
        if not screen:
            return base.ToolResult(
                "The person is not sharing their screen, so nothing can be pointed at. "
                "Explain in words, or ask them to turn screen sharing on.",
                is_error=True,
            )
        known = self._known_targets(screen)
        steps = []
        for position, step in enumerate(arguments["steps"], start=1):
            kind, name = step["target_type"], step["target_name"]
            if name not in known[kind]:
                listed = ", ".join(sorted(known[kind])[:40]) or "none"
                return base.ToolResult(
                    f"Step {position}: there is no {kind} named {name!r} on this screen. "
                    f"Available: {listed}.",
                    is_error=True,
                )
            steps.append({
                "target": {"type": kind, "name": name},
                "text": step["text"].strip(),
                "advance": step.get("advance", "auto"),
            })
        return base.ToolResult(
            f"{len(steps)} step(s) are given to the person, to read or to be shown on the screen.",
            event={"type": "guidance", "steps": steps},
        )

    @staticmethod
    def _known_targets(screen):
        def names(key):
            entries = screen.get(key) or []
            return {entry["name"] for entry in entries if isinstance(entry, dict) and entry.get("name")}

        return {
            "field": names("fields") | names("columns"),
            "button": names("buttons"),
            "tab": names("tabs"),
            "control": set(CONTROLS),
        }
