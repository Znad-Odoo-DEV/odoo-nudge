"""Pointing at the screen.

The model says *what* to point at: a field, a button, a tab, by the name
found in the screen snapshot. It never says *where*: the browser finds the
element. A model has no means to make the browser look for an arbitrary
selector.
"""
import re

from . import base
from .commands import fold

TARGET_TYPES = ("field", "button", "tab", "control")
CONTROLS = ("new", "save", "discard", "search", "filters", "pager_next", "pager_previous", "actions")
ADVANCE = ("auto", "manual")

MAX_STEPS = 8
NAME_PATTERN = r"[A-Za-z0-9_.%()\- ]{1,120}"


# What an answer stresses: **Send**, `Send`, "Send", “Send”, «إرسال»
EMPHASIS = re.compile("\\*\\*(.+?)\\*\\*|`([^`\\n]+)`|[\"\u201c\u00ab]([^\"\u201d\u00bb\\n]{1,60})[\"\u201d\u00bb]")
# A sentence ends here: the text of a step is the sentence that names its element
SENTENCE_END = re.compile("(?<=[.!?\u061f])\\s+|\\n+")
# Words that say a field is meant, when its label is not stressed
FIELD_WORDS = ("field", "\u062d\u0642\u0644", "\u062e\u0627\u0646\u0647")
MAX_TEXT_STEPS = 5
MAX_STEP_CHARS = 300


def steps_from_text(text, screen, limit=MAX_TEXT_STEPS):
    """When a model wrote the steps instead of showing them: the elements of
    the screen its answer names, in the order it names them, become the
    steps of the pointer, each with the sentence that names it.

    A button or a tab is named by its label; a field only when its label is
    stressed, or said to be a field: the label of a field is often a common
    word. :return: steps, possibly empty"""
    if not text or not isinstance(screen, dict):
        return []
    stressed = {fold(next(group for group in match.groups() if group)) for match in EMPHASIS.finditer(text)}
    candidates = []
    for kind, key in (("button", "buttons"), ("tab", "tabs"), ("field", "fields")):
        for entry in screen.get(key) or []:
            if not isinstance(entry, dict) or not entry.get("name") or not entry.get("label"):
                continue
            label = fold(entry["label"])
            if len(label) >= 2:
                candidates.append((kind, entry["name"], label))
    # The longer label first: "send by email" is not "send"
    candidates.sort(key=lambda candidate: -len(candidate[2]))
    steps, used = [], set()
    for sentence in SENTENCE_END.split(text):
        folded = fold(sentence)
        if not folded:
            continue
        found = []
        # What a longer label took is not read again by a shorter one inside it
        left = folded
        for kind, name, label in candidates:
            if name in used:
                continue
            match = re.search(rf"(?<!\w){re.escape(label)}(?!\w)", left)
            if not match:
                continue
            if kind == "field" and label not in stressed and not any(word in folded for word in FIELD_WORDS):
                continue
            if len(label) < 3 and label not in stressed:
                continue
            found.append((match.start(), kind, name))
            used.add(name)
            left = left[:match.start()] + " " * (match.end() - match.start()) + left[match.end():]
        clean = re.sub(r"^\s*(\d+[.)]|[-*\u2022])\s*", "", sentence.replace("**", "").replace("`", "")).strip()
        for _position, kind, name in sorted(found):
            steps.append({
                "target": {"type": kind, "name": name},
                "text": clean[:MAX_STEP_CHARS],
                "advance": "auto",
            })
        if len(steps) >= limit:
            break
    return steps[:limit]


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
