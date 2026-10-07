"""Reading the database with SQL, for administrators, reading only.

SQL goes under the rights of Odoo: a query reads tables, not records a
person may see. So the tool is for the administrators of the system only
(the group Settings), who may read everything in Odoo anyway, and only
those who may act in every company, since a query knows no company rule.
An administrator switches it on in Settings ‣ Nudge: it is off by default.

What a query may do is bounded all the same: one SELECT, no comment, no
word that changes or locks anything, no table outside the models the
administrator may read, none of the tables that hold secrets (users,
parameters, providers), at most 50 rows, 10 seconds, in a savepoint that
is rolled back when it ends. Every query is in the log, with who asked it.
"""
import datetime
import decimal
import re

from ..llm.snapshot import HIDDEN, SENSITIVE_NAME
from . import base
from .records import CLOSED_MODELS, Refused, cut, refusing

SQL_PARAM = "nudge.sql"
MAX_QUERY_CHARS = 2000
MAX_ROWS = 50
MAX_CELL_CHARS = 300
TIMEOUT_MS = 10000

# Words that change, lock or leave the database, or run code
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|copy|execute|call|"
    r"do|vacuum|analyze|analyse|set|reset|lock|listen|notify|prepare|deallocate|refresh|cluster|"
    r"reindex|into|returning|pg_sleep|pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|"
    r"lo_import|lo_export|lo_get|dblink|pg_terminate_backend|pg_cancel_backend|current_setting|"
    r"set_config|pg_reload_conf|pg_rotate_logfile|pg_notify)\b",
    re.IGNORECASE,
)
FOR_LOCK = re.compile(r"\bfor\s+(update|share|no\s+key\s+update|key\s+share)\b", re.IGNORECASE)
COMMENT = re.compile(r"--|/\*|\*/")
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
IDENTIFIER = re.compile(r"\b(pg_[a-z_]+|information_schema)\b", re.IGNORECASE)


def _tables_of(env):
    """:return: {table: model name} for the models this administrator may
    read, and the relation tables of their many2many fields"""
    tables = {}
    for name in env.registry:
        model = env[name]
        if model._abstract or model._transient or CLOSED_MODELS.match(name):
            continue
        if not model.has_access("read"):
            continue
        tables[model._table] = name
        for field in model._fields.values():
            if field.type == "many2many" and field.relation and not CLOSED_MODELS.match(field.comodel_name):
                tables[field.relation] = name
    return tables


def check_query(env, query):
    """:return: the query, clean
    :raise Refused: when it is not one SELECT on tables this administrator may read"""
    text = " ".join(query.split())
    if COMMENT.search(text):
        raise Refused("No comment in the query.")
    text = text.rstrip(";").strip()
    if ";" in text:
        raise Refused("One statement only.")
    if not re.match(r"^(select|with)\b", text, re.IGNORECASE):
        raise Refused("The query must be a SELECT (or WITH ... SELECT).")
    forbidden = FORBIDDEN.search(text)
    if forbidden:
        raise Refused(f"The query may not use {forbidden.group(0).upper()}: reading only.")
    if FOR_LOCK.search(text):
        raise Refused("The query may not lock rows.")
    if IDENTIFIER.search(text):
        raise Refused("The catalog of Postgres is not for the query: the tables of Odoo only.")
    # Every word of the query that is the name of a table of the database
    # must be a table this administrator may read: wherever it stands, after
    # FROM, after a comma, in a subquery or a WITH
    env.cr.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    existing = {row[0] for row in env.cr.fetchall()}
    tables = _tables_of(env)
    words = {word.lower() for word in WORD.findall(text)}
    named = words & existing
    if not named:
        raise Refused("The query must read a table of Odoo: FROM a table.")
    for table in sorted(named):
        if table not in tables:
            raise Refused(
                f"There is no table {table!r} that this administrator may read. Tables are "
                "named after their model, with underscores: sale_order, res_partner.")
    return text


def _cell(column, value):
    if SENSITIVE_NAME.search(column):
        return HIDDEN
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (bytes, memoryview)):
        return HIDDEN
    if isinstance(value, (list, dict)):
        return cut(base.dump(value), MAX_CELL_CHARS)
    return cut(str(value), MAX_CELL_CHARS)


@base.register
class RunSql(base.Tool):
    name = "run_sql"
    group = "base.group_system"
    modes = (base.MODE_AGENT,)
    kind = "code"
    description = (
        "Read the database with one SELECT, for an administrator, when a join or a report is "
        "beyond search_records and aggregate_records. Tables are named after their model with "
        "underscores (sale_order, sale_order_line, res_partner); many2one columns hold ids; "
        "states are their technical values. Reading only: no change, no lock, no comment, one "
        "statement, the tables of Odoo this administrator may read, at most 50 rows and 10 "
        "seconds. The tables of users, parameters and providers are closed. Prefer the other "
        "tools when they can answer: they see the records as the person does."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["query", "purpose"],
        "properties": {
            "query": {"type": "string", "minLength": 8, "maxLength": MAX_QUERY_CHARS},
            "purpose": {
                "type": "string",
                "minLength": 3,
                "maxLength": 200,
                "description": "What the query answers, in the language of the person: kept in the log.",
            },
        },
    }

    def is_available(self, env, mode=base.MODE_TALK):
        return (
            super().is_available(env, mode)
            and env["nudge.conversation"]._is_sql_allowed()
            and self._in_every_company(env)
        )

    @staticmethod
    def _in_every_company(env):
        """A query knows no company rule: only someone allowed in every
        company may read across them."""
        companies = env["res.company"].sudo().search([])
        return not (companies - env.user.company_ids)

    @refusing
    def run(self, env, arguments, screen):
        if not self._in_every_company(env):
            raise Refused("This person is not allowed in every company: no SQL for them.")
        query = check_query(env, arguments["query"])
        cr = env.cr
        try:
            with cr.savepoint():
                cr.execute("SET LOCAL statement_timeout = %s", (TIMEOUT_MS,))
                cr.execute(f"SELECT * FROM ({query}) AS nudge_query LIMIT {MAX_ROWS + 1}")
                columns = [column.name for column in cr.description]
                rows = cr.fetchall()
                # Nothing is kept, whatever the query did
                raise _Done()
        except _Done:
            pass
        except Exception as error:  # noqa: BLE001
            message = cut(str(error).split("\n")[0], MAX_CELL_CHARS)
            raise Refused(f"Postgres refused the query: {message}") from None
        more = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
        result = {
            "purpose": arguments["purpose"],
            "columns": columns,
            "rows": [[_cell(column, value) for column, value in zip(columns, row)] for row in rows],
            "row_count": len(rows),
        }
        if more:
            result["note"] = f"More rows exist: the first {MAX_ROWS} are listed. Narrow the query."
        return base.ToolResult(base.dump(result))


class _Done(Exception):
    """Leaves the savepoint once the rows are read."""
