"""Computing with Python, as the person, and reading only.

The code written by the model runs in the sandbox of Odoo itself, the one
of the server actions (``safe_eval``), with the environment of the person:
every record it reaches passes their access rights and their record rules,
as everywhere in Nudge. On top of the sandbox, what the code may name is
checked before it runs: no sudo, no cursor, no registry, no private
attribute, no import, and the models that hold how Odoo works are closed
to it, as they are to the other tools.

The code reads and computes. What it writes is taken back: it runs in a
savepoint that is rolled back whatever happens. A change goes through
prepare_write or prepare_create, where the person sees it and says yes.

The administrator switches the tool on in Settings ‣ Nudge: it is off by
default.
"""
import ast
import datetime
import time

from odoo.exceptions import AccessError, UserError
from odoo.fields import Command
from odoo.tools import float_compare
from odoo.tools.safe_eval import datetime as safe_datetime, dateutil as safe_dateutil, safe_eval
from odoo.tools.safe_eval import time as safe_time

from . import base
from .records import Refused, cut, get_model, is_closed, refusing

PYTHON_PARAM = "nudge.python"
MAX_CODE_CHARS = 4000
MAX_LOG_LINES = 40
MAX_LINE_CHARS = 500
MAX_RESULT_CHARS = 4000

# Attributes the code may not name: ways around the rights of the person,
# the database itself, and what Nudge never does
DENIED_ATTRIBUTES = frozenset({
    "sudo", "with_user", "with_env", "env", "cr", "registry", "pool", "execute", "commit",
    "rollback", "savepoint", "unlink", "invalidate_all", "flush_all", "clear", "cache",
    "transaction", "config", "params", "ir_rule", "ir_model_access",
})
# Names the code may not use
DENIED_NAMES = frozenset({
    "exec", "eval", "compile", "open", "getattr", "setattr", "delattr", "globals", "locals",
    "vars", "type", "object", "super", "__import__", "input", "breakpoint", "memoryview",
    "classmethod", "staticmethod", "property",
})


class DryRun(Exception):
    """Raised to leave the savepoint: nothing the code did is kept."""


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
                             ast.AsyncFunctionDef, ast.Await)):
            raise Refused("Keep to plain statements: no class, lambda, global or async.")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or node.attr in DENIED_ATTRIBUTES:
                raise Refused(f"The code may not use .{node.attr}: it is not for this person.")
        if isinstance(node, ast.Name) and (node.id in DENIED_NAMES or node.id.startswith("__")):
            raise Refused(f"The code may not use {node.id}.")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "__" in node.value:
            raise Refused("The code may not name private attributes.")


class SandboxEnvironment:
    """What the code sees as ``env``: the models this person may read, in
    their environment, and a few facts about them. No cursor, no sudo."""

    def __init__(self, env):
        self._env = env
        self.uid = env.uid
        self.user = env.user
        self.company = env.company
        self.companies = env.companies
        self.lang = env.lang
        self.context = dict(env.context)

    def __getitem__(self, name):
        if not isinstance(name, str):
            raise Refused("A model is named by a string.")
        return get_model(self._env, name, "read")

    def __contains__(self, name):
        return isinstance(name, str) and name in self._env and not is_closed(self._env, name)

    def ref(self, xml_id):
        record = self._env.ref(xml_id, raise_if_not_found=False)
        if record is None or is_closed(self._env, record._name):
            raise Refused(f"There is no record {xml_id!r} that this person can read.")
        record.check_access("read")
        return record


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
    if hasattr(value, "_name") and hasattr(value, "ids"):
        # A recordset: what it is, how many, the first names
        names = value[:10].mapped("display_name") if value.ids else []
        return {"model": value._name, "count": len(value), "ids": value.ids[:20], "names": names}
    return cut(repr(value), MAX_LINE_CHARS)


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
        "something; set result to what you want back. The code only reads: what it writes is "
        "discarded when it ends. To change records, use prepare_write or prepare_create with the "
        "ids the code found. No import, no sudo, no cursor, no private attribute, no model of the "
        "system."
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

        context = {
            "env": SandboxEnvironment(env),
            "user": env.user,
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
                safe_eval(code, context, mode="exec", nocopy=True, filename="nudge:run_python")
                # Whatever the code did, nothing is kept
                raise DryRun()
        except DryRun:
            pass
        except AccessError:
            # What was reached is not told: no more than a search would say
            error = "The code reached what this person may not read."
        except Refused as refusal:
            error = str(refusal)
        except Exception as exception:  # noqa: BLE001
            error = cut(f"{type(exception).__name__}: {exception}", MAX_LINE_CHARS)
        finally:
            # What the code wrote is gone from the database: gone from the cache too
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
