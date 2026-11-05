"""Custom async state machine that orchestrates the VOC workflow.

    route ──► conversational ──► reply (no DB, often no LLM)
      │
      └─► plan ──► profile slice ──┬─► NL→SQL ⟲ self-repair ─┐
                                   ├─► verbatim extraction    ├─► synthesize ──► UI contract
                                   └─► source distribution  ──┘

The three branches after slice profiling only depend on the validated slice, so
they run concurrently with ``asyncio.gather``. Every step emits progress events
(used by the SSE endpoint) and is timed for the UI trace.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..catalog import Catalog
from ..config import Settings
from ..db import Database
from ..llm import LLM, LLMError
from . import nodes
from .state import TraceStep, WorkflowState

log = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], Awaitable[None]]

STEPS = [
    ("route", "Intent routing"),
    ("plan", "Response planning"),
    ("slice", "Slice validation"),
    ("sql", "NL-to-SQL + execution"),
    ("verbatims", "Verbatim extraction"),
    ("sources", "Source distribution"),
    ("synthesize", "Narrative synthesis"),
]


async def _noop(_: dict[str, Any]) -> None:
    return None


class VOCWorkflow:
    def __init__(self, db: Database, llm: LLM, catalog: Catalog, settings: Settings) -> None:
        self.db, self.llm, self.catalog, self.settings = db, llm, catalog, settings

    async def run(self, question: str, history: list[str] | None = None, emit: Emit | None = None) -> dict[str, Any]:
        emit = emit or _noop
        state = WorkflowState(question=question.strip(), history=history or [])
        state.trace = [TraceStep(k, label) for k, label in STEPS]
        await emit({"type": "start", "request_id": state.request_id, "steps": [t.as_dict() for t in state.trace]})

        try:
            await self._step(state, emit, "route", nodes.route(state, self.llm),
                             lambda: f"route = {state.route}")
            if state.route != "analytical":
                for t in state.trace[1:]:
                    t.status = "skipped"
                return self._conversation_result(state)

            await self._step(state, emit, "plan", nodes.plan(state, self.llm, self.catalog),
                             lambda: f"{len(state.filters)} filter(s), group by {', '.join(state.plan['group_by'])}")
            await self._step(state, emit, "slice", nodes.profile_slice(state, self.db),
                             lambda: f"{state.slice_stats['mentions']} mentions / {state.slice_stats['feedback_units']} feedback units")

            if not state.slice_stats.get("mentions"):
                for t in state.trace[3:]:
                    t.status = "skipped"
                return self._empty_result(state)

            sql_res, verb_res, src_res = await asyncio.gather(
                self._step(state, emit, "sql",
                           nodes.generate_and_execute_sql(state, self.llm, self.db, self.catalog, self.settings),
                           lambda: f"{len(state.table_rows)} rows after {len(state.sql_attempts)} attempt(s)"
                                   + (" (fallback)" if state.used_fallback_sql else "")),
                self._step(state, emit, "verbatims", nodes.extract_verbatims(state, self.db),
                           lambda: f"{len(state.verbatims)} quotes"),
                self._step(state, emit, "sources", nodes.source_distribution(state, self.db),
                           lambda: f"{state.source_distribution['total_feedback_units']} feedback units"),
                return_exceptions=True,
            )
            if isinstance(sql_res, BaseException):  # the table is the core of the answer
                raise sql_res
            if isinstance(verb_res, BaseException):  # side branches degrade gracefully
                state.warnings.append("Customer quotes could not be loaded for this question.")
            if isinstance(src_res, BaseException):
                state.warnings.append("Source distribution could not be computed for this question.")
            await self._step(state, emit, "synthesize", nodes.synthesize(state, self.llm, self.settings),
                             lambda: "narrative ready")
            return self._analysis_result(state)
        except LLMError as exc:
            log.warning("[%s] LLM failure: %s", state.request_id, exc)
            return self._error_result(state, str(exc))
        except Exception:  # noqa: BLE001 - never leak stack traces to the client
            log.exception("[%s] workflow failed", state.request_id)
            return self._error_result(state, "The analysis failed unexpectedly. Check the backend logs for request "
                                             f"{state.request_id}.")

    async def _step(self, state: WorkflowState, emit: Emit, key: str, coro, detail: Callable[[], str]) -> None:
        step = next(t for t in state.trace if t.step == key)
        step.status = "running"
        await emit({"type": "step", **step.as_dict()})
        started = time.perf_counter()
        try:
            await coro
        except Exception as exc:
            step.status, step.ms = "error", int((time.perf_counter() - started) * 1000)
            step.detail = str(exc)[:200]
            await emit({"type": "step", **step.as_dict()})
            raise
        step.status, step.ms = "done", int((time.perf_counter() - started) * 1000)
        step.detail = detail()
        await emit({"type": "step", **step.as_dict()})

    # ------------------------------------------------------------------ result builders

    def _base(self, state: WorkflowState, kind: str) -> dict[str, Any]:
        return {
            "type": kind,
            "request_id": state.request_id,
            "question": state.question,
            "trace": [t.as_dict() for t in state.trace],
            "usage": state.usage.summary(),
            "warnings": state.warnings,
        }

    def _conversation_result(self, state: WorkflowState) -> dict[str, Any]:
        return {**self._base(state, "conversation"), "route": state.route, "message": state.reply}

    def _empty_result(self, state: WorkflowState) -> dict[str, Any]:
        return {
            **self._base(state, "empty"),
            "message": "No customer feedback matches this question. Try widening the product, retailer or date range.",
            "applied_filters": state.plan.get("filters", []),
            "plan": state.plan,
        }

    def _error_result(self, state: WorkflowState, message: str) -> dict[str, Any]:
        return {**self._base(state, "error"), "message": message}

    def _analysis_result(self, state: WorkflowState) -> dict[str, Any]:
        s = state.slice_stats
        return {
            **self._base(state, "analysis"),
            "period": {"start": s.get("start_date"), "end": s.get("end_date")},
            "headline": state.synthesis.get("headline", ""),
            "executive_summary": state.synthesis.get("executive_summary", ""),
            "kpi": state.synthesis.get("kpi", {}),
            "table": {"columns": state.table_columns, "rows": state.table_rows},
            "chart": state.chart,
            "verbatims": state.verbatims,
            "source_distribution": state.source_distribution,
            "takeaways": state.synthesis.get("takeaways", []),
            "slice_stats": s,
            "applied_filters": state.plan.get("filters", []),
            "plan": state.plan,
            "sql": {
                "final": state.final_sql,
                "used_fallback": state.used_fallback_sql,
                "attempts": [a.as_dict() for a in state.sql_attempts],
            },
        }
