"""SQL safety guard (AST level, via sqlglot).

The LLM writes an aggregation query against a virtual table called ``slice``.
This module:

1. parses it (exactly one statement, must be a SELECT / set operation),
2. rejects anything that is not read-only (DML, DDL, SELECT INTO, row locks,
   COPY, SET, transaction control, dangerous functions),
3. only allows the tables ``slice`` / ``enriched_unified_data`` and the query's own CTEs,
4. injects the ``slice`` CTE (built from validated filters) as the first CTE, and
5. enforces a row LIMIT.

It is one layer of defence; the read-only database role and READ ONLY
transaction are the others.
"""

from __future__ import annotations

from sqlglot import exp, parse, parse_one
from sqlglot.errors import ParseError

from .config import TABLE_NAME

SLICE = "slice"
ALLOWED_TABLES = {TABLE_NAME, SLICE}

_FORBIDDEN_NODE_NAMES = [
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Merge", "Into",
    "TruncateTable", "Copy", "Set", "Transaction", "Commit", "Rollback", "Grant", "Revoke",
    "Lock", "Use", "Analyze",
]
FORBIDDEN_NODES = tuple(getattr(exp, n) for n in _FORBIDDEN_NODE_NAMES if hasattr(exp, n))

FORBIDDEN_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink", "txid_")
FORBIDDEN_FUNCTIONS = {
    "set_config", "current_setting", "query_to_xml", "query_to_xml_and_xmlschema",
    "table_to_xml", "database_to_xml", "xpath", "copy", "nextval", "setval",
}


class SQLGuardError(ValueError):
    """Raised when generated SQL is unsafe or invalid. Message is fed back for repair."""


def _with_key(node: exp.Expression) -> str:
    # sqlglot renamed the arg from "with" to "with_" in recent versions
    return "with_" if "with_" in node.arg_types else "with"


def _function_name(fn: exp.Func) -> str:
    if isinstance(fn, exp.Anonymous):
        return str(fn.name).lower()
    return fn.sql_name().lower()


def validate_and_compose(llm_sql: str, slice_where_sql: str, row_limit: int = 200) -> str:
    """Validate LLM SQL, inject the slice CTE and a LIMIT. Returns executable SQL."""
    sql = (llm_sql or "").strip().rstrip(";").strip()
    if not sql:
        raise SQLGuardError("Empty SQL.")

    try:
        statements = [s for s in parse(sql, read="postgres") if s is not None]
    except ParseError as exc:
        raise SQLGuardError(f"SQL syntax error: {exc}") from exc
    if len(statements) != 1:
        raise SQLGuardError("Exactly one SQL statement is allowed.")
    ast = statements[0]

    if not isinstance(ast, exp.Query):
        raise SQLGuardError(f"Only SELECT queries are allowed (got {type(ast).__name__}).")

    for node in ast.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SQLGuardError(f"Forbidden construct: {type(node).__name__}.")
        if isinstance(node, exp.Func):
            name = _function_name(node)
            if name in FORBIDDEN_FUNCTIONS or name.startswith(FORBIDDEN_FUNCTION_PREFIXES):
                raise SQLGuardError(f"Function '{name}' is not allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in ast.find_all(exp.CTE)}
    if SLICE in cte_names:
        raise SQLGuardError("Do not define a CTE named 'slice'; it is provided for you.")
    for table in ast.find_all(exp.Table):
        name = table.name.lower()
        if table.args.get("db") or table.args.get("catalog"):
            raise SQLGuardError(f"Schema-qualified table '{table.sql()}' is not allowed.")
        if name not in ALLOWED_TABLES and name not in cte_names:
            raise SQLGuardError(
                f"Table '{name}' is not allowed. Query FROM {SLICE} (or your own CTEs)."
            )

    # Inject slice CTE first so the model's own CTEs can reference it.
    slice_query = parse_one(f"SELECT * FROM {TABLE_NAME} WHERE {slice_where_sql}", read="postgres")
    slice_cte = exp.CTE(this=slice_query, alias=exp.TableAlias(this=exp.to_identifier(SLICE)))
    key = _with_key(ast)
    existing = ast.args.get(key)
    if existing is not None:
        existing.set("expressions", [slice_cte, *existing.expressions])
    else:
        ast.set(key, exp.With(expressions=[slice_cte]))

    # Enforce LIMIT
    limit_node = ast.args.get("limit")
    current = None
    if limit_node is not None:
        try:
            current = int(limit_node.expression.name)
        except (AttributeError, TypeError, ValueError):
            current = None
    if current is None or current > row_limit:
        ast = ast.limit(row_limit, copy=False)

    return ast.sql(dialect="postgres")
