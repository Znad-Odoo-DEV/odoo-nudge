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

# Talk: Nudge explains and shows. Agent: Nudge does the work.
MODE_TALK = "talk"
MODE_AGENT = "agent"
MODES = (MODE_TALK, MODE_AGENT)


class ToolResult:
    """:param content: what the model reads back
    :param is_error: the call did not do what was asked
    :param event: sent to the browser, for a tool that acts on the screen
    :param pending: what is about to be done, for a tool that waits for the
        person to decide; nothing was done yet
    :param records: ``(model, ids, names)`` the call was about, for the log
    """

    def __init__(self, content, is_error=False, event=None, pending=None, records=None):
        self.content = str(content)[:MAX_RESULT_CHARS]
        self.is_error = is_error
        self.event = event
        self.pending = pending
        self.records = records


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

    def is_available(self, env, mode=MODE_TALK):
        return mode in self.modes and env.user.has_group(self.group)

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


def register(tool_class):
    TOOLS[tool_class.name] = tool_class()
    return tool_class


def available(env, mode=MODE_TALK):
    """:return: tools offered to the current user. Sorted: the list is part
    of the prompt, and its order must not change between two requests."""
    return [tool for _name, tool in sorted(TOOLS.items()) if tool.is_available(env, mode)]


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
    if tool is None or not tool.is_available(env, mode):
        return ToolResult(f"There is no tool named {name!r}.", is_error=True)
    if not isinstance(arguments, dict):
        return ToolResult("The arguments could not be read as JSON.", is_error=True)
    try:
        schema.validate(arguments, tool.input_schema)
    except schema.SchemaError as error:
        return ToolResult(f"Invalid arguments. {error}", is_error=True)
    return guarded(env, name, lambda: tool.run(env, arguments, screen))


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
