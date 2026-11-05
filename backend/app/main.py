"""FastAPI entrypoint.

Startup (lifespan): connect as admin -> seed data + provision read-only role ->
open read-only pool -> load catalog -> build workflow.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from .catalog import load_catalog
from .config import get_settings
from .db import Database
from .llm import OpenAILLM
from .seed import ensure_seeded
from .workflow.engine import VOCWorkflow

settings = get_settings()
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("voc")

EXAMPLE_QUESTIONS = [
    "What are the primary battery and power complaints reported by customers across Surface devices?",
    "Compare customer sentiment and feedback volume across Best Buy, Amazon, and Currys UK.",
    "How do customers perceive the Copilot AI integration and what are the main positive vs negative sentiments?",
    "How has negative sentiment on thermals trended month by month?",
    "Which retailers have the highest churn risk in support conversations?",
]

# Caps concurrent analyses (each costs several OpenAI calls).
_CONCURRENCY = asyncio.Semaphore(8)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database(settings)
    await db.connect_admin()
    rows = await ensure_seeded(db, settings)
    await db.connect_reader()
    catalog = await load_catalog(db)
    llm = OpenAILLM(settings)
    if not llm.enabled:
        log.warning("OPENAI_API_KEY is empty: greetings work, analytical questions will return an error")
    app.state.db, app.state.catalog, app.state.llm = db, catalog, llm
    app.state.workflow = VOCWorkflow(db, llm, catalog, settings)
    log.info("VOC backend ready: %d rows, %d feedback units", rows, catalog.feedback_units)
    yield
    await db.close()


app = FastAPI(title="VOC 360 Intelligence API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)
    history: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("question")
    @classmethod
    def _limit_question(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question must not be empty")
        if len(v) > settings.max_question_chars:
            raise ValueError(f"question must be at most {settings.max_question_chars} characters")
        return v

    @field_validator("history")
    @classmethod
    def _limit_history(cls, v: list[str]) -> list[str]:
        return [h.strip()[:500] for h in v if h and h.strip()]


@app.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    db_ok = await request.app.state.db.ping()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "llm_configured": request.app.state.llm.enabled,
    }


@app.get("/api/meta")
async def meta(request: Request) -> dict[str, Any]:
    cat = request.app.state.catalog
    return {
        "rows": cat.row_count,
        "feedback_units": cat.feedback_units,
        "date_range": {"start": cat.min_date, "end": cat.max_date},
        "brands": cat.values.get("brand_name", []),
        "channels": cat.values.get("data_source_type", []),
        "retailers": cat.values.get("retailer_name", []),
        "themes": list(cat.subthemes_by_theme.keys()),
        "example_questions": EXAMPLE_QUESTIONS,
        "models": {"fast": settings.openai_model_fast, "synthesis": settings.openai_model_synth},
        "llm_configured": request.app.state.llm.enabled,
    }


@app.post("/api/query")
async def query(body: QueryRequest, request: Request) -> dict[str, Any]:
    async with _CONCURRENCY:
        return await request.app.state.workflow.run(body.question, body.history)


@app.post("/api/query/stream")
async def query_stream(body: QueryRequest, request: Request) -> StreamingResponse:
    """Server-Sent Events: `step` events while the workflow runs, then one `result`."""
    workflow: VOCWorkflow = request.app.state.workflow
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def emit(event: dict[str, Any]) -> None:
        await queue.put(event)

    async def runner() -> None:
        async with _CONCURRENCY:
            result = await workflow.run(body.question, body.history, emit=emit)
        await queue.put({"type": "result", "data": result})
        await queue.put(None)

    async def events():
        task = asyncio.create_task(runner())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if await request.is_disconnected():
                    break
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

