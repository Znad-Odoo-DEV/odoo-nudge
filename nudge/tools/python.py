"""Computing with Python, as the person, and reading only.

The code written by the model runs in the sandbox of Odoo itself, the one
of the server actions (``safe_eval``), with the environment of the person:
every record it reaches passes their access rights and their record rules,
as everywhere in Nudge. On top of the sandbox, what the code may name is
checked before it runs: no sudo, no cursor, no registry, no private
attribute, no import, no while, no method that acts, and the models that
hold how Odoo works are closed to it, as they are to the other tools.

While it runs, every recordset it holds is wrapped: a many2one, a mapped
path or a search that lands on a closed model is refused there, a field
that holds a secret reads as hidden, and of the methods only those that
read are called. Nothing a savepoint cannot take back is reachable: no
action_, no send, no post, no message_. The code has DEADLINE_SECONDS to
finish; a loop that goes on is stopped there.

The code reads and computes. What it would write is refused before it
runs, and it runs in a savepoint that is rolled back whatever happens all
the same. A change goes through prepare_write or prepare_create, where the
person sees it and says yes.

The administrator switches the tool on in Settings ‣ Nudge: it is off by
default.
"""
import ast
import datetime
import re
import sys
import time

from odoo import models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Command
from odoo.tools import float_compare
from odoo.tools.safe_eval import datetime as safe_datetime, dateutil as safe_dateutil, safe_eval, wrap_module

from ..llm.snapshot import HIDDEN, SENSITIVE_NAME
from . import base
from .actions import SENSITIVE_METHOD
from .records import Refused, cut, get_model, is_closed, is_secret, refusing

PYTHON_PARAM = "nudge.python"
MAX_CODE_CHARS = 4000
MAX_LOG_LINES = 40
MAX_LINE_CHARS = 500
MAX_RESULT_CHARS = 4000
# How long the code may run, in seconds: a loop that goes on is stopped there
DEADLINE_SECONDS = 10
FILENAME = "nudge:run_python"

# time without sleep: the code computes, it does not wait
safe_time = wrap_module(__import__("time"), ["time", "strptime", "strftime"])

# Attributes the code may not name: ways around the rights of the person,
# the database itself, what shows the inside of things, and what Nudge never does
DENIED_ATTRIBUTES = frozenset({
    "sudo", "with_user", "with_env", "env", "cr", "registry", "pool", "execute", "commit",
    "rollback", "savepoint", "unlink", "invalidate_all", "flush_all", "clear", "cache",
    "transaction", "config", "params", "ir_rule", "ir_model_access", "format", "format_map",
    "sleep",
})
# Names the code may not use
DENIED_NAMES = frozenset({
    "exec", "eval", "compile", "open", "getattr", "setattr", "delattr", "globals", "locals",
    "vars", "type", "object", "super", "__import__", "input", "breakpoint", "memoryview",
    "classmethod", "staticmethod", "property", "format", "BaseException",
})
# What the code may call on a recordset: what reads. Every other method is
# refused, by its name before the code runs and again when the code holds it
READ_METHODS = frozenset({
    "search", "search_count", "search_read", "search_fetch", "read", "read_group", "mapped",
    "filtered", "filtered_domain", "sorted", "browse", "exists", "fields_get", "name_get",
    "name_search", "get_metadata", "get_external_id", "with_context", "ensure_one",
})
# Methods named after what they do to the world: never called from here
ACTING_METHOD = re.compile(r"^(action_|button_|message_|send|post|write|create|unlink|copy|_)")
# %r and %a in a template: what shows the inside of things
REPR_CONVERSION = re.compile(r"%[-#0 +]*\d*(?:\.\d+)?[ra]")
# Of a domain or a method, what reads a path of fields
WITH_DOMAIN = ("search", "search_count", "search_read", "search_fetch", "read_group", "filtered_domain")
WITH_PATH = ("mapped", "filtered", "sorted")


class DryRun(Exception):
    """Raised to leave the savepoint: nothing the code did is kept."""


class TimedOut(BaseException):
    """The code ran past the deadline. Not an Exception: the code cannot
    catch it and go on."""


def check_code(code):
    """:raise Refused: when the code names what it may not"""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as error:
        raise Refused(f"The code could not be read: {error}") from None
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise Refused("Nothing can be imported: datetime, dateutil and time are already there.")
        if isinstance(node, (ast.ClassDef, ast.Global, ast.Nonlocal, ast.Lambda,
                             ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith, ast.Await)):
            raise Refused("Keep to plain statements: no class, lambda, global or async.")
        if isinstance(node, ast.While):
            raise Refused("No while: loop with for over what is bounded, a recordset or a range.")
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            raise Refused("Name what is caught: except Exception, not a bare except.")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or node.attr in DENIED_ATTRIBUTES:
                raise Refused(f"The code may not use .{node.attr}: it is not for this person.")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            method = node.func.attr
            if ACTING_METHOD.match(method) or SENSITIVE_METHOD.search(method):
                raise Refused(
                    f"The code may not call .{method}(): it only reads. A change goes through "
                    "prepare_write or prepare_create, an action through its own tool.")
        if isinstance(node, ast.Name) and (node.id in DENIED_NAMES or node.id.startswith("__")):
            raise Refused(f"The code may not use {node.id}.")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "__" in node.value:
            raise Refused("The code may not name private attributes.")
        if isinstance(node, ast.FormattedValue) and node.conversion in (ord("r"), ord("a")):
            raise Refused("No !r or !a in an f-string: it shows the inside of things.")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            left, right = node.left, node.right
            if isinstance(left, ast.Constant) and isinstance(left.value, str):
                if REPR_CONVERSION.search(left.value):
                    raise Refused("No %r or %a in a text: it shows the inside of things.")
            elif not (isinstance(right, ast.Constant) and isinstance(right.value, (int, float))):
                raise Refused(
                    "% is read with a constant text on its left, '...' % (...), or a number on its "
                    "right, n % 2: between two names it is not.")


def _check_path(records, path):
    """:raise Refused: when ``path``, a field or fields joined by dots as a
    domain, a mapped, a filtered or a sorted reads them, goes through a
    closed model or reads a secret. A field the model does not have is left
    to the ORM to tell"""
    model = records
    steps = path.split(".")
    for index, name in enumerate(steps):
        field = model._fields.get(name)
        if field is None:
            return
        if is_secret(field):
            raise Refused(f"The code may not read {name}: it holds a secret.")
        if field.comodel_name and index < len(steps) - 1:
            if is_closed(model.env, field.comodel_name):
                raise Refused(f"The code may not reach {field.comodel_name}: it is not for this person.")
            model = model.env[field.comodel_name]


def _unwrap(value):
    """:return: ``value`` as the ORM takes it: the recordsets out of their wrapping"""
    if isinstance(value, Records):
        return value._records
    if isinstance(value, list):
        return [_unwrap(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_unwrap(item) for item in value)
    if isinstance(value, dict):
        return {key: _unwrap(item) for key, item in value.items()}
    return value


def _guard(env, value):
    """:return: ``value`` as the code may hold it: a recordset of a closed
    model is refused however it was reached, every other recordset is
    wrapped, and what holds values (a list, a tuple, a dict) is looked into,
    a secret among the keys of a dict hidden"""
    if isinstance(value, models.BaseModel):
        if is_closed(env, value._name):
            raise Refused(f"The code may not reach {value._name}: it is not for this person.")
        return Records(env, value)
    if isinstance(value, Records):
        return value
    if isinstance(value, list):
        return [_guard(env, item) for item in value]
    if isinstance(value, tuple):
        return tuple(_guard(env, item) for item in value)
    if isinstance(value, dict):
        return {
            key: HIDDEN if isinstance(key, str) and SENSITIVE_NAME.search(key) else _guard(env, item)
            for key, item in value.items()}
    return value


class Records:
    """A recordset as the code sees it: what it reaches is checked on the
    way. A field that holds a secret reads as hidden; a record of a closed
    model is refused, whether a many2one, a mapped path or a search led to
    it; of the methods, only those that read are called, and what they are
    given or give back goes through the same check. The recordset itself
    stays out of the hands of the code."""

    def __init__(self, env, records):
        self._env = env
        self._records = records

    def __getattr__(self, name):
        if name.startswith("_") or name in DENIED_ATTRIBUTES:
            raise Refused(f"The code may not use .{name}: it is not for this person.")
        records = self._records
        field = records._fields.get(name)
        if field is not None:
            if is_secret(field):
                return HIDDEN
            return _guard(self._env, getattr(records, name))
        value = getattr(records, name)
        if callable(value):
            if name not in READ_METHODS:
                raise Refused(
                    f"The code may not call .{name}(): it only reads. A change goes through "
                    "prepare_write or prepare_create, an action through its own tool.")
            return self._reading(name, value)
        return _guard(self._env, value)

    def _reading(self, name, method):
        """:return: ``method``, a method that reads, called with what the code
        gives it unwrapped and its result wrapped. A path of fields is checked
        before the ORM walks it; a function the code hands over sees the
        records as the code does"""
        env, records = self._env, self._records

        def given(value):
            if callable(value) and not isinstance(value, Records):
                return lambda *items: _unwrap(value(*[_guard(env, item) for item in items]))
            return _unwrap(value)

        def call(*args, **kwargs):
            args = [given(value) for value in args]
            kwargs = {key: given(value) for key, value in kwargs.items()}
            if name in WITH_DOMAIN:
                domain = kwargs.get("domain", args[0] if args else [])
                for term in domain if isinstance(domain, (list, tuple)) else []:
                    if isinstance(term, (list, tuple)) and len(term) == 3 and isinstance(term[0], str):
                        _check_path(records, term[0])
                groupby = kwargs.get("groupby", args[2] if name == "read_group" and len(args) > 2 else [])
                for group in [groupby] if isinstance(groupby, str) else groupby:
                    if isinstance(group, str):
                        _check_path(records, group.split(":")[0])
            elif name in WITH_PATH:
                path = kwargs.get("key", args[0] if args else None)
                if isinstance(path, str):
                    _check_path(records, path)
            return _guard(env, method(*args, **kwargs))

        return call

    def __getitem__(self, key):
        if isinstance(key, str):
            return self.__getattr__(key)
        return _guard(self._env, self._records[key])

    def __iter__(self):
        for record in self._records:
            yield _guard(self._env, record)

    def __len__(self):
        return len(self._records)

    def __bool__(self):
        return bool(self._records)

    def __contains__(self, item):
        return _unwrap(item) in self._records

    def __eq__(self, other):
        return self._records == _unwrap(other)

    def __ne__(self, other):
        return self._records != _unwrap(other)

    def __lt__(self, other):
        return self._records < _unwrap(other)

    def __le__(self, other):
        return self._records <= _unwrap(other)

    def __gt__(self, other):
        return self._records > _unwrap(other)

    def __ge__(self, other):
        return self._records >= _unwrap(other)

    def __hash__(self):
        return hash(self._records)

    def __or__(self, other):
        return _guard(self._env, self._records | _unwrap(other))

    def __and__(self, other):
        return _guard(self._env, self._records & _unwrap(other))

    def __sub__(self, other):
        return _guard(self._env, self._records - _unwrap(other))

    def __add__(self, other):
        return _guard(self._env, self._records + _unwrap(other))

    def __repr__(self):
        return repr(self._records)

    def __str__(self):
        return str(self._records)


class SandboxEnvironment:
    """What the code sees as ``env``: the models this person may read, in
    their environment, and a few facts about them. No cursor, no sudo."""

    def __init__(self, env):
        self._env = env
        self.uid = env.uid
        # The person themself is open to the code, what they lead to is checked
        self.user = Records(env, env.user)
        self.company = _guard(env, env.company)
        self.companies = _guard(env, env.companies)
        self.lang = env.lang
        self.context = dict(env.context)

    def __getitem__(self, name):
        if not isinstance(name, str):
            raise Refused("A model is named by a string.")
        return Records(self._env, get_model(self._env, name, "read"))

    def __contains__(self, name):
        return isinstance(name, str) and name in self._env and not is_closed(self._env, name)

    def ref(self, xml_id):
        record = self._env.ref(xml_id, raise_if_not_found=False)
        if record is None or is_closed(self._env, record._name):
            raise Refused(f"There is no record {xml_id!r} that this person can read.")
        record.check_access("read")
        return Records(self._env, record)

    def __repr__(self):
        return "env"


def _plain(value, depth=0):
    """A value of the code as plain data the model can read."""
    if depth > 4:
        return cut(repr(value))
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return cut(value, MAX_LINE_CHARS)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key)[:80]: _plain(item, depth + 1) for key, item in list(value.items())[:50]}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item, depth + 1) for item in list(value)[:50]]
    if isinstance(value, Records):
        value = value._records
    if isinstance(value, models.BaseModel):
        # A recordset: what it is, how many, the first names. Not even that of a closed model
        if is_closed(value.env, value._name):
            raise Refused(f"The code may not reach {value._name}: it is not for this person.")
        names = value[:10].mapped("display_name") if value.ids else []
        return {"model": value._name, "count": len(value), "ids": value.ids[:20], "names": names}
    return cut(repr(value), MAX_LINE_CHARS)


def _watching(deadline):
    """:return: a trace function that stops the code at ``deadline``: looked
    at on every call anywhere, and on every line of the code itself"""
    def trace(frame, event, arg):
        if time.monotonic() > deadline:
            raise TimedOut()
        return trace if frame.f_code.co_filename == FILENAME else None
    return trace


@base.register
class RunPython(base.Tool):
    name = "run_python"
    group = "nudge.group_nudge_agent"
    modes = (base.MODE_AGENT,)
    kind = "code"
    description = (
        "Compute with Python, as the person, when search_records and aggregate_records are not "
        "enough: a loop over records with a calculation per record, a comparison between two "
        "sets, a date arithmetic. The code runs in the sandbox of Odoo with env, the models this "
        "person may read (env['sale.order'].search(...), .read(), .mapped(), .filtered()), user, "
        "datetime, dateutil, time, Command, float_compare and UserError. log(value) tells you "
        "something; set result to what you want back. The code only reads: search, read, mapped, "
        "filtered, sorted, browse and the like; write, create, action_ and send methods are "
        "refused, as are while loops, .format and %r. To change records, use prepare_write or "
        "prepare_create with the ids the code found. No import, no sudo, no cursor, no private "
        "attribute, no model of the system, 10 seconds."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["code", "purpose"],
        "properties": {
            "code": {"type": "string", "minLength": 1, "maxLength": MAX_CODE_CHARS},
            "purpose": {
                "type": "string",
                "minLength": 3,
                "maxLength": 200,
                "description": "What the code computes, in the language of the person: kept in the log.",
            },
        },
    }

    def is_available(self, env, mode=base.MODE_TALK):
        return super().is_available(env, mode) and env["nudge.conversation"]._is_python_allowed()

    @refusing
    def run(self, env, arguments, screen):
        code = arguments["code"]
        check_code(code)
        lines = []

        def log(*values):
            if len(lines) < MAX_LOG_LINES:
                lines.append(" ".join(cut(str(value), MAX_LINE_CHARS) for value in values))

        sandbox = SandboxEnvironment(env)
        context = {
            "env": sandbox,
            "user": sandbox.user,
            "uid": env.uid,
            "datetime": safe_datetime,
            "dateutil": safe_dateutil,
            "time": safe_time,
            "float_compare": float_compare,
            "Command": Command,
            "UserError": UserError,
            "log": log,
            "result": None,
        }
        started = time.monotonic()
        error = None
        try:
            with env.cr.savepoint():
                # The code is watched for its deadline while it runs, and no longer
                previous = sys.gettrace()
                sys.settrace(_watching(started + DEADLINE_SECONDS))
                try:
                    safe_eval(code, context, mode="exec", nocopy=True, filename=FILENAME)
                finally:
                    sys.settrace(previous)
                # Whatever the code did, nothing is kept
                raise DryRun()
        except DryRun:
            pass
        except TimedOut:
            error = (
                f"The code ran for more than {DEADLINE_SECONDS} seconds and was stopped. Narrow "
                "the search, or compute less at a time.")
        except AccessError:
            # What was reached is not told: no more than a search would say
            error = "The code reached what this person may not read."
        except Refused as refusal:
            error = str(refusal)
        except Exception as exception:  # noqa: BLE001
            # safe_eval wraps what the code raised in a ValueError: that is what is told
            cause = exception.__context__ if isinstance(exception, ValueError) else None
            if isinstance(cause, Refused):
                error = str(cause)
            else:
                cause = cause or exception
                error = cut(f"{type(cause).__name__}: {cause}", MAX_LINE_CHARS)
        finally:
            # What the code wrote, if it could, is gone from the database: gone from the cache too
            env.invalidate_all()
        payload = {
            "purpose": arguments["purpose"],
            "output": lines,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
        if error:
            payload["error"] = error
            return base.ToolResult(base.dump(payload)[:MAX_RESULT_CHARS], is_error=True)
        payload["result"] = _plain(context.get("result"))
        payload["note"] = (
            "What the code wrote, if anything, was discarded. To change records, use "
            "prepare_write or prepare_create.")
        return base.ToolResult(base.dump(payload)[:MAX_RESULT_CHARS])
