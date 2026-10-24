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

The deadline is looked at between two steps of the code, and one step that
runs in C is not stopped before it ends: 10 ** 10 ** 10, 'a' * 10 ** 10.
So the size of such a step is bounded before the code runs: the whole
numbers it writes, a power or a shift between them or of a value, a text or
a list repeated. A long range is walked one step at a time, where the
deadline sees it, even inside sum() or list().

What the checks of the code cannot know before it runs (x * x, s + s, n ** 3
with values the code computed) is checked as it runs: the code is rewritten
so that every arithmetic step between values, and the few methods that make
a text or a list longer (join, ljust, replace, extend...), go through a small
helper that refuses a result beyond MAX_BITS bits or MAX_ITEMS items before
it is computed. And the memory the process holds is looked at with the
deadline, so a loop of steps that are each small cannot fill it.

The code reads and computes. What it would write is refused before it
runs, and it runs in a savepoint that is rolled back whatever happens all
the same. A change goes through prepare_write or prepare_create, where the
person sees it and says yes.

The administrator switches the tool on in Settings ‣ Nudge: it is off by
default.
"""
import ast
import copy
import datetime
import operator
import re
import sys
import time

try:
    import psutil
except ImportError:  # pragma: no cover - Odoo requires it
    psutil = None

from odoo import models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Command
from odoo.tools import float_compare
from odoo.tools.safe_eval import datetime as safe_datetime, dateutil as safe_dateutil, safe_eval, wrap_module

from ..llm.snapshot import HIDDEN, SENSITIVE_NAME
from . import base
from .actions import SENSITIVE_METHOD
from .records import Refused, cut, get_model, holds_secret, is_closed, refusing

PYTHON_PARAM = "nudge.python"
MAX_CODE_CHARS = 4000
MAX_LOG_LINES = 40
MAX_LINE_CHARS = 500
MAX_RESULT_CHARS = 4000
# How long the code may run, in seconds: a loop that goes on is stopped there
DEADLINE_SECONDS = 10
FILENAME = "nudge:run_python"
# The size of one step that runs in C, between two looks at the deadline:
# the whole numbers the code writes or computes from numbers it writes (a
# billion keeps every id of a database), the power of a value (x ** 2, x **
# 0.5), a shift, the items of a text or a list repeated
MAX_NUMBER = 10 ** 9
MAX_EXPONENT = 3
MAX_SHIFT = 64
MAX_REPEAT = 100000
# A range longer than this is walked in Python, one step at a time
MAX_C_RANGE = 10 ** 6
# What a step on values computed by the code may make: a whole number of this
# many bits (79000 digits: a product takes milliseconds, a quotient less than
# a tenth of a second), a text, a list or bytes of this many items
MAX_BITS = 2 ** 18
MAX_ITEMS = 10 ** 6
# How much more memory than at the start the code may hold, in bytes, looked
# at every MEMORY_CHECK seconds
MAX_MEMORY = 200 * 1024 * 1024
MEMORY_CHECK = 0.05

BEYOND = "{expression} is beyond 10 ** 9: a step that size runs past the deadline."
POWER = (
    "** and << take small numbers written in the code (10 ** 6, x ** 2, 1 << 8): a computed power "
    "could run past the deadline.")
HUGE = (
    "A whole number beyond 2 ** 262144 (about 79000 digits) is more than a step can compute before "
    "the deadline.")
LONG = "A text, a list or bytes of more than 1000000 items is more than a step can build before the deadline."
FORMAT_WIDTH = "A width or a precision beyond 999999 in a text format makes a text too long for a step."
CALL_DIRECTLY = (
    "Call .{name}() on the value, s.{name}(...): it is not for passing around, a step that size "
    "could run past the deadline.")
BYTES_DIRECTLY = "bytes is called as bytes(...) or named in isinstance(x, bytes): nothing else."
SLICE_ASSIGN = "No assignment to a slice, l[1:3] = ...: it could double a list. Build a new list instead."
REPEAT = "Repeat a text or a list by a number written in the code, 100000 items at most ('-' * 40, [0] * 12)."

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
# One conversion of a % text: its width and its precision
PERCENT_FIELD = re.compile(r"%(?:\([^)]*\))?[-#0 +]*(?P<width>\*|\d+)?(?:\.(?P<precision>\*|\d+)?)?[a-zA-Z]")
# Of a domain or a method, what reads a path of fields
WITH_DOMAIN = ("search", "search_count", "search_read", "search_fetch", "read_group", "filtered_domain")
WITH_PATH = ("mapped", "filtered", "sorted")
# Of a method, where it takes the fields it reads: (position, keyword)
WITH_FIELDS = {"read": (0, "fields"), "search_read": (1, "fields"), "search_fetch": (1, "field_names"),
               "read_group": (1, "fields")}


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
        if isinstance(node, ast.Name) and (node.id in DENIED_NAMES or node.id.startswith(("__", "_nudge_"))):
            raise Refused(f"The code may not use {node.id}.")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "__" in node.value:
            raise Refused("The code may not name private attributes.")
        if isinstance(node, ast.FormattedValue) and node.conversion in (ord("r"), ord("a")):
            raise Refused("No !r or !a in an f-string: it shows the inside of things.")
        if (isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool)
                and abs(node.value) > MAX_NUMBER):
            raise Refused(BEYOND.format(expression=node.value))
        if isinstance(node, ast.BinOp):
            _check_size(node.left, node.op, node.right)
        if isinstance(node, ast.AugAssign):
            _check_size(node.target, node.op, node.value)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            left, right = node.left, node.right
            if isinstance(left, ast.Constant) and isinstance(left.value, str):
                if REPR_CONVERSION.search(left.value):
                    raise Refused("No %r or %a in a text: it shows the inside of things.")
                _check_format(left.value)
            elif not (isinstance(right, ast.Constant) and isinstance(right.value, (int, float))):
                raise Refused(
                    "% is read with a constant text on its left, '...' % (...), or a number on its "
                    "right, n % 2: between two names it is not.")


def _check_format(template):
    """:raise Refused: when a conversion of ``template``, a % text, asks for a
    width or a precision from the arguments (*) or beyond a million"""
    for match in PERCENT_FIELD.finditer(template):
        for size in (match.group("width"), match.group("precision")):
            if size and (size == "*" or int(size) >= MAX_ITEMS):
                raise Refused(FORMAT_WIDTH)


def _shown(node):
    return cut(ast.unparse(node), 60)


def _number(node):
    """:return: the value of ``node`` when it is a number written in the
        code, or arithmetic between such numbers; None otherwise
    :raise Refused: when a whole number it comes to is beyond MAX_NUMBER,
        found out before it is computed when it would take long"""
    if isinstance(node, ast.Constant):
        value = node.value
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _number(node.operand)
        return None if value is None else -value if isinstance(node.op, ast.USub) else value
    if not isinstance(node, ast.BinOp):
        return None
    left, right = _number(node.left), _number(node.right)
    if left is None or right is None:
        return None
    whole = isinstance(left, int) and isinstance(right, int)
    if isinstance(node.op, ast.Pow) and whole and right > 0 and abs(left) > 1 and right * abs(left).bit_length() > 64:
        raise Refused(BEYOND.format(expression=_shown(node)))
    if isinstance(node.op, ast.LShift) and whole and right > MAX_SHIFT:
        raise Refused(BEYOND.format(expression=_shown(node)))
    operations = {
        ast.Add: lambda: left + right, ast.Sub: lambda: left - right, ast.Mult: lambda: left * right,
        ast.Div: lambda: left / right, ast.FloorDiv: lambda: left // right, ast.Mod: lambda: left % right,
        ast.Pow: lambda: left ** right, ast.LShift: lambda: left << right,
    }
    compute = operations.get(type(node.op))
    if compute is None:
        return None
    try:
        value = compute()
    except (ArithmeticError, TypeError, ValueError):
        # Left to Python to say, at once, when the code runs
        return None
    if isinstance(value, int) and abs(value) > MAX_NUMBER:
        raise Refused(BEYOND.format(expression=_shown(node)))
    return value if isinstance(value, (int, float)) else None


def _repeated(node):
    """:return: whether ``node`` is a text or a list as the code writes it,
        and how many items it holds when that is known"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        return True, len(node.value)
    if isinstance(node, (ast.List, ast.Tuple)):
        starred = any(isinstance(item, ast.Starred) for item in node.elts)
        return True, None if starred else len(node.elts)
    if isinstance(node, (ast.JoinedStr, ast.ListComp)):
        return True, None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        (left, left_size), (right, right_size) = _repeated(node.left), _repeated(node.right)
        if left or right:
            return True, None if left_size is None or right_size is None else left_size + right_size
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        for sequence, count in ((node.left, node.right), (node.right, node.left)):
            found, size = _repeated(sequence)
            if found:
                count = _number(count)
                return True, size * count if size is not None and isinstance(count, int) else None
    return False, None


def _check_size(left, op, right):
    """:raise Refused: when ``left op right`` is one step of C larger than
    the deadline can stop: a power or a shift beyond small numbers, a
    repetition by a count that is not small or not written in the code"""
    left_value, right_value = _number(left), _number(right)
    if left_value is not None and right_value is not None:
        # Numbers written in the code: what they come to is bounded
        _number(ast.BinOp(left=left, op=op, right=right))
        return
    if isinstance(op, ast.Pow):
        if right_value is None or abs(right_value) > MAX_EXPONENT:
            raise Refused(POWER)
    elif isinstance(op, ast.LShift):
        if not isinstance(right_value, int) or not 0 <= right_value <= MAX_SHIFT:
            raise Refused(POWER)
    elif isinstance(op, ast.Mult):
        for sequence, count in ((left, right_value), (right, left_value)):
            found, size = _repeated(sequence)
            if found:
                if not isinstance(count, int) or count > MAX_REPEAT or (size is not None and size * count > MAX_REPEAT):
                    raise Refused(REPEAT)
                break


def _range(*args):
    """range, as the code calls it: a long one is walked one step at a time
    in Python, where the deadline looks at every step, even inside sum() or
    list(), which would walk it in C to its end"""
    steps = range(*args)
    try:
        long = len(steps) > MAX_C_RANGE
    except OverflowError:
        long = True
    return _stepping(steps) if long else steps


def _stepping(steps):
    # Every step resumes this frame: a call the trace of the deadline sees
    for step in steps:
        yield step


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
        if holds_secret(model, field):
            raise Refused(f"The code may not read {name}: it holds a secret.")
        if field.comodel_name and index < len(steps) - 1:
            if is_closed(model.env, field.comodel_name):
                raise Refused(f"The code may not reach {field.comodel_name}: it is not for this person.")
            model = model.env[field.comodel_name]


def _masked(records, rows):
    """:return: ``rows``, as read or search_read give them, with what holds a secret hidden: called without a list
    of fields, they read every field"""
    if not isinstance(rows, list):
        return rows
    names = {name for row in rows if isinstance(row, dict) for name in row}
    hidden = {name for name in names if name in records._fields and holds_secret(records, records._fields[name])}
    if not hidden:
        return rows
    return [{name: HIDDEN if name in hidden else value for name, value in row.items()} if isinstance(row, dict)
            else row for row in rows]


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
            if holds_secret(records, field):
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
            if name in WITH_FIELDS:
                position, keyword = WITH_FIELDS[name]
                listed = kwargs.get(keyword, args[position] if len(args) > position else None)
                for spec in [listed] if isinstance(listed, str) else listed or []:
                    if isinstance(spec, str):
                        _check_path(records, re.split(r"[:(]", spec)[0].strip())
            result = method(*args, **kwargs)
            if name in ("read", "search_read"):
                result = _masked(records, result)
            return _guard(env, result)

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
        record._nudge_check_access("read")
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


SEQUENCES = (str, bytes, list, tuple)


def _whole(value):
    return isinstance(value, int)


def _check_sum(left, right):
    """+ and -: a whole number grows by a bit, a text or a list by the other's items"""
    if _whole(left) and _whole(right):
        if max(left.bit_length(), right.bit_length()) >= MAX_BITS:
            raise Refused(HUGE)
    elif isinstance(left, SEQUENCES) and isinstance(right, SEQUENCES):
        if len(left) + len(right) > MAX_ITEMS:
            raise Refused(LONG)


def _check_product(left, right):
    if _whole(left) and _whole(right):
        if left.bit_length() + right.bit_length() > MAX_BITS:
            raise Refused(HUGE)
        return
    for sequence, count in ((left, right), (right, left)):
        if isinstance(sequence, SEQUENCES) and _whole(count):
            if count > 0 and len(sequence) * count > MAX_ITEMS:
                raise Refused(LONG)
            return


def _check_power(left, right):
    if _whole(left) and _whole(right) and right > 1 and left.bit_length() > 1:
        if left.bit_length() * right > MAX_BITS:
            raise Refused(HUGE)


def _check_shift(left, right):
    if _whole(left) and _whole(right) and left and right > 0:
        if left.bit_length() + right > MAX_BITS:
            raise Refused(HUGE)


def _check_template(template, arguments):
    """% on a text: as long as the template plus the longest argument, once
    for each conversion"""
    if not isinstance(template, (str, bytes)):
        return
    values = list(arguments.values()) if isinstance(arguments, dict) else (
        arguments if isinstance(arguments, tuple) else (arguments,))
    longest = max((len(value) for value in values if isinstance(value, SEQUENCES)), default=0)
    if len(template) + template.count("%" if isinstance(template, str) else b"%") * longest > MAX_ITEMS:
        raise Refused(LONG)


def _step(check, operate):
    def step(left, right):
        check(left, right)
        return operate(left, right)
    return step


def _add(left, right):
    # The most frequent steps are on whole numbers or floats: straight through
    if type(left) is int and type(right) is int:
        if left.bit_length() >= MAX_BITS or right.bit_length() >= MAX_BITS:
            raise Refused(HUGE)
    elif type(left) is not float and type(right) is not float:
        _check_sum(left, right)
    return left + right


def _sub(left, right):
    if type(left) is int and type(right) is int:
        if left.bit_length() >= MAX_BITS or right.bit_length() >= MAX_BITS:
            raise Refused(HUGE)
    elif type(left) is not float and type(right) is not float:
        _check_sum(left, right)
    return left - right


def _mul(left, right):
    if type(left) is int and type(right) is int:
        if left.bit_length() + right.bit_length() > MAX_BITS:
            raise Refused(HUGE)
    elif type(left) is not float and type(right) is not float:
        _check_product(left, right)
    return left * right


def _in_place(plain, check, operate):
    """+=, -=, *=: a list is extended in place, so these go through operate"""
    def step(left, right):
        if type(left) is int or type(left) is float:
            return plain(left, right)
        check(left, right)
        return operate(left, right)
    return step


HELPERS = {
    "_nudge_add": _add, "_nudge_sub": _sub, "_nudge_mul": _mul,
    "_nudge_iadd": _in_place(_add, _check_sum, operator.iadd),
    "_nudge_isub": _in_place(_sub, _check_sum, operator.isub),
    "_nudge_imul": _in_place(_mul, _check_product, operator.imul),
}
HELPERS |= {
    f"_nudge_{name}": _step(check, operate)
    for name, check, operate in (
        ("pow", _check_power, operator.pow), ("ipow", _check_power, operator.ipow),
        ("lshift", _check_shift, operator.lshift), ("ilshift", _check_shift, operator.ilshift),
        ("mod", _check_template, operator.mod), ("imod", _check_template, operator.imod),
    )
}


def _sum(iterable, start=0):
    """sum, as the code calls it: of numbers it is the sum of Python, of
    anything else (sum(lists, [])) it is walked one step at a time, where
    every step is checked and the deadline sees it"""
    if isinstance(start, (int, float)):
        total = sum(iterable, start)
        if _whole(total):
            _check_sum(total, 0)
        return total
    total = start
    for item in iterable:
        total = _add(total, item)
    return total


def _round(number, ndigits=None):
    """round: a whole number rounded to a power of ten beyond its own
    digits is 0, and 10 ** 10 ** 8 is not computed to say so"""
    if ndigits is None:
        return round(number)
    if _whole(number) and _whole(ndigits) and ndigits < 0:
        if -ndigits > number.bit_length() // 3 + 2:
            return 0
    return round(number, ndigits)


def _bytes(*args, **kwargs):
    if args and _whole(args[0]) and args[0] > MAX_ITEMS:
        raise Refused(LONG)
    return bytes(*args, **kwargs)


def _text(*pieces):
    """An f-string: its pieces, joined once their length is known"""
    if sum(map(len, pieces)) > MAX_ITEMS:
        raise Refused(LONG)
    return "".join(pieces)


def _format(value, conversion, spec):
    """One {value!s:spec} of an f-string"""
    if spec:
        for size in re.findall(r"\d+", spec):
            if int(size) >= MAX_ITEMS:
                raise Refused(FORMAT_WIDTH)
    if conversion == ord("s"):
        value = str(value)
    elif conversion == -1 and not spec and isinstance(value, str):
        return value
    return format(value, spec or "")


def _call(subject, name, *args, **kwargs):
    """``subject.name(*args)`` for the few methods that make a text, bytes or
    a list longer than their arguments say: checked first"""
    if isinstance(subject, (str, bytes)):
        if name == "join" and args:
            # A generator is walked here, where the deadline sees it, once
            items = args[0] if isinstance(args[0], (list, tuple)) else list(args[0])
            args = (items,) + args[1:]
            total = sum(len(item) for item in items if isinstance(item, SEQUENCES))
            if total + len(subject) * len(items) > MAX_ITEMS:
                raise Refused(LONG)
        else:
            _check_method(subject, name, args, kwargs)
    elif isinstance(subject, list) and name == "extend" and args:
        extra = args[0]
        if hasattr(extra, "__len__") and len(subject) + len(extra) > MAX_ITEMS:
            raise Refused(LONG)
    elif _whole(subject) and name == "to_bytes":
        length = args[0] if args else kwargs.get("length", 1)
        if _whole(length) and length > MAX_ITEMS:
            raise Refused(LONG)
    return getattr(subject, name)(*args, **kwargs)


def _check_method(subject, name, args, kwargs):
    tab = "\t" if isinstance(subject, str) else b"\t"
    if name in ("ljust", "rjust", "center", "zfill"):
        width = args[0] if args else kwargs.get("width")
        if _whole(width) and width > MAX_ITEMS:
            raise Refused(LONG)
    elif name == "expandtabs":
        tabsize = args[0] if args else kwargs.get("tabsize", 8)
        if _whole(tabsize) and len(subject) + subject.count(tab) * max(tabsize, 0) > MAX_ITEMS:
            raise Refused(LONG)
    elif name == "replace" and len(args) >= 2:
        old, new = args[0], args[1]
        if isinstance(old, SEQUENCES) and isinstance(new, SEQUENCES) and len(new) > len(old):
            count = args[2] if len(args) > 2 else kwargs.get("count", -1)
            found = subject.count(old) if old else len(subject) + 1
            if _whole(count) and count >= 0:
                found = min(found, count)
            if len(subject) + found * (len(new) - len(old)) > MAX_ITEMS:
                raise Refused(LONG)
    elif name == "translate" and args and isinstance(args[0], dict):
        longest = max((len(value) for value in args[0].values() if isinstance(value, str)), default=1)
        if len(subject) * longest > MAX_ITEMS:
            raise Refused(LONG)


# The methods that go through _call, whoever holds them
GUARDED_METHODS = frozenset({
    "join", "ljust", "rjust", "center", "zfill", "expandtabs", "replace", "translate", "extend", "to_bytes"})
OPERATIONS = {
    ast.Add: "add", ast.Sub: "sub", ast.Mult: "mul", ast.Pow: "pow", ast.LShift: "lshift", ast.Mod: "mod"}


def _named(name, *args):
    return ast.Call(func=ast.Name(id=name, ctx=ast.Load()), args=list(args), keywords=[])


def _is_fixed(node):
    """Whether ``node`` is a value written in the code that cannot make a
    step grow by more than the length of the code: a constant, or a list or a
    tuple of constants"""
    if isinstance(node, ast.Constant):
        return True
    return isinstance(node, (ast.List, ast.Tuple)) and all(isinstance(item, ast.Constant) for item in node.elts)


class Hardening(ast.NodeTransformer):
    """The code, once checked, rewritten so that the steps it cannot be
    checked for before it runs are checked as it runs (see HELPERS)"""

    def __init__(self):
        self.isinstance_depth = 0

    @staticmethod
    def _guarded(augmented, left, right, op):
        """:return: the name of the helper that takes the step, None when the
        step is small whatever the values"""
        name = OPERATIONS.get(type(op))
        if name is None:
            return None
        if isinstance(op, (ast.Add, ast.Sub)):
            # Growing by a value written in the code: by its length at most
            return None if _is_fixed(left) or _is_fixed(right) else name
        if isinstance(op, ast.Mod):
            # n % 2 shrinks; a text template grows with its arguments
            if augmented:
                return None if isinstance(right, ast.Constant) and not isinstance(right.value, (str, bytes)) else name
            return name if isinstance(left, ast.Constant) and isinstance(left.value, (str, bytes)) else None
        if _number(left) is not None and _number(right) is not None:
            return None
        if isinstance(op, ast.Mult) and (isinstance(_number(left), float) or isinstance(_number(right), float)):
            return None
        return name

    def visit_BinOp(self, node):
        self.generic_visit(node)
        name = self._guarded(False, node.left, node.right, node.op)
        if name is None:
            return node
        return ast.copy_location(_named(f"_nudge_{name}", node.left, node.right), node)

    def visit_AugAssign(self, node):
        self.generic_visit(node)
        name = self._guarded(True, node.target, node.value, node.op)
        if name is None:
            return node
        if isinstance(node.target, ast.Subscript) and isinstance(node.target.slice, ast.Slice):
            raise Refused(SLICE_ASSIGN)
        current = copy.deepcopy(node.target)
        current.ctx = ast.Load()
        call = _named(f"_nudge_i{name}", current, node.value)
        return ast.copy_location(ast.Assign(targets=[node.target], value=call), node)

    def visit_Subscript(self, node):
        if isinstance(node.ctx, ast.Store) and isinstance(node.slice, ast.Slice):
            raise Refused(SLICE_ASSIGN)
        self.generic_visit(node)
        return node

    def visit_Attribute(self, node):
        # Reached only when the method is not called on the spot (see visit_Call)
        if node.attr in GUARDED_METHODS and isinstance(node.ctx, ast.Load):
            raise Refused(CALL_DIRECTLY.format(name=node.attr))
        self.generic_visit(node)
        return node

    def visit_Name(self, node):
        if node.id == "bytes" and isinstance(node.ctx, ast.Load) and not self.isinstance_depth:
            raise Refused(BYTES_DIRECTLY)
        return node

    def visit_Call(self, node):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in GUARDED_METHODS:
            subject = self.visit(func.value)
            args = [self.visit(arg) for arg in node.args]
            call = _named("_nudge_call", subject, ast.Constant(value=func.attr), *args)
            call.keywords = [self.visit(keyword) for keyword in node.keywords]
            return ast.copy_location(call, node)
        if isinstance(func, ast.Name) and func.id == "bytes":
            node.args = [self.visit(arg) for arg in node.args]
            node.keywords = [self.visit(keyword) for keyword in node.keywords]
            node.func = ast.copy_location(ast.Name(id="_nudge_bytes", ctx=ast.Load()), func)
            return node
        named_here = isinstance(func, ast.Name) and func.id == "isinstance"
        self.isinstance_depth += named_here
        try:
            self.generic_visit(node)
        finally:
            self.isinstance_depth -= named_here
        return node

    def visit_JoinedStr(self, node):
        self.generic_visit(node)
        pieces = []
        for part in node.values:
            if isinstance(part, ast.FormattedValue):
                spec = part.format_spec or ast.Constant(value=None)
                part = _named("_nudge_format", part.value, ast.Constant(value=part.conversion), spec)
            pieces.append(part)
        return ast.copy_location(_named("_nudge_text", *pieces), node)


HELPERS.update({
    "_nudge_call": _call, "_nudge_bytes": _bytes, "_nudge_text": _text, "_nudge_format": _format})


def harden(code):
    """:return: ``code``, once checked, as the source to run: rewritten so
    that a step on values the code computes is checked as it runs
    :raise Refused: for the few shapes that cannot be checked"""
    tree = Hardening().visit(ast.parse(code, mode="exec"))
    return ast.unparse(ast.fix_missing_locations(tree))


class OverMemory(BaseException):
    """The code holds more memory than it may. Not an Exception, as TimedOut."""


def _watching(deadline):
    """:return: a trace function that stops the code at ``deadline``, or when
    it holds MAX_MEMORY more than at the start: looked at on every call
    anywhere, and on every line of the code itself"""
    process = psutil.Process() if psutil else None
    start = process.memory_info().rss if process else 0
    next_look = time.monotonic() + MEMORY_CHECK

    def trace(frame, event, arg):
        nonlocal next_look
        now = time.monotonic()
        if now > deadline:
            raise TimedOut()
        if process is not None and now >= next_look:
            next_look = now + MEMORY_CHECK
            if process.memory_info().rss - start > MAX_MEMORY:
                raise OverMemory()
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
        "attribute, no model of the system, 10 seconds, whole numbers under 79000 digits, texts and lists "
        "under a million items."
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
        code = harden(code)
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
            "range": _range,
            "xrange": _range,
            "sum": _sum,
            "round": _round,
            **HELPERS,
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
        except OverMemory:
            error = (
                f"The code held more than {MAX_MEMORY // 1024 ** 2} MB of memory and was stopped. "
                "Narrow the search, or keep less at a time.")
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
