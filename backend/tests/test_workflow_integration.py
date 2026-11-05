"""End-to-end workflow test against a real Postgres, with a scripted fake LLM.

Runs when VOC_TEST_DB=1 and POSTGRES_* env vars point at a database
(e.g. `docker compose run --rm -e VOC_TEST_DB=1 backend pytest`).
"""

import os

import pytest

from app.catalog import load_catalog
from app.config import Settings
from app.db import Database
from app.seed import ensure_seeded
from app.workflow.engine import VOCWorkflow

pytestmark = pytest.mark.skipif(os.getenv("VOC_TEST_DB") != "1", reason="needs Postgres (VOC_TEST_DB=1)")


class ScriptedLLM:
    """Returns canned JSON per workflow purpose; records calls."""

    enabled = True

    def __init__(self, script):
        self.script, self.calls = script, []

    async def complete_json(self, *, purpose, system, user, model=None, usage=None, temperature=0.0):
        self.calls.append(purpose)
        return self.script[purpose]


PLAN = {
    "restated_question": "Battery & power complaints for Surface devices",
    "analysis_type": "pain_points",
    "filters": [
        {"column": "product_family", "op": "ilike_any", "values": ["Surface%"]},
        {"column": "maintheme", "op": "in", "values": ["battery & power management"]},
    ],
    "group_by": ["subtheme"],
    "verbatim_sentiment": "Negative",
    "verbatim_diversity_column": None,
    "chart_type": "bar",
    "sql_goal": "Volume and sentiment by subtheme",
}
BROKEN_SQL = {"sql": "SELECT subthemes, COUNT(*) FROM slice GROUP BY 1", "chart": {}}
GOOD_SQL = {
    "sql": (
        "SELECT subtheme, COUNT(*) AS mentions, COUNT(DISTINCT uuid) AS feedback_units, "
        "COUNT(*) FILTER (WHERE sentiment = 'Negative') AS negative_mentions, "
        "ROUND(100.0 * COUNT(*) FILTER (WHERE sentiment = 'Negative') / NULLIF(COUNT(*), 0), 1) AS negative_pct "
        "FROM slice GROUP BY 1 ORDER BY negative_mentions DESC"
    ),
    "chart": {"type": "bar", "x": "subtheme", "y": ["negative_mentions"]},
}
SYNTH = {
    "headline": "Standby drain leads Surface battery complaints",
    "executive_summary": "Summary.",
    "kpi": {"value": "97.3%", "label": "made-up number", "context": "hallucinated"},
    "takeaways": [{"title": "Fix standby", "detail": "x", "owner": "Engineering"}] * 3,
}


@pytest.fixture
async def db():
    settings = Settings(data_file=os.getenv("DATA_FILE", "../data/enriched_unified_data.csv"), data_dir=os.getenv("DATA_DIR", "../data"))
    database = Database(settings)
    await database.connect_admin(retries=3, delay_s=1)
    await ensure_seeded(database, settings)
    await database.connect_reader()
    yield database, settings
    await database.close()


async def test_analytical_flow_with_self_repair_and_grounding(db):
    database, settings = db
    catalog = await load_catalog(database)
    llm = ScriptedLLM({
        "router": {"route": "analytical", "reply": ""},
        "planner": PLAN,
        "sql_generation": BROKEN_SQL,
        "sql_repair_1": GOOD_SQL,
        "synthesizer": SYNTH,
    })
    events = []

    async def emit(e):
        events.append(e)

    result = await VOCWorkflow(database, llm, catalog, settings).run(
        "What are the primary battery and power complaints across Surface devices?", emit=emit
    )
    assert result["type"] == "analysis", result.get("message")
    # self-repair happened
    attempts = result["sql"]["attempts"]
    assert len(attempts) == 2 and attempts[0]["error"] and attempts[1]["error"] is None
    # slice grounding: lower-case theme value canonicalised
    assert result["applied_filters"][1]["values"] == ["Battery & Power Management"]
    # zero NULL contract
    assert all(v is not None for row in result["table"]["rows"] for v in row.values())
    # quotes belong to the slice and are negative
    assert 2 <= len(result["verbatims"]) <= 4
    assert all(q["product_family"].startswith("Surface") for q in result["verbatims"])
    assert all(q["maintheme"] == "Battery & Power Management" for q in result["verbatims"])
    assert all(q["sentiment"] == "Negative" for q in result["verbatims"])
    # source distribution sums to the slice's unique feedback units
    sd = result["source_distribution"]
    assert sd["total_feedback_units"] == result["slice_stats"]["feedback_units"]
    assert {c["channel"] for c in sd["channels"]} == {"RATINGS_REVIEWS", "HML", "WOTS"}
    # hallucinated KPI was replaced by a grounded one
    assert result["kpi"]["value"] != "97.3%"
    # table counts reconcile with the slice (two-level grain)
    assert sum(r["mentions"] for r in result["table"]["rows"]) == result["slice_stats"]["mentions"]
    assert result["period"]["start"] <= result["period"]["end"]
    assert any(e["type"] == "step" and e["step"] == "sql" and e["status"] == "done" for e in events)


async def test_greeting_fast_path_skips_llm_and_db(db):
    database, settings = db
    catalog = await load_catalog(database)
    llm = ScriptedLLM({})
    result = await VOCWorkflow(database, llm, catalog, settings).run("Hi, what can this VOC platform do?")
    assert result["type"] == "conversation"
    assert llm.calls == []


async def test_guard_blocks_destructive_sql_then_falls_back(db):
    database, settings = db
    catalog = await load_catalog(database)
    evil = {"sql": "DROP TABLE enriched_unified_data", "chart": {}}
    llm = ScriptedLLM({
        "router": {"route": "analytical"}, "planner": PLAN,
        "sql_generation": evil, "sql_repair_1": evil, "sql_repair_2": evil, "synthesizer": SYNTH,
    })
    result = await VOCWorkflow(database, llm, catalog, settings).run("battery complaints on surface")
    assert result["type"] == "analysis"
    assert result["sql"]["used_fallback"] is True
    assert all("SQL guard" in a["error"] for a in result["sql"]["attempts"])
    assert (await database.fetch_readonly("SELECT COUNT(*) AS n FROM enriched_unified_data"))[0]["n"] > 0


async def test_reader_role_cannot_write(db):
    database, _ = db
    with pytest.raises(Exception):
        await database.fetch_readonly("DELETE FROM enriched_unified_data")
