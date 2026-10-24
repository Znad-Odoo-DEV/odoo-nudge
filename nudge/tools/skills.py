"""Skills (NP-03): the instructions of one kind of work, given only when a question needs them.

Every call carries a one-line index of the skills the person may use. The full text of a skill
joins the system prompt when the question holds one of its trigger words, in Arabic or in English,
or names the screen it is for (no AI), or when the model asked for it with use_skill earlier in the
conversation. At most two are given with a question.

A skill names the tools it needs. They are offered only as far as the person may use them in this
mode anyway: every name still passes ``Tool.is_available`` (groups, mode, the switches of the
administrator), so a skill narrows what is offered, never widens it. Its text is an instruction of
the company: it enters as a system block, never as a tool result, and only once published.
"""
import re

from . import base, commands, groups

# Full texts given with one question
MAX_LOADED = 2
# What the model may ask to keep for the rest of a conversation
MAX_KEPT = 2
MAX_SUMMARY = 200
KEY = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def split_words(text):
    """:return: the trigger words of a skill, one per comma, Arabic comma, semicolon or line"""
    return [word.strip() for word in re.split(r"[,\n;،]+", text or "") if word.strip()]


def split_names(text):
    return [name.strip() for name in re.split(r"[,\s]+", text or "") if name.strip()]


def forms_of(trigger_words):
    """:return: how the words of a skill are compared, as the tool groups do (groups.py)"""
    return tuple(form for form in (groups._forms(word) for word in split_words(trigger_words)) if form)


def score(forms, text):
    """:return: how many of ``forms`` the text holds: an English word by its start, an Arabic one
    by its root, words together as they are written"""
    folded = commands.fold(text or "")
    words = folded.split()
    roots = [commands.root_of(word) for word in words]
    padded = f" {folded} "
    hits = 0
    for form in forms:
        if " " in form:
            # Words together, from the start of a word: "set up" in "how do I set up", not in "reset up"
            hit = f" {form}" in padded
        elif form.isascii():
            hit = len(form) >= 3 and any(word.startswith(form) for word in words)
        else:
            hit = any(form in root for root in roots if len(form) >= 3) or form in roots
        hits += bool(hit)
    return hits


def pick(entries, text, model=None, limit=MAX_LOADED):
    """The rules router: no AI, the same answer for the same words.

    :param entries: ``(key, forms, models)`` of the skills the person may use
    :param model: the model of the screen shared with the question
    :return: the keys of the skills the question needs, the most words met first"""
    scored = []
    for key, forms, models in entries:
        hits = score(forms, text) + (2 if model and model in models else 0)
        if hits:
            scored.append((-hits, key))
    return [key for _hits, key in sorted(scored)[:limit]]


def offered_tools(env, mode, names):
    """:return: of the tools ``names``, those this person may use in ``mode``: what a skill brings
    is never more than what their groups, the mode and the switches of the administrator give"""
    return sorted(name for name in set(names) if name in base.TOOLS and base.TOOLS[name].is_available(env, mode))


@base.register
class UseSkill(base.Tool):
    name = "use_skill"
    modes = (base.MODE_TALK, base.MODE_AGENT, base.MODE_DEV)
    description = (
        "Ask for the instructions of a skill of the list \"Skills\" of your instructions, by its key, "
        "when what the person asks is that kind of work and its instructions are not given yet. They "
        "join your instructions from your next step, and stay for the next questions of this "
        "conversation, with the tools the skill needs that are open to this person. The list shows the "
        "skills closest to the question; other skills exist: ask for one by its key, or describe the "
        "work in \"work\" and the closest skill is given."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "key": {"type": "string", "minLength": 2, "maxLength": 63, "description": "The key of the skill, as the list writes it."},
            "work": {"type": "string", "minLength": 3, "maxLength": 300,
                     "description": "Instead of a key: the kind of work, in a few words, when no listed skill fits."},
        },
    }

    def run(self, env, arguments, screen):
        mode = env.context.get("nudge_mode") or base.MODE_TALK
        Skill = env["nudge.skill"]
        visible = Skill._visible(mode)
        asked = str(arguments.get("key") or "").strip().lower()
        work = str(arguments.get("work") or "").strip()
        if not asked and not work:
            return base.ToolResult("Give the key of a skill, or describe the work in \"work\".", is_error=True)
        skill = visible.filtered(lambda record: record.key == asked)[:1]
        if not skill and not asked and work:
            # The work described, read by the router as a question is (the closest skill, if any)
            found = visible._route(work).ranked
            skill = visible.filtered(lambda record: found and record.key == found[0][0])[:1]
        if not skill:
            keys = ", ".join(sorted(set(visible.mapped("key")))) or "none"
            return base.ToolResult(
                f"No skill {asked or work!r} is open to this person in this mode. Their skills: {keys}.",
                is_error=True)
        conversation = env["nudge.conversation"].browse(env.context.get("nudge_conversation_id")).exists()
        if not conversation:
            return base.ToolResult("There is no conversation to give the skill to.", is_error=True)
        if skill in conversation._skill_plan(mode)["given"]:
            return base.ToolResult(
                f"The instructions of the skill {skill.key} are already in your instructions: follow them.")
        conversation._skill_keep(skill)
        tools = offered_tools(env, mode, split_names(skill.tool_names))
        with_tools = f", with the tools {', '.join(tools)}," if tools else ""
        # The text itself comes as instructions (models/nudge_skill.py, _chat_record), never in this result
        return base.ToolResult(
            f"The skill {skill.key} joins your instructions{with_tools} from your next step and for the "
            "next questions of this conversation: read it, then go on.")
