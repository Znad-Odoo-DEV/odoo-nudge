"""Reading the database with SQL, for administrators, reading only.

SQL goes under the rights of Odoo: a query reads tables, not records a
person may see. So the tool is for the administrators of the system only
(the group Settings), who may read everything in Odoo anyway, and only
those who may act in every company, since a query knows no company rule.
An administrator switches it on in Settings ‣ Nudge: it is off by default.

What a query may do is bounded all the same: one SELECT, no comment, no
word that changes or locks anything, no function beyond those of a report,
no table outside the models the administrator may read, none of the tables
that hold secrets (users, parameters, providers), at most 50 rows, 10
seconds. Reading only is not left to the words of the query: it runs in a
savepoint the database itself keeps read-only, and the savepoint is rolled
back when it ends. Every query is in the log, with who asked it.

The fields kept for some groups stay with those groups: a query that names
the column of one this administrator is not in is refused, and so is a *,
a whole row or a list of column names in FROM over its table. A column
whose name says secret may be listed in the main SELECT, where it comes
back hidden, by the name it is given and by the column it is read from: it
is not computed with, compared, or carried through a subquery. Texts are
read as Postgres reads them, so that no rule is passed by a text that ends
elsewhere than it seems to.
"""
import datetime
import decimal
import re

import psycopg2

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
# The functions of a report: what counts, sums, rounds, cuts, formats or
# ranks, and the types a value is cast to. Every other function is refused
# by name, whatever it is: one that runs a query of its own (query_to_xml,
# dblink), reads a file (pg_read_file), or builds a name (chr) reads what
# the rule on tables keeps closed
ALLOWED_FUNCTIONS = frozenset({
    "count", "sum", "avg", "min", "max", "coalesce", "nullif", "round", "abs", "floor", "ceil",
    "ceiling", "trunc", "mod", "sign", "power", "sqrt", "date_trunc", "date_part", "extract",
    "now", "current_date", "current_timestamp", "age", "date", "to_char", "to_date",
    "to_timestamp", "make_date", "lower", "upper", "initcap", "length", "char_length",
    "substr", "substring", "left", "right", "concat", "concat_ws", "trim", "ltrim", "rtrim",
    "btrim", "replace", "split_part", "position", "strpos", "greatest", "least", "string_agg",
    "array_agg", "bool_and", "bool_or", "every", "array_length", "cardinality", "unnest",
    "row_number", "rank", "dense_rank", "lag", "lead", "first_value", "last_value",
    "percentile_cont", "percentile_disc", "json_agg", "jsonb_agg", "json_build_object",
    "jsonb_build_object", "generate_series",
    "numeric", "decimal", "int", "integer", "bigint", "float", "text", "varchar", "char",
    "character", "boolean", "timestamp", "timestamptz", "time", "interval",
})
# Words that stand before a parenthesis without calling anything
NOT_A_FUNCTION = frozenset({
    "select", "from", "where", "in", "not", "and", "or", "exists", "on", "as", "join", "values",
    "over", "filter", "within", "by", "any", "all", "some", "when", "then", "else", "case",
    "cast", "between", "like", "ilike", "similar", "is", "using", "having", "union",
    "intersect", "except", "distinct", "lateral", "row", "array", "with", "order", "group",
    "partition", "limit", "offset",
})
CALLED = re.compile(r"\b([A-Za-z_][A-Za-z0-9_.]*)\s*\(")
# A text joined to another with ||: how a name is built out of pieces no rule reads
BUILT_STRING = re.compile(r"'(?:[^']|'')*'\s*\|\||\|\|\s*'")
PLAIN_NAME = re.compile(r"[a-z_][a-z0-9_]*")
# A value that is a key or a hash, whatever its column is called
KEY_VALUE = re.compile(r"\bsk-|\bAIza|\bgsk_|\$pbkdf2|\$2[aby]\$")
# The pieces of a query once its texts are blanked: names, numbers, signs
TOKEN = re.compile(r"[^\W\d]\w*|\d+(?:\.\d*)?(?:e[+-]?\d+)?|\.\d+|''|::|\S")
NAME_TOKEN = re.compile(r"[^\W\d]\w*")
# The words that open a clause, all reserved by Postgres: what a comma
# belongs to is read back to the nearest one
CLAUSE = frozenset({
    "select", "from", "join", "on", "using", "where", "group", "having", "order", "limit",
    "offset", "union", "intersect", "except", "window", "fetch", "returning",
})
# Words that follow a table, or a column, without being its alias: none of
# them can be one
NOT_AN_ALIAS = frozenset({
    "where", "join", "inner", "left", "right", "full", "outer", "cross", "natural", "on", "using",
    "group", "order", "limit", "offset", "union", "intersect", "except", "window", "having",
    "fetch", "for", "tablesample", "lateral", "and", "or", "not", "select", "from", "with",
    "returning", "when", "then", "else", "end", "is", "in", "as", "isnull", "notnull",
})
SET_OPERATIONS = frozenset({"union", "intersect", "except"})
# The words the rules read as SQL: a quoted name may not be one of them
SQL_WORDS = CLAUSE | NOT_AN_ALIAS | {"table", "only", "distinct", "all", "public", "by", "values"}

TEXTS = "Write each text plainly between single quotes, a quote inside doubled ('it''s'): no E'...', no $."
QUOTED_NAME = "A quoted name must be a plain lowercase name, as Odoo names its tables and columns."
KEPT = "The query reads {what}, a field for the groups {groups} only, and this person is not in them."
SECRET = (
    "{column} of {table} holds a secret: list it only as a column of the main SELECT, where it "
    "comes back hidden.")
WHOLE = (
    "{table} has columns hidden from this person ({columns}): name the columns needed, without *, "
    "a whole row or a column list in FROM.")


def _tables_of(env):
    """:return: {table: [(model name, field)]} for the models this
    administrator may read: the table of each, with no field, and the
    relation table of each many2many, with its field"""
    tables = {}
    for name in env.registry:
        model = env[name]
        if model._abstract or model._transient or CLOSED_MODELS.match(name):
            continue
        if not model._nudge_has_access("read"):
            continue
        tables.setdefault(model._table, []).append((name, None))
        for field in model._fields.values():
            if field.type == "many2many" and field.relation and not CLOSED_MODELS.match(field.comodel_name):
                tables.setdefault(field.relation, []).append((name, field))
    return tables


def _kept_from(env, field):
    """:return: the groups ``field`` is kept for, when this person is in none of them"""
    if not field.groups or env.su:
        return None
    if field.groups == "." or not env.user.user_has_groups(field.groups):
        return field.groups
    return None


def _hidden_columns(env, named, tables):
    """:return: {table: {column: groups}} for the tables of ``named``: the
        columns of the fields kept for groups this administrator is not in,
        with those groups, and the columns whose name says secret, with None
    :raise Refused: when a relation table it names holds a many2many kept
        for such groups"""
    hidden = {}
    env.cr.execute(
        "SELECT c.relname, a.attname FROM pg_attribute a"
        " JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = 'public' AND c.relname IN %s AND a.attnum > 0 AND NOT a.attisdropped",
        (tuple(sorted(named)),))
    for table, column in env.cr.fetchall():
        if SENSITIVE_NAME.search(column):
            hidden.setdefault(table, {})[column] = None
    for table in sorted(named):
        for name, relation in tables[table]:
            if relation is not None:
                groups = _kept_from(env, relation)
                if groups:
                    raise Refused(KEPT.format(what=f"the table {table} of {name}.{relation.name}", groups=groups))
                continue
            for field in env[name]._fields.values():
                if field.store and field.column_type:
                    groups = _kept_from(env, field)
                    if groups:
                        hidden.setdefault(table, {})[field.name] = groups
    return hidden


def _blank_texts(text):
    """:return: ``text`` with every text blanked to '' and every quoted name
        written plainly, between spaces: what the rules read is what
        Postgres reads as code
    :raise Refused: on a text Postgres reads otherwise than plainly (E'...'
        with its backslashes, $$...$$), and on a quoted name that is not
        plain or is a word of SQL"""
    code = []
    index, size = 0, len(text)
    while index < size:
        char = text[index]
        if char == "$":
            raise Refused(TEXTS)
        if char in "'\"":
            if char == "'" and index and text[index - 1] in "eE" and not (
                    index > 1 and (text[index - 2].isalnum() or text[index - 2] in "_$")):
                raise Refused(TEXTS)
            end = index + 1
            while True:
                end = text.find(char, end)
                if end < 0:
                    raise Refused(TEXTS if char == "'" else QUOTED_NAME)
                if not text.startswith(char * 2, end):
                    break
                end += 2
            if char == "'":
                code.append("''")
            else:
                name = text[index + 1:end]
                if not PLAIN_NAME.fullmatch(name) or name in SQL_WORDS:
                    raise Refused(QUOTED_NAME)
                code.append(f" {name} ")
            index = end + 1
        else:
            code.append(char)
            index += 1
    return "".join(code)


def _clause(tokens, index):
    """:return: the word that opens the clause ``index`` stands in, and where
        it stands, read back at the same depth; None inside parentheses
        that do not open one"""
    level = 0
    for at in range(index, -1, -1):
        token = tokens[at]
        if token == ")":
            level += 1
        elif token == "(":
            if not level:
                return None, at
            level -= 1
        elif not level and token in CLAUSE:
            return token, at
    return None, -1


def _opening(tokens, index):
    """:return: where the parenthesis that ``index`` closes opens"""
    level = 0
    for at in range(index, -1, -1):
        if tokens[at] == ")":
            level += 1
        elif tokens[at] == "(":
            level -= 1
            if not level:
                return at
    return -1


def _is_from(tokens, at):
    """:return: whether the FROM at ``at`` is the FROM of a SELECT, not that
    of IS DISTINCT FROM, EXTRACT or SUBSTRING"""
    return not (at and tokens[at - 1] == "distinct") and _clause(tokens, at - 1)[0] == "select"


def _may_read(tokens, index):
    """:return: whether a table after ``index`` may stand in a FROM. Says
    yes more than Postgres would: a table read is never missed"""
    if index < 0:
        return False
    if tokens[index] in ("from", "join"):
        return True
    return tokens[index] == "," and _clause(tokens, index - 1)[0] in ("from", "join", "on", "using")


def _reads(tokens, index):
    """:return: whether a table after ``index`` surely stands in a FROM.
    Says yes less than Postgres would: no whole row passes for a table read"""
    if index < 0:
        return False
    token = tokens[index]
    if token == "join":
        return True
    if token == "from":
        return _is_from(tokens, index)
    if token != ",":
        return False
    word, at = _clause(tokens, index - 1)
    if word == "join":
        return True
    if word == "on":
        return not (at and tokens[at - 1] == "distinct")
    if word == "using":
        return at + 1 < len(tokens) and tokens[at + 1] == "("
    return word == "from" and _is_from(tokens, at)


def _every_column(tokens, index):
    """:return: whether the * at ``index`` stands for every column: not a
    product, not count(*), not table.*"""
    before = tokens[index - 1] if index else ""
    if before in ("select", "distinct", "all", ","):
        return True
    if before == ")":
        opening = _opening(tokens, index - 1)
        return opening >= 2 and tokens[opening - 1] == "on" and tokens[opening - 2] == "distinct"
    return False


def _listed(tokens, index, plain, depth):
    """:return: whether the column at ``index`` is listed as it is in the
    main SELECT: alone between two commas, maybe after its table, maybe
    given a name. There Postgres says which column it comes from"""
    if not plain or depth[index]:
        return False
    start = index - 2 if index >= 2 and tokens[index - 1] == "." else index
    if start < 1 or tokens[start - 1] not in ("select", "distinct", "all", ","):
        return False
    if _clause(tokens, start - 1)[0] != "select":
        return False
    after = index + 1
    if after < len(tokens) and tokens[after] == "as":
        after += 1
    if after < len(tokens) and NAME_TOKEN.fullmatch(tokens[after]) and tokens[after] not in NOT_AN_ALIAS:
        after += 1
    return after >= len(tokens) or tokens[after] in (",", "from")


def _check_hidden(code, hidden):
    """:raise Refused: when the query reaches a hidden column of a table it
    reads otherwise than by listing a secret in the main SELECT: by naming
    a column kept for groups, by a *, a whole row or a column list in FROM"""
    tokens = TOKEN.findall(code.lower())
    size = len(tokens)
    depth, level = [], 0
    for token in tokens:
        if token == ")":
            level -= 1
        depth.append(level)
        if token == "(":
            level += 1
    plain = bool(tokens) and tokens[0] == "select" and not SET_OPERATIONS & set(tokens)

    def refuse(table):
        raise Refused(WHOLE.format(table=table, columns=", ".join(sorted(hidden[table]))))

    # Where each table stands: read in a FROM, maybe under an alias
    owner, bound = {}, set()
    for index, token in enumerate(tokens):
        if token not in hidden:
            continue
        before = index - 1
        if before >= 1 and tokens[before] == "." and tokens[before - 1] == "public":
            before -= 2
        while before >= 0 and tokens[before] in ("(", "only"):
            before -= 1
        if before >= 0 and tokens[before] == "table":
            refuse(token)
        if not _may_read(tokens, before):
            continue
        owner[token] = token
        surely = _reads(tokens, before)
        if surely:
            bound.add(index)
        after = index + 1
        if after < size and tokens[after] == "as":
            after += 1
        if after < size and NAME_TOKEN.fullmatch(tokens[after]) and tokens[after] not in NOT_AN_ALIAS:
            owner.setdefault(tokens[after], token)
            if surely:
                bound.add(after)
            if after + 1 < size and tokens[after + 1] == "(":
                refuse(token)
    if not owner:
        return
    read = set(owner.values())
    kept = sorted(table for table in read if any(hidden[table].values()))
    names = set(tokens)
    # A column kept for groups is not named, wherever and however
    for table in kept:
        for column, groups in sorted(hidden[table].items()):
            if groups and column in names:
                raise Refused(KEPT.format(what=f"{column} of {table}", groups=groups))
    # A table read, or its alias, stands before one of its columns: never
    # alone, as a whole row, and never before a * unless that * is allowed
    for index, token in enumerate(tokens):
        table = owner.get(token)
        if table is None or index in bound:
            continue
        if index and tokens[index - 1] == "." and not (index > 1 and tokens[index - 2] == "public"):
            # A column of another table, that bears its name
            continue
        if index + 1 < size and tokens[index + 1] == ".":
            if index + 2 < size and tokens[index + 2] == "*" and (table in kept or not plain or depth[index]):
                refuse(table)
            continue
        refuse(table)
    # A * of the main SELECT shows the secrets hidden, as columns of their
    # table; anywhere else they would come out of it as plain values
    for index, token in enumerate(tokens):
        if token == "*" and _every_column(tokens, index):
            if kept:
                refuse(kept[0])
            if not plain or depth[index]:
                refuse(sorted(read)[0])
    secrets = {column: table for table in sorted(read) for column, groups in hidden[table].items() if groups is None}
    for index, token in enumerate(tokens):
        if token in secrets and not _listed(tokens, index, plain, depth):
            raise Refused(SECRET.format(column=token, table=secrets[token]))


def check_query(env, query):
    """:return: the query, clean, and the columns hidden from this
        administrator in the tables it reads, {table: {column: groups}}
    :raise Refused: when it is not one SELECT on tables this administrator
        may read, or reaches a hidden column otherwise than by listing it"""
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
    # What Postgres reads as code: the texts blanked, the quoted names plain
    code = _blank_texts(text)
    # Only the functions of a report are called: looked for outside the
    # texts, where a parenthesis is text
    for match in CALLED.finditer(code):
        name = match.group(1).lower()
        if name not in NOT_A_FUNCTION and name not in ALLOWED_FUNCTIONS:
            raise Refused(
                f"The query may not call {name}(): the functions of a report only, such as "
                "count, sum, avg, round, coalesce, date_trunc, to_char, lower, string_agg.")
    if BUILT_STRING.search(text):
        raise Refused("No text built with ||: name tables and columns plainly.")
    # Every word of the query that is the name of a table or a view of the
    # database must be a table this administrator may read: wherever it
    # stands, after FROM, after a comma, in a subquery or a WITH
    env.cr.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')")
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
    hidden = _hidden_columns(env, named, tables)
    if hidden:
        _check_hidden(code, hidden)
    return text, hidden


def read_only(cr, statement):
    """:return: the columns and the rows of ``statement``, and for each
        column the (table, column) it is read from when Postgres can tell,
        run where the database itself keeps the transaction read-only and
        stops it after TIMEOUT_MS: whatever the words of a query hide from
        check_query, a write fails in Postgres. The savepoint is rolled back
        once the rows are read, and the settings go with it: the transaction
        of the request writes again. An error of Postgres is raised, the
        savepoint rolled back all the same"""
    columns = rows = origins = None
    try:
        with cr.savepoint():
            cr.execute("SET LOCAL transaction_read_only = on")
            cr.execute("SET LOCAL statement_timeout = %s", (TIMEOUT_MS,))
            # The error of a query is told to the person who wrote it: not
            # an error of the server, for its log
            cr.execute(statement, log_exceptions=False)
            description = cr.description
            rows = cr.fetchall()
            columns = [column.name for column in description]
            origins = _origins(cr, description)
            # Nothing is kept, whatever the statement did
            raise _Done()
    except _Done:
        pass
    return columns, rows, origins


def _origins(cr, description):
    """:return: for each column of a result, the (table, column) of the
    database it is read from, through any alias, or None when it is
    computed"""
    keys = [(getattr(column, "table_oid", None), getattr(column, "table_column", None)) for column in description]
    oids = sorted({oid for oid, number in keys if oid and number})
    names = {}
    if oids:
        cr.execute(
            "SELECT a.attrelid, a.attnum, c.relname, a.attname FROM pg_attribute a"
            " JOIN pg_class c ON c.oid = a.attrelid WHERE a.attrelid IN %s AND a.attnum > 0",
            (tuple(oids),))
        names = {(oid, number): (table, column) for oid, number, table, column in cr.fetchall()}
    return [names.get(key) for key in keys]


def _is_hidden(column, origin, hidden):
    """:return: whether the cells of ``column`` are hidden: by the name the
    query gave it, or by the column it is read from, whatever it was named"""
    if SENSITIVE_NAME.search(column):
        return True
    if origin is None:
        return False
    table, source = origin
    return bool(SENSITIVE_NAME.search(source)) or source in hidden.get(table, ())


def _cell(column, value, hidden=False):
    if hidden or SENSITIVE_NAME.search(column):
        return HIDDEN
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (bytes, memoryview)):
        return HIDDEN
    text = base.dump(value) if isinstance(value, (list, dict)) else str(value)
    # A key or a hash is hidden whatever its column is called: a query may
    # have given it any name, or wrapped it in a text
    if KEY_VALUE.search(text):
        return HIDDEN
    return cut(text, MAX_CELL_CHARS)


def _first_line(error):
    """:return: what Postgres said of a query, its first line"""
    diag = getattr(error, "diag", None)
    message = getattr(diag, "message_primary", None) or str(error).split("\n")[0]
    return cut(message, MAX_CELL_CHARS)


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
        "states are their technical values. Reading only, enforced by the database: no change, "
        "no lock, no comment, one statement, the functions of a report only (count, sum, round, "
        "date_trunc, to_char, string_agg...), the tables of Odoo this administrator may read, "
        "at most 50 rows and 10 seconds. The tables of users, parameters and providers are "
        "closed. Prefer the other tools when they can answer: they see the records as the "
        "person does."
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
        query, hidden = check_query(env, arguments["query"])
        try:
            columns, rows, origins = read_only(
                env.cr, f"SELECT * FROM ({query}) AS nudge_query LIMIT {MAX_ROWS + 1}")
        except psycopg2.Error as error:
            # What Postgres says of the query is for whoever wrote it: the
            # savepoint is rolled back, the transaction goes on
            raise Refused(f"Postgres refused the query: {_first_line(error)}") from None
        hiding = [_is_hidden(column, origin, hidden) for column, origin in zip(columns, origins)]
        more = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
        result = {
            "purpose": arguments["purpose"],
            "columns": columns,
            "rows": [
                [_cell(column, value, hide) for column, value, hide in zip(columns, row, hiding)]
                for row in rows],
            "row_count": len(rows),
        }
        if more:
            result["note"] = f"More rows exist: the first {MAX_ROWS} are listed. Narrow the query."
        return base.ToolResult(base.dump(result))


class _Done(Exception):
    """Leaves the savepoint once the rows are read."""
