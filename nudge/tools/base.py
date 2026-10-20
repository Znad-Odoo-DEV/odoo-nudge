"""What Nudge can do besides writing text.

A tool is called by the model and run by Odoo, as the person Nudge is
talking to: with their access rights, their record rules, their companies.
The list of tools is closed; a model cannot call what is not registered.
"""
import json
import logging

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

    def is_available(self, env, mode=MODE_TALK):
        # What the agent may do, the developer may do too
        offered = mode in self.modes or (mode == MODE_DEV and MODE_AGENT in self.modes)
        return offered and env.user.has_group(self.group)

    def definition(self):
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
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
    TOOLS[tool_class.name] = tool_class()
    return tool_class


def available(env, mode=MODE_TALK, tool_set="full", extra=(), background=False):
    """:param tool_set: "essential" keeps the tools of the mode, and ``extra``
    :param extra: names of the tools of the groups a small model was given
    :return: tools offered to the current user. Sorted: the list is part
    of the prompt, and its order must not change between two requests."""
    if tool_set == "essential":
        names = ESSENTIAL_BY_MODE.get(mode, TALK_TOOLS) | set(extra) | {"more_tools"}
    else:
        names = {name for name, tool in TOOLS.items() if not getattr(tool, "small_models_only", False)}
    if background:
        names -= SCREEN_TOOLS
    return [tool for name, tool in sorted(TOOLS.items()) if name in names and tool.is_available(env, mode)]


def guarded(env, name, function):
    """Run ``function`` for the tool ``name``. Whatever goes wrong is told
    to the model as the result, and what was done before it went wrong is
    undone: nothing is raised to the conversation."""
    try:
        with env.cr.savepoint():
            return function()
    except Exception as error:  # noqa: BLE001
        _logger.info("Nudge: tool %s failed for user %s: %s", name, env.uid, error)
        return ToolResult(f"{type(error).__name__}: {error}", is_error=True)


def call(env, name, arguments, screen=None, mode=MODE_TALK):
    """Run one call made by the model."""
    tool = TOOLS.get(name)
    if tool is not None and name in SCREEN_TOOLS and env.context.get("nudge_background"):
        return ToolResult(NO_SCREEN.format(name=name), is_error=True)
    if tool is None or not tool.is_available(env, mode):
        return ToolResult(f"There is no tool named {name!r}.", is_error=True)
    if not isinstance(arguments, dict):
        return ToolResult("The arguments could not be read as JSON.", is_error=True)
    try:
        arguments = schema.forgive(arguments, tool.input_schema)
        schema.validate(arguments, tool.input_schema)
    except schema.SchemaError as error:
        result = ToolResult(f"Invalid arguments. {error}", is_error=True)
    else:
        result = guarded(env, name, lambda: tool.run(env, arguments, screen))
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


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
