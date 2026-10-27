"""Typing in the form the person is on.

The text is typed in their browser, in the form they have open, the way
their keyboard would: nothing is written in the database. It becomes a
change that is not saved yet, that they can read, edit, undo, and that they
save themselves, with their own rights.
"""
from . import base
from .guidance import NAME_PATTERN

WRITABLE_TYPES = ("char", "text", "many2one")
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
        "themselves. Only the fields of the screen snapshot of \"type\" char, text or many2one, not "
        "\"readonly\", can be written in; use their \"name\". In a many2one field (Job Position, "
        "Customer, Department) the text is the name of the record to pick, and it must exist. "
        "Match the field by its \"label\" in the snapshot: Job Position is not Job Title. For any "
        "other field, show where it is with show_on_screen. Write the text in the language they want it in, which for a "
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
        if entry.get("type") == "many2one":
            return self._pick(env, screen, name, label, text)
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

    def _pick(self, env, screen, name, label, text):
        """Found in the live trial (2026-10-24, 2026-10-27): "Write X in the Job Position field" is
        a many2one. The record is found here, as the person (their rules), and the browser sets
        it the way a pick in the dropdown would."""
        model = screen.get("model")
        field = env[model]._fields.get(name) if isinstance(model, str) and model in env else None
        if field is None or field.type != "many2one":
            return base.ToolResult(f"The field {name!r} cannot be set from here.", is_error=True)
        comodel = env[field.comodel_name]
        found = comodel.name_search(" ".join(text.split()), limit=6)
        exact = [item for item in found if item[1].casefold() == text.casefold()]
        if len(exact) == 1:
            found = exact
        if not found:
            return base.ToolResult(
                f"There is no {comodel._description} named {text!r}: nothing was typed in “{label}”. "
                "Tell the person, and ask whether to use another.", is_error=True)
        if len(found) > 1:
            names = ", ".join(repr(item[1]) for item in found)
            return base.ToolResult(
                f"Several {comodel._description} match {text!r}: {names}. Ask the person which one.",
                is_error=True)
        record_id, display_name = found[0]
        return base.ToolResult(
            f"“{label}” is set to {display_name} on the person's screen. It is not saved: they read it and save.",
            event={
                "type": "write",
                "field": name,
                "label": label,
                "text": display_name,
                "mode": "replace",
                "value": {"id": record_id, "display_name": display_name},
            },
        )
