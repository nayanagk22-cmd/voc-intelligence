"""Typed state passed between workflow nodes."""

from __future__ import annotations

import uuid as uuid_lib
from dataclasses import dataclass, field
from typing import Any

from ..filters import Filter
from ..llm import UsageTracker


@dataclass
class SQLAttempt:
    attempt: int
    sql: str
    error: str | None = None
    row_count: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"attempt": self.attempt, "sql": self.sql, "error": self.error, "row_count": self.row_count}


@dataclass
class TraceStep:
    step: str
    label: str
    status: str = "pending"  # pending | running | done | skipped | error
    ms: int = 0
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"step": self.step, "label": self.label, "status": self.status, "ms": self.ms, "detail": self.detail}


@dataclass
class WorkflowState:
    question: str
    history: list[str] = field(default_factory=list)
    request_id: str = field(default_factory=lambda: uuid_lib.uuid4().hex[:12])

    # routing
    route: str = ""
    reply: str = ""

    # planning
    plan: dict[str, Any] = field(default_factory=dict)
    filters: list[Filter] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    where_sql: str = "TRUE"            # parameterised ($n)
    where_params: list[Any] = field(default_factory=list)
    where_literal: str = "TRUE"        # literal, used for the slice CTE

    # data
    slice_stats: dict[str, Any] = field(default_factory=dict)
    sql_attempts: list[SQLAttempt] = field(default_factory=list)
    final_sql: str = ""
    used_fallback_sql: bool = False
    table_columns: list[dict[str, str]] = field(default_factory=list)
    table_rows: list[dict[str, Any]] = field(default_factory=list)
    chart: dict[str, Any] = field(default_factory=dict)
    verbatims: list[dict[str, Any]] = field(default_factory=list)
    source_distribution: dict[str, Any] = field(default_factory=dict)

    # narrative
    synthesis: dict[str, Any] = field(default_factory=dict)

    # observability
    trace: list[TraceStep] = field(default_factory=list)
    usage: UsageTracker = field(default_factory=UsageTracker)
