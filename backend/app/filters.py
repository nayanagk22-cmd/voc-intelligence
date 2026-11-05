"""Structured slice filters.

The planner does not hand-write WHERE clauses. It emits a small JSON filter spec,
which this module validates against a column whitelist, grounds against real
catalog values and renders into SQL in two equivalent forms:

* parameterised (``$1, $2``) for the deterministic queries we own
  (verbatims, source distribution, period/KPI stats), and
* literal, for the ``slice`` CTE that wraps the LLM-generated aggregation query.

Because every branch of the workflow reads the *same* slice, the table, chart,
quotes and source bar are guaranteed to describe the same rows.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field

from .catalog import ALL_COLUMNS, CATEGORICAL_COLUMNS, DATE_COLUMNS, NUMERIC_COLUMNS, Catalog

Op = Literal["in", "not_in", "ilike_any", "between", "gte", "lte"]
TEXT_COLUMNS = [c for c in ALL_COLUMNS if c not in NUMERIC_COLUMNS + DATE_COLUMNS]


class Filter(BaseModel):
    column: str
    op: Op
    values: list[Any] = Field(default_factory=list)


def _parse_date(value: Any) -> dt.datetime:
    text = str(value).strip()[:10]
    return dt.datetime.strptime(text, "%Y-%m-%d")


def normalize_filters(raw: list[dict], catalog: Catalog) -> tuple[list[Filter], list[str]]:
    """Validate + ground planner filters. Returns (filters, human-readable warnings)."""
    filters: list[Filter] = []
    warnings: list[str] = []
    for item in raw or []:
        try:
            f = Filter.model_validate(item)
        except Exception:  # noqa: BLE001
            warnings.append(f"Ignored malformed filter: {item}")
            continue
        if f.column not in ALL_COLUMNS:
            warnings.append(f"Ignored filter on unknown column '{f.column}'")
            continue
        values = [v for v in f.values if v is not None and str(v).strip() != ""]
        if not values:
            continue

        if f.op in ("in", "not_in"):
            if f.column not in CATEGORICAL_COLUMNS:
                # exact match on free text is rarely intended; fall back to ILIKE
                f = Filter(column=f.column, op="ilike_any", values=[f"%{v}%" for v in values])
                filters.append(f)
                continue
            resolved: list[str] = []
            for v in values:
                exact = catalog.canonical(f.column, v)
                if exact:
                    resolved.append(exact)
                    continue
                partial = [c for c in catalog.values.get(f.column, []) if str(v).lower() in c.lower()]
                if partial:
                    resolved.extend(partial)
                    warnings.append(f"Matched '{v}' in {f.column} to: {', '.join(partial)}")
                else:
                    warnings.append(f"No {f.column} value matches '{v}'")
            f.values = sorted(set(resolved))
            filters.append(f)  # may be empty -> renders as FALSE (honest empty slice)

        elif f.op == "ilike_any":
            if f.column not in TEXT_COLUMNS:
                warnings.append(f"ILIKE not supported on '{f.column}', filter ignored")
                continue
            f.values = [str(v) if "%" in str(v) else f"%{v}%" for v in values]
            filters.append(f)

        elif f.op in ("between", "gte", "lte"):
            if f.column not in DATE_COLUMNS + NUMERIC_COLUMNS:
                warnings.append(f"Range filter not supported on '{f.column}', filter ignored")
                continue
            need = 2 if f.op == "between" else 1
            if len(values) < need:
                warnings.append(f"Range filter on '{f.column}' needs {need} value(s), ignored")
                continue
            try:
                if f.column in DATE_COLUMNS:
                    f.values = [_parse_date(v) for v in values[:need]]
                else:
                    f.values = [float(v) for v in values[:need]]
            except (TypeError, ValueError):
                warnings.append(f"Unparseable range values on '{f.column}', filter ignored")
                continue
            filters.append(f)
    return filters, warnings


def _literal(value: Any) -> str:
    if isinstance(value, dt.datetime):
        return f"TIMESTAMP '{value:%Y-%m-%d %H:%M:%S}'"
    if isinstance(value, (int, float)):
        return repr(float(value))
    return "'" + str(value).replace("'", "''") + "'"


def _array_literal(values: list[Any]) -> str:
    return "ARRAY[" + ", ".join(_literal(v) for v in values) + "]::text[]"


class _Renderer:
    """Renders filters either with $n placeholders or as inline literals."""

    def __init__(self, parameterised: bool, start: int = 1) -> None:
        self.parameterised = parameterised
        self.params: list[Any] = []
        self.next = start

    def bind(self, value: Any, array: bool = False) -> str:
        if not self.parameterised:
            return _array_literal(value) if array else _literal(value)
        self.params.append(value)
        ph = f"${self.next}"
        self.next += 1
        if array:
            return f"{ph}::text[]"
        if isinstance(value, dt.datetime):
            return f"{ph}::timestamp"
        if isinstance(value, (int, float)):
            return f"{ph}::float8"
        return ph

    def clause(self, f: Filter) -> str:
        col = f.column  # whitelisted in normalize_filters
        if f.op == "in":
            return f"{col} = ANY({self.bind(f.values, array=True)})" if f.values else "FALSE"
        if f.op == "not_in":
            return f"({col} IS NULL OR NOT {col} = ANY({self.bind(f.values, array=True)}))"
        if f.op == "ilike_any":
            return f"{col} ILIKE ANY({self.bind(f.values, array=True)})"
        if f.op == "gte":
            return f"{col} >= {self.bind(f.values[0])}"
        if f.op == "lte":
            v = f.values[0]
            if isinstance(v, dt.datetime):  # inclusive end date
                return f"{col} < {self.bind(v + dt.timedelta(days=1))}"
            return f"{col} <= {self.bind(v)}"
        if f.op == "between":
            lo, hi = f.values
            if isinstance(hi, dt.datetime):
                hi = hi + dt.timedelta(days=1)
                return f"{col} >= {self.bind(lo)} AND {col} < {self.bind(hi)}"
            return f"{col} BETWEEN {self.bind(lo)} AND {self.bind(hi)}"
        raise ValueError(f"unsupported op {f.op}")


def render_where(filters: list[Filter], parameterised: bool = True, start: int = 1) -> tuple[str, list[Any]]:
    r = _Renderer(parameterised, start)
    clauses = [r.clause(f) for f in filters]
    return (" AND ".join(clauses) if clauses else "TRUE"), r.params


def describe_filters(filters: list[Filter]) -> list[dict[str, Any]]:
    """JSON-friendly description for the UI ('applied filters' chips)."""
    out = []
    for f in filters:
        vals = [v.strftime("%Y-%m-%d") if isinstance(v, dt.datetime) else v for v in f.values]
        out.append({"column": f.column, "op": f.op, "values": vals})
    return out
