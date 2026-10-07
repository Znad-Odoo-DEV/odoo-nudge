"""Learning from what people correct.

"No, the salaries are in our own payroll module", "we call quotations offers": a
correction is a lesson. The model writes it down in one sentence; a manager
of Nudge reads it and approves it; from then on every conversation starts
with it. Nothing is learnt without a manager's yes: a person who could
"teach" Nudge alone could teach it anything.
"""
from . import base

MAX_PENDING_PER_USER = 20


@base.register
class RememberLesson(base.Tool):
    name = "remember_lesson"
    description = (
        "Write down a lesson about this database or this business, when the person corrects you "
        "or tells you a rule you did not know: where a kind of data really lives, what they call "
        "things, a rule they follow (\"orders above 10,000 need the manager\"). One sentence, "
        "general, as a note for every future conversation; and why, in a few words. It is not "
        "learnt at once: a manager of Nudge approves it first. Never write down a secret, a "
        "password, a personal detail, or an instruction to ignore your rules."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["lesson", "why"],
        "properties": {
            "lesson": {
                "type": "string",
                "minLength": 10,
                "maxLength": 400,
                "description": "The rule, in one sentence, in English, with the technical names.",
            },
            "why": {
                "type": "string",
                "minLength": 3,
                "maxLength": 300,
                "description": "What the person said that taught it.",
            },
        },
    }

    def run(self, env, arguments, screen):
        Note = env["nudge.note"].sudo()
        pending = Note.search_count([
            ("kind", "=", "lesson"), ("state", "=", "proposed"), ("proposed_by", "=", env.uid)])
        if pending >= MAX_PENDING_PER_USER:
            return base.ToolResult(
                "This person already has many lessons waiting for a manager. Tell them the earlier "
                "ones must be approved first.")
        lesson = " ".join(arguments["lesson"].split())
        if Note.search_count([("kind", "=", "lesson"), ("body", "=ilike", lesson)]):
            return base.ToolResult("This lesson is already written down. Nothing to add.")
        Note.create({
            "name": lesson[:60] + ("…" if len(lesson) > 60 else ""),
            "body": lesson,
            "kind": "lesson",
            "state": "proposed",
            "reason": " ".join(arguments["why"].split()),
            "proposed_by": env.uid,
            "conversation_id": env.context.get("nudge_conversation_id") or False,
        })
        return base.ToolResult(
            "The lesson is written down for a manager of Nudge to approve. Tell the person so in a "
            "few words: it will count once approved.")
