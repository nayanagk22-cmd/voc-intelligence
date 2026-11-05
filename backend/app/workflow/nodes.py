"""Workflow nodes. Each node reads from / writes to :class:`WorkflowState`."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import asyncpg

from .. import prompts
from ..catalog import CATEGORICAL_COLUMNS, Catalog
from ..config import TABLE_NAME, Settings
from ..db import Database
from ..filters import describe_filters, normalize_filters, render_where
from ..llm import LLM, LLMError
from ..sql_guard import SQLGuardError, validate_and_compose
from .state import SQLAttempt, WorkflowState

log = logging.getLogger(__name__)

CHANNELS = [
    ("RATINGS_REVIEWS", "Ratings & Reviews"),
    ("HML", "Chatbot / Support"),
    ("WOTS", "In-store field"),
]

# --------------------------------------------------------------------------- routing

_GREETING = re.compile(
    r"^\s*(hi+|hello|hey|hiya|howdy|good\s+(morning|afternoon|evening)|thanks|thank\s+you|cheers)\b",
    re.I,
)
_CAPABILITY = re.compile(
    r"(what\s+(can|could|does)\s+(you|this|it)|what\s+do\s+you\s+do|who\s+are\s+you|"
    r"how\s+does\s+this\s+(work|platform)|your\s+capabilities|help\s*$)",
    re.I,
)
_DOMAIN = re.compile(
    r"(complain|sentiment|battery|display|screen|thermal|price|pricing|retailer|compare|comparison|"
    r"copilot|review|feedback|quote|trend|theme|issue|problem|surface|macbook|dell|lenovo|apple|"
    r"samsung|asus|\bhp\b|best buy|amazon|currys|country|region|churn|rating|top\s+\d|most)",
    re.I,
)


def heuristic_route(question: str) -> str | None:
    """Zero-latency fast path for obvious small talk. None = ask the LLM."""
    q = question.strip()
    if _DOMAIN.search(q):
        return None
    if _CAPABILITY.search(q) or (_GREETING.search(q) and len(q.split()) <= 6):
        return "conversational"
    return None


async def route(state: WorkflowState, llm: LLM) -> None:
    fast = heuristic_route(state.question)
    if fast:
        state.route, state.reply = fast, prompts.CAPABILITIES_REPLY
        return
    try:
        out = await llm.complete_json(
            purpose="router", system=prompts.ROUTER_SYSTEM, user=state.question, usage=state.usage
        )
        state.route = out.get("route") if out.get("route") in {"conversational", "analytical", "out_of_scope"} else "analytical"
        state.reply = str(out.get("reply") or "")
    except LLMError as exc:
        # Fail open towards analysis: the planner surfaces a clear error if the LLM is down.
        log.warning("router failed, defaulting to analytical: %s", exc)
        state.route = "analytical"
    if state.route != "analytical" and not state.reply:
        state.reply = prompts.CAPABILITIES_REPLY


# --------------------------------------------------------------------------- planning

GROUPABLE = set(CATEGORICAL_COLUMNS) | {"month"}


async def plan(state: WorkflowState, llm: LLM, catalog: Catalog) -> None:
    system = prompts.PLANNER_SYSTEM.format(catalog=catalog.prompt_block(), grain_rules=prompts.GRAIN_RULES)
    user = state.question
    if state.history:
        prior = "\n".join(f"- {h}" for h in state.history[-3:])
        user = (
            f"Earlier questions in this session (use only to resolve follow-ups like 'what about Apple?'):\n"
            f"{prior}\n\nCurrent question: {state.question}"
        )
    raw = await llm.complete_json(purpose="planner", system=system, user=user, usage=state.usage)

    filters, warnings = normalize_filters(raw.get("filters") or [], catalog)
    group_by = [g for g in (raw.get("group_by") or []) if g in GROUPABLE][:2] or ["subtheme"]
    diversity = raw.get("verbatim_diversity_column")
    if diversity not in CATEGORICAL_COLUMNS:
        diversity = group_by[0] if raw.get("analysis_type") == "comparison" and group_by[0] in CATEGORICAL_COLUMNS else "data_source_type"
    vs = raw.get("verbatim_sentiment")
    if vs not in {"Negative", "Positive", "Neutral", "mixed", "any"}:
        vs = "any"

    state.plan = {
        "restated_question": raw.get("restated_question") or state.question,
        "analysis_type": raw.get("analysis_type") or "general",
        "group_by": group_by,
        "verbatim_sentiment": vs,
        "verbatim_diversity_column": diversity,
        "chart_type": raw.get("chart_type") or "bar",
        "sql_goal": raw.get("sql_goal") or "",
        "filters": describe_filters(filters),
    }
    state.filters = filters
    state.warnings.extend(warnings)
    state.where_sql, state.where_params = render_where(filters, parameterised=True)
    state.where_literal, _ = render_where(filters, parameterised=False)


# --------------------------------------------------------------------------- slice profile


async def profile_slice(state: WorkflowState, db: Database) -> None:
    rows = await db.fetch_readonly(
        f"""
        SELECT COUNT(*) AS mentions,
               COUNT(DISTINCT uuid) AS feedback_units,
               MIN(engagement_date)::date AS start_date,
               MAX(engagement_date)::date AS end_date,
               COUNT(*) FILTER (WHERE sentiment = 'Positive') AS positive,
               COUNT(*) FILTER (WHERE sentiment = 'Negative') AS negative,
               COUNT(*) FILTER (WHERE sentiment = 'Neutral')  AS neutral,
               COALESCE(ROUND(AVG(customer_rating)::numeric, 2), 0) AS avg_rating,
               COUNT(DISTINCT uuid) FILTER (WHERE churn_risk_signal = 'High Risk') AS high_churn_units
        FROM {TABLE_NAME} WHERE {state.where_sql}
        """,
        *state.where_params,
    )
    s = rows[0]
    m = s["mentions"] or 0

    def pct(n: int) -> float:
        return round(100.0 * n / m, 1) if m else 0.0

    s.update({
        "positive_pct": pct(s["positive"]),
        "negative_pct": pct(s["negative"]),
        "neutral_pct": pct(s["neutral"]),
        "net_sentiment_score": pct(s["positive"] - s["negative"]),
    })
    state.slice_stats = s


# --------------------------------------------------------------------------- NL -> SQL


def _deterministic_sql(group_col: str) -> str:
    label = (
        "TO_CHAR(DATE_TRUNC('month', engagement_date), 'YYYY-MM')" if group_col == "month"
        else f"COALESCE({group_col}, 'Unknown')"
    )
    alias = "month" if group_col == "month" else group_col
    order = "1" if group_col == "month" else "mentions DESC"
    return f"""
        SELECT {label} AS {alias},
               COUNT(*) AS mentions,
               COUNT(DISTINCT uuid) AS feedback_units,
               COUNT(*) FILTER (WHERE sentiment = 'Negative') AS negative_mentions,
               ROUND(100.0 * COUNT(*) FILTER (WHERE sentiment = 'Positive') / NULLIF(COUNT(*), 0), 1) AS positive_pct,
               ROUND(100.0 * COUNT(*) FILTER (WHERE sentiment = 'Negative') / NULLIF(COUNT(*), 0), 1) AS negative_pct,
               ROUND(100.0 * COUNT(*) FILTER (WHERE sentiment = 'Neutral')  / NULLIF(COUNT(*), 0), 1) AS neutral_pct,
               ROUND(100.0 * (COUNT(*) FILTER (WHERE sentiment = 'Positive')
                     - COUNT(*) FILTER (WHERE sentiment = 'Negative')) / NULLIF(COUNT(*), 0), 1) AS net_sentiment_score
        FROM slice GROUP BY 1 ORDER BY {order}
    """


async def generate_and_execute_sql(
    state: WorkflowState, llm: LLM, db: Database, catalog: Catalog, settings: Settings
) -> None:
    system = prompts.SQL_SYSTEM.format(catalog=catalog.prompt_block(), grain_rules=prompts.GRAIN_RULES)
    base_user = (
        f"Question: {state.question}\n"
        f"Plan: {json.dumps({k: state.plan[k] for k in ('restated_question', 'analysis_type', 'group_by', 'chart_type', 'sql_goal', 'filters')}, default=str)}\n"
        f"The slice already applies these filters and contains {state.slice_stats.get('mentions', 0)} rows."
    )
    last_sql, last_error = "", ""
    chart_hint: dict[str, Any] = {}

    for attempt in range(1, settings.max_sql_attempts + 1):
        user = base_user + (prompts.SQL_REPAIR_SUFFIX.format(sql=last_sql, error=last_error) if attempt > 1 else "")
        try:
            out = await llm.complete_json(
                purpose="sql_generation" if attempt == 1 else f"sql_repair_{attempt - 1}",
                system=system, user=user, usage=state.usage,
            )
        except LLMError as exc:
            state.sql_attempts.append(SQLAttempt(attempt, last_sql, f"LLM error: {exc}"))
            break
        last_sql = str(out.get("sql") or "")
        chart_hint = out.get("chart") or {}
        rec = SQLAttempt(attempt, last_sql)
        state.sql_attempts.append(rec)
        try:
            executable = validate_and_compose(last_sql, state.where_literal, settings.sql_row_limit)
            rows = await db.fetch_readonly(executable)
        except SQLGuardError as exc:
            rec.error = last_error = f"Rejected by SQL guard: {exc}"
            continue
        except (asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            rec.error = last_error = f"PostgreSQL error: {type(exc).__name__}: {exc}"
            continue
        rec.row_count = len(rows)
        if not rows and state.slice_stats.get("mentions", 0) > 0 and attempt < settings.max_sql_attempts:
            rec.error = last_error = (
                "The query returned 0 rows although the slice has "
                f"{state.slice_stats['mentions']} rows. Do not add extra restrictive conditions."
            )
            continue
        state.final_sql = executable
        _set_table(state, rows, chart_hint)
        return

    # Every attempt failed: fall back to a deterministic, known-good aggregation so the
    # user still gets a correct (if less tailored) answer instead of an error.
    group_col = state.plan["group_by"][0]
    executable = validate_and_compose(_deterministic_sql(group_col), state.where_literal, settings.sql_row_limit)
    rows = await db.fetch_readonly(executable)
    state.used_fallback_sql = True
    state.final_sql = executable
    state.warnings.append("Generated SQL failed validation after all repair attempts; a standard aggregation was used.")
    _set_table(state, rows, {"type": "bar", "x": "month" if group_col == "month" else group_col, "y": ["negative_mentions"]})


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _set_table(state: WorkflowState, rows: list[dict[str, Any]], chart_hint: dict[str, Any]) -> None:
    rows = rows[:50]
    columns = list(rows[0].keys()) if rows else []
    numeric = {c for c in columns if any(_is_number(r.get(c)) for r in rows)
               and all(r.get(c) is None or _is_number(r.get(c)) for r in rows)}
    clean = []
    for r in rows:  # zero-NULL contract
        clean.append({c: (r.get(c) if r.get(c) is not None else (0 if c in numeric else "Unknown")) for c in columns})
    state.table_rows = clean
    state.table_columns = [
        {"key": c, "label": c.replace("_", " ").replace("pct", "%").strip().capitalize(),
         "type": "number" if c in numeric else "text"}
        for c in columns
    ]
    state.chart = _choose_chart(columns, numeric, clean, chart_hint, state.plan.get("chart_type", "bar"))


_PREFERRED_Y = ["negative_mentions", "mentions", "feedback_units", "negative_pct", "net_sentiment_score"]


def _choose_chart(columns, numeric, rows, hint, planned_type) -> dict[str, Any]:
    if not columns or not numeric:
        return {}
    text_cols = [c for c in columns if c not in numeric]
    x = hint.get("x") if hint.get("x") in columns else (text_cols[0] if text_cols else columns[0])
    y = [c for c in (hint.get("y") or []) if c in numeric and c != x][:3]
    if not y:
        y = [next((c for c in _PREFERRED_Y if c in numeric), sorted(numeric)[0])]
    ctype = hint.get("type") or planned_type
    if ctype not in {"bar", "grouped_bar", "stacked_bar", "pie", "line"}:
        ctype = "bar"
    if ctype == "pie" and (len(y) > 1 or len(rows) > 8):
        ctype = "bar"
    if len(y) > 1 and ctype == "bar":
        ctype = "grouped_bar"
    if x == "month" and ctype != "line":
        ctype = "line"
    return {"type": ctype, "x": x, "y": y, "title": hint.get("title") or ""}


# --------------------------------------------------------------------------- parallel branches

_VERBATIM_COLUMNS = (
    "uuid, customer_voice_id, customer_voice, data_source_type, retailer_name, customer_rating, "
    "sentiment, sentiment_intensity, emotional_intensity, subtheme, maintheme, product_family, "
    "brand_name, country, engagement_date::date AS engagement_date"
)


async def _verbatim_query(state: WorkflowState, db: Database, sentiment: str | None, limit: int) -> list[dict]:
    diversity = state.plan.get("verbatim_diversity_column") or "data_source_type"
    if diversity not in CATEGORICAL_COLUMNS:  # defence in depth: identifier is whitelisted
        diversity = "data_source_type"
    params = list(state.where_params)
    sentiment_clause = ""
    if sentiment:
        params.append(sentiment)
        sentiment_clause = f" AND sentiment = ${len(params)}"
    params.append(limit)
    sql = f"""
        WITH s AS (
            SELECT DISTINCT ON (uuid) {_VERBATIM_COLUMNS}, {diversity} AS grp
            FROM {TABLE_NAME}
            WHERE {state.where_sql}{sentiment_clause} AND customer_voice IS NOT NULL
            ORDER BY uuid, sentiment_intensity DESC, emotional_intensity DESC
        ), r AS (
            SELECT s.*, ROW_NUMBER() OVER (
                PARTITION BY grp ORDER BY sentiment_intensity DESC, emotional_intensity DESC, md5(uuid)
            ) AS rn
            FROM s
        )
        SELECT * FROM r ORDER BY rn, sentiment_intensity DESC, md5(uuid) LIMIT ${len(params)}
    """
    rows = await db.fetch_readonly(sql, *params)
    for r in rows:
        r.pop("grp", None)
        r.pop("rn", None)
    return rows


async def extract_verbatims(state: WorkflowState, db: Database) -> None:
    pref = state.plan.get("verbatim_sentiment", "any")
    if state.plan.get("analysis_type") == "comparison":
        pref = "any"  # one quote per compared entity first (partitioned by diversity column)
    if pref == "mixed":
        pos, neg = await asyncio.gather(
            _verbatim_query(state, db, "Positive", 2), _verbatim_query(state, db, "Negative", 2)
        )
        quotes = neg + pos
    else:
        quotes = await _verbatim_query(state, db, None if pref == "any" else pref, 4)
    if len(quotes) < 2:  # not enough matching the preferred tone: widen tone, never the slice
        seen = {q["uuid"] for q in quotes}
        extra = await _verbatim_query(state, db, None, 4)
        quotes += [q for q in extra if q["uuid"] not in seen]
    state.verbatims = quotes[:4]


async def source_distribution(state: WorkflowState, db: Database) -> None:
    rows = await db.fetch_readonly(
        f"SELECT data_source_type AS channel, COUNT(DISTINCT uuid) AS feedback_units, COUNT(*) AS mentions "
        f"FROM {TABLE_NAME} WHERE {state.where_sql} GROUP BY 1",
        *state.where_params,
    )
    by_channel = {r["channel"]: r for r in rows}
    total = sum(r["feedback_units"] for r in rows)
    channels = []
    for key, label in CHANNELS:
        r = by_channel.get(key, {"feedback_units": 0, "mentions": 0})
        channels.append({
            "channel": key, "label": label,
            "feedback_units": r["feedback_units"], "mentions": r["mentions"],
            "pct": round(100.0 * r["feedback_units"] / total, 1) if total else 0.0,
        })
    state.source_distribution = {"basis": "feedback_units", "total_feedback_units": total, "channels": channels}


# --------------------------------------------------------------------------- synthesis

_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def _collect_numbers(state: WorkflowState) -> tuple[set[float], set[float]]:
    """Returns (values rounded to 1 decimal, values rounded to whole numbers)."""
    exact: set[float] = set()
    whole: set[float] = set()

    def add(v: Any) -> None:
        if _is_number(v):
            exact.add(round(float(v), 1))
            whole.add(float(round(float(v))))

    for row in state.table_rows:
        for v in row.values():
            add(v)
    for v in state.slice_stats.values():
        add(v)
    for c in state.source_distribution.get("channels", []):
        for k in ("feedback_units", "mentions", "pct"):
            add(c[k])
    add(state.source_distribution.get("total_feedback_units"))
    return exact, whole


def kpi_is_grounded(kpi: dict[str, Any], state: WorkflowState) -> bool:
    """Every number in the KPI value must exist in the computed data (hallucination guard).

    Decimals must match a computed value exactly (1 dp); whole numbers may also be a
    rounding of a computed value (e.g. "38%" for 37.9).
    """
    found = [float(n.replace(",", "")) for n in _NUM.findall(str(kpi.get("value", "")))]
    if not found:
        return False
    exact, whole = _collect_numbers(state)
    return all(round(n, 1) in exact or (n.is_integer() and n in whole) for n in found)


def deterministic_kpi(state: WorkflowState) -> dict[str, Any]:
    s = state.slice_stats
    rows = state.table_rows
    label_col = state.table_columns[0]["key"] if state.table_columns else None
    if rows and label_col and "negative_pct" in rows[0] and "mentions" in rows[0] and state.plan.get("analysis_type") == "pain_points":
        top = max(rows, key=lambda r: (r.get("negative_mentions", 0), r.get("negative_pct", 0)))
        return {
            "value": f"{top['negative_pct']}%",
            "label": f"negative sentiment on {top[label_col]}",
            "context": f"across {top['mentions']} mentions",
        }
    return {
        "value": f"{s.get('negative_pct', 0)}%",
        "label": "of matching mentions are negative",
        "context": f"{s.get('mentions', 0)} mentions from {s.get('feedback_units', 0)} feedback units",
    }


def _fallback_synthesis(state: WorkflowState) -> dict[str, Any]:
    s = state.slice_stats
    label_col = state.table_columns[0]["key"] if state.table_columns else None
    top = state.table_rows[:3]
    tops = ", ".join(str(r[label_col]) for r in top) if label_col else "n/a"
    return {
        "headline": "Customer feedback overview",
        "executive_summary": (
            f"{s.get('feedback_units', 0)} feedback units ({s.get('mentions', 0)} topic mentions) match this "
            f"question. Sentiment is {s.get('positive_pct', 0)}% positive and {s.get('negative_pct', 0)}% negative "
            f"(net score {s.get('net_sentiment_score', 0)}). Largest groups: {tops}."
        ),
        "kpi": deterministic_kpi(state),
        "takeaways": [
            {"title": f"Review {r[label_col]}", "owner": "Product",
             "detail": f"{r.get('mentions', 0)} mentions, {r.get('negative_pct', 0)}% negative."}
            for r in top
        ] if label_col else [],
    }


async def synthesize(state: WorkflowState, llm: LLM, settings: Settings) -> None:
    payload = {
        "question": state.question,
        "plan": {k: state.plan.get(k) for k in ("restated_question", "analysis_type", "filters")},
        "slice_statistics": state.slice_stats,
        "summary_table": state.table_rows[:25],
        "source_distribution": state.source_distribution,
        "customer_quotes": [
            {k: q.get(k) for k in ("customer_voice", "data_source_type", "retailer_name", "sentiment", "subtheme", "customer_rating")}
            for q in state.verbatims
        ],
    }
    try:
        out = await llm.complete_json(
            purpose="synthesizer", system=prompts.SYNTH_SYSTEM,
            user=json.dumps(payload, default=str), model=settings.openai_model_synth,
            usage=state.usage, temperature=0.2,
        )
    except LLMError as exc:
        state.warnings.append(f"Narrative generation failed ({exc}); showing a data-only summary.")
        state.synthesis = _fallback_synthesis(state)
        return

    kpi = out.get("kpi") if isinstance(out.get("kpi"), dict) else {}
    if not kpi_is_grounded(kpi, state):
        log.info("KPI not grounded in data, replacing: %s", kpi)
        kpi = deterministic_kpi(state)
    takeaways = [t for t in (out.get("takeaways") or []) if isinstance(t, dict) and t.get("title")][:4]
    state.synthesis = {
        "headline": str(out.get("headline") or "")[:120],
        "executive_summary": str(out.get("executive_summary") or _fallback_synthesis(state)["executive_summary"]),
        "kpi": kpi,
        "takeaways": takeaways or _fallback_synthesis(state)["takeaways"],
    }
