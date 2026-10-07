"""Typing in the form the person is on.

The text is typed in their browser, in the form they have open, the way
their keyboard would: nothing is written in the database. It becomes a
change that is not saved yet, that they can read, edit, undo, and that they
save themselves, with their own rights.
"""
from . import base
from .guidance import NAME_PATTERN

WRITABLE_TYPES = ("char", "text")
MODES = ("append", "replace")
MAX_TEXT_CHARS = 2000


@base.register
class WriteInField(base.Tool):
    name = "write_in_field"
    terminal = True
    description = (
        "Type a text in a field of the form the person is on, for them: what they dictate to "
        "you, or a text they ask you to write. Use it only when their message asks for it. The "
        "text is typed in the form and nothing is saved: they read it, change it, and save "
        "themselves. Only the text fields of the screen snapshot can be written in: \"type\" "
        "char or text, not \"readonly\"; use their \"name\". For any other field, show where it "
        "is with show_on_screen. Write the text in the language they want it in, which for a "
        "dictation is the language they spoke, and write what they said, not what you would "
        "have said."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["field", "text"],
        "properties": {
            "field": {
                "type": "string",
                "pattern": NAME_PATTERN,
                "description": "The \"name\" of the field in the snapshot.",
            },
            "text": {"type": "string", "minLength": 1, "maxLength": MAX_TEXT_CHARS},
            "mode": {
                "type": "string",
                "enum": list(MODES),
                "description": (
                    "append (default): the text comes after what the field holds. replace: "
                    "the text takes the place of what the field holds; for when they ask to "
                    "change or rewrite it."
                ),
            },
        },
    }

    def run(self, env, arguments, screen):
        if not screen or screen.get("view_type") != "form":
            return base.ToolResult(
                "The person is not sharing a form, so nothing can be typed for them. "
                "Give them the text in your answer instead.",
                is_error=True,
            )
        entries = {
            entry["name"]: entry for entry in screen.get("fields") or []
            if isinstance(entry, dict) and isinstance(entry.get("name"), str)
        }
        writable = sorted(
            name for name, entry in entries.items()
            if entry.get("type") in WRITABLE_TYPES and not entry.get("readonly")
        )
        name = arguments["field"]
        entry = entries.get(name)
        if entry is None or name not in writable:
            if entry is None:
                reason = f"There is no field named {name!r} on this screen."
            elif entry.get("readonly"):
                reason = f"The field {name!r} cannot be edited in the state the record is in."
            else:
                reason = (
                    f"The field {name!r} is a {entry.get('type')} field: only text can be "
                    "typed for the person. Show them where it is instead.")
            listed = ", ".join(writable[:40]) or "none"
            return base.ToolResult(f"{reason} Fields that can be written in: {listed}.", is_error=True)

        model = screen.get("model")
        if isinstance(model, str) and model in env and not env[model].has_access("write"):
            # They would type, and be refused when saving
            return base.ToolResult(
                "The person is not allowed to change this kind of record.", is_error=True)

        label = entry.get("label") if isinstance(entry.get("label"), str) else name
        text = arguments["text"].strip()
        if entry.get("type") == "char":
            # One line
            text = " ".join(text.split())
        if not text:
            return base.ToolResult("There is nothing to write.", is_error=True)
        return base.ToolResult(
            f"The text is typed in the field “{label}” on the person's screen. It is not saved: "
            "they read it and save.",
            event={
                "type": "write",
                "field": name,
                "label": label,
                "text": text,
                "mode": arguments.get("mode", "append"),
            },
        )
