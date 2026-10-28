"""What Nudge can do besides writing text.

A tool is called by the model and run by Odoo, as the person Nudge is
talking to: with their access rights, their record rules, their companies.
The list of tools is closed; a model cannot call what is not registered.
"""
import json
import logging
import re

from odoo.exceptions import UserError

from . import schema

_logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 6000

# Talk: Nudge explains and shows. Agent: Nudge does the work. Developer:
# Nudge builds, for an administrator: automations, server actions, emails.
MODE_TALK = "talk"
MODE_AGENT = "agent"
MODE_DEV = "dev"
MODES = (MODE_TALK, MODE_AGENT, MODE_DEV)
# What a refused action leaves: the model told "has been created" of a refusal
NOTHING_MADE = "Nothing was made or changed: if you tell the person, say so."


class ToolResult:
    """:param content: what the model reads back
    :param is_error: the call did not do what was asked
    :param event: sent to the browser, for a tool that acts on the screen
    :param pending: what is about to be done, for a tool that waits for the
        person to decide; nothing was done yet
    :param records: ``(model, ids, names)`` the call was about, for the log
    :param unfinished: what was asked is not done yet: the model is not
        offered to tell the person it is
    :param card_lines: for a step the person said yes to, what its card reads
        once it is done, in place of the lines it read before the yes
    """

    def __init__(self, content, is_error=False, event=None, pending=None, records=None, unfinished=False,
                 card_lines=None):
        self.content = str(content)[:MAX_RESULT_CHARS]
        self.is_error = is_error
        self.event = event
        self.pending = pending
        self.records = records
        self.unfinished = unfinished
        self.card_lines = card_lines


# NP-03: what the model is shown of a schema. Every call is checked against the whole schema
# (schema.validate), which says in words what is wrong: the lengths of a text and "no other
# argument" are left out of what the model reads, and a long description is given once
LEFT_OUT = ("minLength", "maxLength")
REPEATED_CHARS = 120


def model_schema(value, seen=None):
    """:return: ``value``, a JSON schema, as the model reads it: a new value, the schema itself is not
    changed; a description of more than ``REPEATED_CHARS`` characters met again is replaced by
    "Same as above." """
    seen = set() if seen is None else seen
    if isinstance(value, list):
        return [model_schema(item, seen) for item in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, item in value.items():
        if key in LEFT_OUT or (key == "additionalProperties" and item is False):
            continue
        if key == "properties" and isinstance(item, dict):
            # The names of the arguments are kept whatever they are
            out[key] = {name: model_schema(schema_of, seen) for name, schema_of in item.items()}
        elif key == "description" and isinstance(item, str) and len(item) > REPEATED_CHARS:
            out[key] = "Same as above." if item in seen else item
            seen.add(item)
        elif key in ("enum", "required", "default", "pattern"):
            out[key] = item
        else:
            out[key] = model_schema(item, seen)
    return out


class Tool:
    name = None
    description = None
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    # The answer is complete once the tool has run: the model is not asked
    # to comment on what the person can already see
    terminal = False
    # Group the person needs for the tool to be offered
    group = "nudge.group_nudge_user"
    modes = MODES
    # What the tool does, for the log: read, show, create, write, method
    kind = "read"
    # What a step of it says in the log, when it is not a change, in English
    summary = None
    # EXT-01: the modules it needs besides its own, ("sale",): not offered in a database without them
    requires = ()

    def is_available(self, env, mode=MODE_TALK):
        # EXT-01: a tool of a module this database does not have, or of a plugin not allowed here, is not there
        if not offered_here(env, self):
            return False
        # Nudge Agents, a public channel: the service user has the tools of its channel's scope, in Talk, and
        # nothing else; outside that scope, nothing at all (public_tools)
        public = public_tools(env)
        if public is not None:
            return mode == MODE_TALK and self.name in public
        # What the agent may do, the developer may do too
        offered = mode in self.modes or (mode == MODE_DEV and MODE_AGENT in self.modes)
        if offered and env.user.has_group(self.group):
            return True
        # NP-02: a person without the Agent group is offered the reading tools, and only those
        return self.name in ASK_TOOLS and mode == MODE_TALK and ask_tier_applies(env)

    def definition(self):
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": model_schema(self.input_schema),
        }

    def run(self, env, arguments, screen):
        """:param env: environment of the person, never sudo
        :param dict arguments: checked against ``input_schema``
        :param dict screen: clean snapshot of the screen they shared, or None
        :return: :class:`ToolResult`"""
        raise NotImplementedError


class Action(Tool):
    """A tool that changes what the database holds. ``run`` checks what is
    asked and describes it; nothing is done before the person has seen the
    description and said yes. ``execute`` then does it."""
    group = "nudge.group_nudge_agent"
    modes = (MODE_AGENT,)

    def execute(self, env, step):
        """:param env: environment of the person, never sudo
        :param step: ``nudge.step`` the person said yes to
        :return: :class:`ToolResult`"""
        raise NotImplementedError


TOOLS = {}

# NP-02, Ask the data: what a person of the group Nudge User may ask of the data in Talk, whatever else their
# groups give them. The names are the whole tier: reading and counting under the rights of the person, and
# taking them to the list or the record. Nothing that creates, writes, presses a button, plans, runs code or
# reads the database is named here, so in Talk it is not in the schema at all, not only refused. A tool is added
# to the tier by naming it here, never by its group.
ASK_TOOLS = frozenset({
    "search_records", "read_record", "find_record", "aggregate_records", "show_chart", "filter_screen",
    "open_record",
})
ASK_DATA_PARAM = "nudge.ask_data"


def ask_data_enabled(env):
    """:return: whether the administrator keeps the Ask tier on (it is, until switched off)"""
    return env["ir.config_parameter"].sudo().get_param(ASK_DATA_PARAM) != "no"


def ask_tier_applies(env):
    """:return: whether the current user is offered the tier: a Nudge User, and the tier is on"""
    return env.user.has_group("nudge.group_nudge_user") and ask_data_enabled(env)


# The tools a small model is given when its provider asks for the essential
# ones: finding, reading, showing, and in each mode what it is for. A model
# that runs on a laptop reads fewer tools faster, and picks the right one more
# often; the others come in groups, by the words of the question or when it
# asks for them (groups.py). Named here for the companion modules too.
TALK_TOOLS = frozenset({
    "search_odoo_documentation", "show_on_screen", "show_menu_path", "find_screen", "open_screen",
    "describe_model", "locate_data", "search_company_files", "write_in_field", "test_import",
    "match_import_columns",
    "remember_lesson", "search_products", "record_changes",
})
AGENT_TOOLS = TALK_TOOLS | {
    "search_records", "read_record", "find_record", "aggregate_records", "filter_screen", "open_record",
    "prepare_create", "prepare_write", "call_method", "prepare_plan", "bulk_apply", "prepare_task",
    "show_chart", "prepare_revert", "save_report",
}
# Developer mode builds: what it reads to build, what it builds with, and
# prepare_write to switch off what it built. Records, imports and products
# come as groups
DEV_TOOLS = frozenset({
    "search_odoo_documentation", "show_on_screen", "show_menu_path", "find_screen", "open_screen",
    "describe_model", "locate_data", "search_company_files", "remember_lesson",
    "search_records", "read_record", "prepare_write",
    "read_automations", "check_python_code", "prepare_automation", "prepare_server_action",
    "prepare_email_template", "prepare_email_design", "prepare_email_review", "send_test_email",
    "prepare_delete", "prepare_undo", "read_view", "prepare_view_change", "prepare_button", "prepare_report_design",
    "prepare_field", "try_automation", "prepare_scheduled_action", "prepare_approval_rule",
    "prepare_approval_flow", "diagnose_failure", "prepare_code_fix",
    "prepare_module_scaffold",
})
ESSENTIAL_BY_MODE = {MODE_TALK: TALK_TOOLS, MODE_AGENT: AGENT_TOOLS, MODE_DEV: DEV_TOOLS}
ESSENTIAL_TOOLS = TALK_TOOLS | AGENT_TOOLS | DEV_TOOLS | {"more_tools"}

# NP-03: of the tools that come in groups (groups.py), those every call of the mode still carries,
# whatever the question: finding and reading, showing on the screen, and in Agent mode changing a
# record. The other tools of the groups come by the words of the question, its screen, its skills,
# or when the model asks for them with more_tools; a tool named in no group is in every call.
CORE_SHOWING = frozenset({"show_on_screen", "write_in_field"})
CORE_BY_MODE = {
    MODE_TALK: CORE_SHOWING | (ASK_TOOLS - {"show_chart"}) | {"search_database_profile", "more_tools"},
    MODE_AGENT: CORE_SHOWING | {
        "search_records", "read_record", "find_record", "aggregate_records", "filter_screen", "open_record",
        "prepare_create", "prepare_write", "call_method", "search_database_profile", "more_tools"},
    MODE_DEV: frozenset({
        "search_records", "read_record", "prepare_write", "search_database_profile", "more_tools"}),
}

# Every result of a tool is read by the model inside a frame that says it is data:
# information to reason about, never an instruction, whatever a note, a file, a
# label or an error text quoting a field says. Only the tools below answer in
# words that Nudge alone wrote (where they led the person, a lesson kept), and
# stay unframed; a new tool is framed until it is named here.
UNFRAMED_TOOLS = frozenset({
    "open_screen", "open_record", "filter_screen", "show_on_screen", "show_menu_path", "show_tour",
    "show_settings", "show_in_studio", "write_in_field", "find_screen", "more_tools", "remember_lesson",
})

# The tools whose result is what people wrote: in records, in files, in the
# pages of the documentation, in code and views. Kept as the list of the ones
# that are data by nature; the frame itself no longer depends on it.
DATA_TOOLS = frozenset({
    "search_records", "read_record", "find_record", "aggregate_records", "show_chart",
    "search_company_files", "search_odoo_documentation", "search_products", "find_products",
    "read_automations", "diagnose_failure", "read_view", "read_module_code", "test_import", "match_import_columns",
    "document_to_record",
    "summarize_record_history", "record_changes",
})

# The tools whose result is text from outside Odoo and its people: a file, a page of the web, a
# mail. Once a conversation holds any of it, the model may be following what it says, and a tool
# that sends words out of Odoo asks the person first (the reads of nudge_connect)
OUTSIDE_TOOLS = frozenset({
    "search_company_files", "search_company_sources", "document_to_record", "summarize_meeting",
    "meeting_to_records", "summarize_record_history",
})
# The questions asked about a record with its mails (an @Nudge mention in the chatter)
OUTSIDE_COMMANDS = ("mention",)
# A Char value longer than this is written text rather than a name, a code or a label
SHORT_LABEL = 60
# In what search_records and read_record answer (dump): the model, then each field with a text value
RECORD_MODEL = re.compile(r'"model":"([\w.]+)"')
STRING_VALUE = re.compile(r'"(\w+)":"((?:[^"\\]|\\.)*)')

TOOL_SETS = ("full", "essential")

# What acts on the screen of the person: a run in the background has none
SCREEN_TOOLS = frozenset({
    "filter_screen", "open_screen", "open_record", "show_menu_path", "show_on_screen", "show_tour",
    "show_settings", "write_in_field", "show_chart",
})

NO_SCREEN = (
    '<data source="{name}">There is no screen in a background run: nothing can be shown, filtered or '
    "opened. Answer with search_records (or aggregate_records) and write the rows in your answer.</data>")


def register(tool_class):
    # EXT-01 / L3: a plugin takes only a free name, never one of Nudge's (registry.claim)
    from . import registry  # noqa: PLC0415 - registry imports this module
    if registry.claim(tool_class):
        TOOLS[tool_class.name] = tool_class()
    return tool_class


def offered_here(env, tool):
    """:return: whether the database of ``env`` offers ``tool`` at all: its module is installed here, and for
    a plugin, the plugin is allowed and on (tools/registry.py). Whoever asks, in whatever mode."""
    from . import registry  # noqa: PLC0415 - registry imports this module
    return registry.offered(env, tool)


def available(env, mode=MODE_TALK, tool_set="full", extra=(), background=False):
    """:param tool_set: "essential" keeps the tools of the mode, and ``extra``;
        "core" (NP-03) every tool but those of the groups (groups.py) beyond the core of the
        mode (``CORE_BY_MODE``), and ``extra``; "full" every tool
    :param extra: names of the tools of the groups the question was given
    :return: tools offered to the current user. Sorted: the list is part
    of the prompt, and its order must not change between two requests."""
    # EXT-01: only the tools of this database, those of the plugins allowed here included
    from . import registry  # noqa: PLC0415 - registry imports this module
    here = registry.tools_here(env)
    if tool_set == "essential":
        names = ESSENTIAL_BY_MODE.get(mode, TALK_TOOLS) | set(extra) | {"more_tools"}
        # Nudge Agents: a public channel reads with the tools of the Ask tier too, within its scope (is_available)
        if mode == MODE_TALK and (ask_tier_applies(env) or public_tools(env)):
            names |= ASK_TOOLS
    else:
        names = {name for name, tool in here.items() if not getattr(tool, "small_models_only", False)}
        if tool_set == "core":
            from . import groups  # noqa: PLC0415 - groups imports this module
            core = CORE_BY_MODE.get(mode, CORE_BY_MODE[MODE_TALK])
            names = (names - (groups.routed_tools() - core)) | (core & {"more_tools"}) | set(extra)
            # L3: the tools of a plugin come with the skills of the question (or extra), not in every call
            names -= registry.plugin_names(here) - set(extra)
    # NP-03: the skills given with the question bring the tools they need (a small model gets them
    # without asking), and use_skill comes with their index only. Each name still passes is_available
    # below, as the person and in this mode: a skill narrows what is offered, it never gives a tool
    skills = env.context.get("nudge_skills") or {}
    names = set(names) | {name for name in skills.get("tools") or () if name in here
                          and not getattr(here[name], "small_models_only", False)}
    if not skills.get("index"):
        names.discard("use_skill")
    if background:
        names -= SCREEN_TOOLS
    # Nudge Agents: a conversation with an agent offers the agent's tools and no other (agent_allows)
    names = {name for name in names if agent_allows(env, name)}
    return [tool for name, tool in sorted(here.items()) if name in names and tool.is_available(env, mode)]


# Nudge Agents: what an agent is never given, whatever its list says: more_tools widens what is offered, and a
# scheduled task runs later as plain Nudge, outside the agent (v1)
AGENT_NEVER_TOOLS = frozenset({"more_tools", "prepare_task"})


def agent_allows(env, name):
    """Nudge Agents. :return: whether the agent of the conversation, when there is one, has the tool ``name``.
    Its list (``nudge.agent._tools_for``: what the agent names, within what the person may use) is put in the
    context by ``nudge.conversation._agent_scope`` from the agent stored on the conversation, at every turn:
    it only narrows what the person has, and no list restricts nothing"""
    allowed = env.context.get("nudge_agent_tools")
    return allowed is None or (name in allowed and name not in AGENT_NEVER_TOOLS)


def agent_unscoped(env):
    """Nudge Agents. :return: whether ``env`` names a conversation with an agent (``nudge_conversation_id``) but
    carries no list of its tools: a tool called there did not come through the scope of the turn
    (``nudge.conversation._agent_scope``), and is refused rather than run with every tool of the person"""
    if env.context.get("nudge_agent_tools") is not None:
        return False
    try:
        conversation_id = int(env.context.get("nudge_conversation_id") or 0)
    except (TypeError, ValueError):
        return False
    if not conversation_id:
        return False
    # sudo: whether the conversation has an agent, nothing more
    conversation = env["nudge.conversation"].sudo().browse(conversation_id).exists()
    return bool(conversation and "agent_id" in conversation._fields and conversation.agent_id)


# Nudge Agents 60.1, a public channel (website visitors, unknown phone numbers). Its turns run as the service user
# of the channel, never in sudo, with these tools only: knowledge, reading records of the models the channel lists
# (its allow-list), and handing over to a person. Every model and every field a call names is checked against the
# list before the tool runs (public_refusal), and what it answers is checked again (public_checked)
SERVICE_GROUP = "nudge.group_agent_service"
PUBLIC_KNOWLEDGE_TOOLS = frozenset({"search_odoo_documentation", "use_skill"})
PUBLIC_RECORD_TOOLS = frozenset({"search_records", "read_record", "find_record", "aggregate_records"})
HAND_OVER_TOOL = "hand_to_person"
PUBLIC_TOOLS = PUBLIC_KNOWLEDGE_TOOLS | PUBLIC_RECORD_TOOLS | {HAND_OVER_TOOL}
# The context keys of the scope of a public turn, put by nudge.conversation._agent_scope from the binding stored on
# the conversation: the tools (a tuple) and the models (a tuple) of the channel
PUBLIC_SCOPE = "nudge_agent_public"
PUBLIC_MODELS = "nudge_agent_models"
# The arguments that name a model, and those that name fields of it, in any tool, at any depth
MODEL_ARGUMENTS = frozenset({"model", "models", "res_model", "model_name", "relation", "comodel", "comodel_name"})
FIELD_ARGUMENTS = frozenset({"field", "fields", "group_by", "measures", "order"})
# Fields that point to a record of any model: never followed on a public channel
ANY_MODEL_FIELDS = ("reference", "many2one_reference")
PUBLIC_REFUSED = "This channel cannot read that: it reads only {models}. Answer from what you know, or say so."
# H2: what says a record is on the website; a public turn reads only those that are (public_published_field)
PUBLISHED_FIELDS = ("is_published", "website_published")
# Fields a public turn never names: archived records stay hidden, and free-form values (properties, json) hold
# whatever the company put there
PUBLIC_DENIED_TYPES = ("properties", "properties_definition", "json")
PUBLIC_NO_RECORDS = "This channel reads no record of the database. Answer from what you know, or say so."


def public_tools(env):
    """Nudge Agents. :return: the tools of the public channel ``env`` answers on, a tuple, when its user is the
    service user of a channel; an empty tuple when such a user is outside the scope of a public turn (nothing is
    offered or run); None for anybody else (the usual rules)"""
    if env.su or not env.user.has_group(SERVICE_GROUP):
        return None
    tools = env.context.get(PUBLIC_SCOPE)
    return tuple(tools) if isinstance(tools, (tuple, list, frozenset)) else ()


def _public_models(env):
    models = env.context.get(PUBLIC_MODELS)
    return frozenset(models) if isinstance(models, (tuple, list, frozenset)) else frozenset()


def _named(value, keys):
    """:return: every value of ``value`` (arguments of a tool) held under one of ``keys``, at any depth, a list
    flattened into its items"""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys:
                yield from (item if isinstance(item, (list, tuple)) else [item])
            if isinstance(item, (dict, list, tuple)):
                yield from _named(item, keys)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _named(item, keys)


def _field_names(value):
    """:return: the names of the fields ``value`` names: "date_order desc, name", "date:month", "amount:sum" """
    if not isinstance(value, str):
        return [value]
    names = []
    for part in value.split(","):
        words = part.split()
        if words:
            names.append(words[0].split(":")[0])
    return names


def _public_field_problem(model, name, allowed):
    """:return: why the field ``name`` of ``model`` cannot be read on a public channel, or None"""
    if not isinstance(name, str):
        return "A field is named by its technical name."
    if "." in name:
        return "This channel does not follow a field to another record."
    field = model._fields.get(name)
    if field is None:
        # Not a field (count, or a mistake the tool itself refuses)
        return None
    if name == "active":
        return "This channel reads only the records in use: it never looks at archived ones."
    if field.type in PUBLIC_DENIED_TYPES:
        return f"This channel does not read the field {name}."
    if not model._has_field_access(field, "read"):
        # The groups of the field, as the service user (a portal user) holds them: a cost price, a note
        return f"This channel does not read the field {name}."
    if field.type in ANY_MODEL_FIELDS or (field.relational and field.comodel_name not in allowed):
        return f"The field {name} points to records this channel does not read."
    return None


def public_published_field(model):
    """H2. :return: the field that says a record of ``model`` is published on the website, when a public turn
    reads it (only those records are read: a search adds it, a record named by its id is refused unless it is
    published); None for anybody else, or a model without one"""
    if public_tools(model.env) is None:
        return None
    return next((name for name in PUBLISHED_FIELDS if name in model._fields), None)


def public_fields(model, allowed):
    """:return: the usual fields of ``model`` a public channel reads: none that points to a model off its list"""
    from .records import usual_fields  # noqa: PLC0415 - records imports this module
    names = [name for name in usual_fields(model) if not _public_field_problem(model, name, allowed)]
    return names or ["display_name"]


def public_refusal(env, name, arguments):
    """Nudge Agents, a public channel: check a call before it runs, before any query.

    :return: (why it is refused, or None; the arguments to run it with: the fields a public channel reads are
        named when the call names none). Anybody but a service user: (None, ``arguments``)"""
    public = public_tools(env)
    if public is None:
        return None, arguments
    allowed = _public_models(env)
    listed = ", ".join(sorted(allowed)) or "nothing"
    tool = TOOLS.get(name)
    if name not in public or name not in PUBLIC_TOOLS or isinstance(tool, Action):
        return f"There is no tool named {name!r}.", arguments
    models = list(_named(arguments, MODEL_ARGUMENTS))
    if name in PUBLIC_RECORD_TOOLS:
        if not allowed:
            return PUBLIC_NO_RECORDS, arguments
        if not models:
            return f"Name the model, one of: {listed}.", arguments
    for model_name in models:
        if not isinstance(model_name, str) or model_name not in allowed or model_name not in env:
            return PUBLIC_REFUSED.format(models=listed), arguments
    if not models:
        return None, arguments
    model = env[models[0]]
    for value in _named(arguments, FIELD_ARGUMENTS):
        for field_name in _field_names(value):
            problem = _public_field_problem(model, field_name, allowed)
            if problem:
                return problem, arguments
    published = public_published_field(model)
    if published and name == "find_record":
        # A search by name reads no condition: the records of a published kind are looked for with search_records,
        # which reads only the published ones
        return f"Look for {model._name} with search_records on this channel.", arguments
    if published and name == "read_record":
        record_id = arguments.get("id")
        if not isinstance(record_id, int) or not model.search_count(
                [("id", "=", record_id), (published, "=", True)], limit=1):
            return "This channel reads only what is published on the website.", arguments
    if name in ("search_records", "read_record") and not arguments.get("fields"):
        arguments = dict(arguments, fields=public_fields(model, allowed))
    return None, arguments


def public_checked(env, name, result):
    """Nudge Agents, a public channel: what a tool of the records answered names only models of the channel's list;
    anything else is replaced by a refusal (a second guard after :func:`public_refusal`). :return: ``result``"""
    if public_tools(env) is None or result.is_error or name not in PUBLIC_RECORD_TOOLS:
        return result
    allowed = _public_models(env)
    named = set(RECORD_MODEL.findall(result.content if isinstance(result.content, str) else str(result.content)))
    if result.records:
        named.add(result.records[0])
    if named - allowed:
        return ToolResult(PUBLIC_REFUSED.format(models=", ".join(sorted(allowed)) or "nothing"), is_error=True)
    return result


# Nudge Agents, a rehearsal (Try it): what the model reads of a change it asked for. Nothing is prepared for
# a yes: the card is told in words, and nothing is done
REHEARSED = (
    "Rehearsal (Try it): nothing was done and nothing waits for a yes. Done for real, the person would be asked "
    "to confirm this:\n{card}\nTell the person in a sentence what would be done.")
# What a tool that is not one of reading or showing saved in a rehearsal: undone, and the model is told
REHEARSAL_UNDONE = "(Rehearsal: whatever this tool saved was undone.)"
REHEARSAL_CARD_LINES = 15


class _Undone(Exception):
    """Ends the savepoint of a call made in a rehearsal: whatever the call wrote is rolled back"""


def card_text(pending):
    """:param dict pending: what an Action prepared for its card (``ToolResult.pending``)
    :return: the card in words: its title, the records it is about, and what it would write"""
    title = str(pending.get("summary") or "")
    if pending.get("record_names"):
        title = f"{title} ({pending['record_names']})"
    lines = [title]
    for line in ((pending.get("preview") or {}).get("lines") or [])[:REHEARSAL_CARD_LINES]:
        if isinstance(line, dict):
            value = line.get("value")
            if value is None and line.get("lines"):
                value = "; ".join(map(str, line["lines"]))
            lines.append(f"- {line.get('label') or line.get('field') or ''}: {value}")
        else:
            lines.append(f"- {line}")
    return "\n".join(lines)


def rehearse(env, name, arguments, screen=None, mode=MODE_TALK):
    """Nudge Agents, a rehearsal (Try it): run one call, then undo whatever it wrote, always. A change
    (an :class:`Action`) is told as its card in words, never left waiting for a yes; what a tool would type
    in a field of the screen is not typed.

    What the AI calls made by the tool cost (``nudge.usage``) is not undone: read before the rollback and
    written again after it, as a rehearsal, it counts in the tokens of the person's day
    (``nudge.agent.budget_user_day``). Effects outside the database (a webhook of an automation, a mail an
    outside hook sends) cannot be held back by the rollback: see doc/agents.md.

    :return: :class:`ToolResult`"""
    kept, spent = [], []
    Usage = env["nudge.usage"].sudo()
    try:
        with env.cr.savepoint():
            # sudo: the ledger, which the person cannot read; the newest line before the call
            last = Usage.search([], order="id desc", limit=1).id or 0
            kept.append(call(env(context=dict(env.context, nudge_rehearsing=True)), name, arguments, screen, mode))
            spent = _usage_lines(Usage.search([("id", ">", last)], order="id"))
            raise _Undone
    except _Undone:
        pass
    if spent:
        Usage.create([_usage_kept(Usage, values) for values in spent])
    result = kept[0]
    if result.pending and not result.is_error:
        return ToolResult(REHEARSED.format(card=card_text(result.pending)))
    if result.event and result.event.get("type") == "write":
        result.event = None
    if not result.is_error and name not in (ASK_TOOLS | DATA_TOOLS | SCREEN_TOOLS) - {"write_in_field"}:
        result.content = f"{result.content} {REHEARSAL_UNDONE}"
    return result


def _usage_lines(lines):
    """:return: the stored values of ``lines`` of ``nudge.usage`` that are written, not computed, as plain data
    that outlives a rollback"""
    names = [name for name, field in lines._fields.items()
             if field.store and not field.compute and not field.related and field.type not in ("one2many", "many2many")
             and name not in ("id", "create_uid", "create_date", "write_uid", "write_date")]
    return lines.read(names, load=None) if lines else []


def _usage_kept(Usage, values):
    """:return: ``values`` read by :func:`_usage_lines` to write a line again after the rollback: a rehearsal, and
    without the links to what the rollback took away"""
    values = {name: value for name, value in values.items() if name != "id"}
    for name, value in list(values.items()):
        field = Usage._fields[name]
        if field.type == "many2one" and value and not Usage.env[field.comodel_name].browse(value).exists():
            values[name] = False
    if "rehearsal" in Usage._fields:
        values["rehearsal"] = True
    return values


def error_text(error, what, uid):
    """:return: what the model and the person are told of ``error``, raised by ``what`` for the
    user ``uid``: the words of a refusal of Odoo or of a mistake in the arguments, which they can
    act on; for a database error or a fault only its kind, since its text may hold SQL, names and
    values. The whole of it goes to the log."""
    # Written for the model or the person: a refusal of a tool, a file that cannot be read, a
    # provider's answer (its key already taken out); imported here, those modules import this one
    from ..llm.attachments import Unreadable  # noqa: PLC0415
    from ..llm.base import AdapterError  # noqa: PLC0415
    from .records import Refused  # noqa: PLC0415
    told = (Refused, Unreadable, AdapterError, UserError, ValueError, KeyError, TypeError)
    if isinstance(error, told):
        _logger.info("Nudge: %s failed for user %s: %s", what, uid, error)
        return f"{type(error).__name__}: {error}"
    _logger.warning("Nudge: %s failed for user %s", what, uid, exc_info=error)
    return f"{type(error).__name__}: the action failed on the server. Try another way."


def guarded(env, name, function):
    """Run ``function`` for the tool ``name``. Whatever goes wrong is told
    to the model as the result, and what was done before it went wrong is
    undone: nothing is raised to the conversation."""
    try:
        with env.cr.savepoint():
            return function()
    except Exception as error:  # noqa: BLE001
        return ToolResult(error_text(error, f"tool {name}", env.uid), is_error=True)


def call(env, name, arguments, screen=None, mode=MODE_TALK):
    """Run one call made by the model."""
    if env.context.get("nudge_rehearsal") and not env.context.get("nudge_rehearsing"):
        # Nudge Agents, a rehearsal (Try it): run, then undone (rehearse)
        return rehearse(env, name, arguments, screen, mode)
    tool = TOOLS.get(name)
    if tool is not None and name in SCREEN_TOOLS and env.context.get("nudge_background"):
        return ToolResult(NO_SCREEN.format(name=name), is_error=True)
    # Nudge Agents: a tool the agent does not have is not there, whatever the model asks (agent_allows); nor any
    # tool in a conversation with an agent reached outside the scope of its turn (agent_unscoped): fails closed
    if tool is None or not tool.is_available(env, mode) or not agent_allows(env, name) or agent_unscoped(env):
        return ToolResult(f"There is no tool named {name!r}.", is_error=True)
    if not isinstance(arguments, dict):
        return ToolResult("The arguments could not be read as JSON.", is_error=True)
    try:
        arguments = schema.forgive(arguments, tool.input_schema)
        schema.validate(arguments, tool.input_schema)
    except schema.SchemaError as error:
        result = ToolResult(f"Invalid arguments. {error}", is_error=True)
    else:
        # Nudge Agents, a public channel: a model or a field off the channel's list is refused here, before any
        # query; what the tool answers is checked again
        refused, arguments = public_refusal(env, name, arguments)
        if refused:
            result = ToolResult(refused, is_error=True)
        else:
            result = public_checked(env, name, guarded(env, name, lambda: tool.run(env, arguments, screen)))
    if result.is_error and isinstance(tool, Action):
        result.content = f"{result.content} {NOTHING_MADE}"
    return result


def as_data(name, content, is_error=False):
    """:return: the result of the tool ``name`` as the model reads it. Whatever
    the tool says is framed as data, errors included (they quote record names and
    values), so that an instruction written in a note or a file is reported to
    the person, not followed; "<" is escaped inside, and the frame cannot be
    closed from within. Only the tools of ``UNFRAMED_TOOLS`` answer in plain
    words, when they succeed."""
    if name in UNFRAMED_TOOLS and not is_error:
        return content
    content = content if isinstance(content, str) else str(content)
    return f'<data source="{name}">{content.replace("<", "&lt;")}</data>'


def free_text_read(env, content):
    """:return: whether ``content``, what a tool answered, gives the value of a field of text of a
    record: a Text or Html field, or a Char longer than a short label. What anybody may have written
    is there (a mail, a web form, a note); numbers, dates, choices, the names of linked records and
    short codes are not text of that kind"""
    text = str(content or "")
    found = RECORD_MODEL.search(text)
    if not found or found.group(1) not in env:
        return False
    fields = env[found.group(1)]._fields
    # The values of the records, after what the tool says of them
    start = max(text.find('"record":{', found.end()), text.find('"records":[', found.end()))
    if start < 0:
        return False
    for name, value in STRING_VALUE.findall(text, start):
        field = fields.get(name)
        if field is not None and value.strip() and (
                field.type in ("text", "html") or (field.type == "char" and len(value) > SHORT_LABEL)):
            return True
    return False


def holds_outside(env):
    """:return: whether the conversation of ``env`` holds text from outside: a file sent with a
    question, a record asked about with its mails, what a tool of ``OUTSIDE_TOOLS`` read, or the
    text of a record the model read (:func:`free_text_read`). A module adds its own sources
    (nudge_connect: the answers of the connectors)."""
    conversation_id = env.context.get("nudge_conversation_id")
    if not conversation_id:
        return False
    # sudo: whether, not what; the conversation is the person's
    Message = env["nudge.message"].sudo()
    if Message.search_count([
            ("conversation_id", "=", conversation_id), ("role", "=", "user"),
            "|", ("attachment_name", "!=", False), ("command", "in", OUTSIDE_COMMANDS)], limit=1):
        return True
    if env["nudge.step"].sudo().search_count([
            ("run_id.conversation_id", "=", conversation_id), ("tool", "in", sorted(OUTSIDE_TOOLS)),
            ("state", "!=", "failed")], limit=1):
        return True
    # What the model read back, in every mode
    for message in Message.search([("conversation_id", "=", conversation_id), ("role", "=", "tool")]):
        for result in message.tool_results or []:
            if isinstance(result, dict) and not result.get("is_error") and (
                    result.get("name") in OUTSIDE_TOOLS or free_text_read(env, result.get("content"))):
                return True
    return False


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
