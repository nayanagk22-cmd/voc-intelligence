# VOC 360 Intelligence: Agentic Voice-of-Customer Analytics

A fully containerised, full-stack AI application that answers natural-language questions about multi-channel customer feedback (Ratings & Reviews, support chatbot transcripts, in-store field observations). An OpenAI-powered agentic workflow plans the analysis, writes and safety-checks SQL, repairs it when it fails, pulls authentic customer quotes and channel mix in parallel, and returns an executive-ready answer rendered as structured UI components.

```bash
cp .env.example .env        # add your OPENAI_API_KEY
docker compose up --build   # open http://localhost:3000
```

---

## 1. Quick start

**Prerequisites:** Docker Desktop (or Docker Engine + Compose v2) and an OpenAI API key.

1. Copy the environment template and fill in `OPENAI_API_KEY`:
   ```bash
   cp .env.example .env
   ```
2. Place the dataset in `./data/`. The app seeds `enriched_unified_data.xlsx` by default and also accepts a `.csv` with the same columns; if the configured file is absent it uses the first `.xlsx`/`.csv` it finds in `./data/`. (The repo ships the provided sample as `data/enriched_unified_data.csv`.)
3. Start everything:
   ```bash
   docker compose up --build
   ```
4. Open:
   - UI: <http://localhost:3000>
   - API docs (OpenAPI): <http://localhost:8000/docs>

On first start the backend creates the table, bulk-loads the dataset with `COPY`, creates indexes and provisions a least-privilege read-only database role. Subsequent starts skip seeding (set `FORCE_RESEED=true` to reload). To start from scratch: `docker compose down -v`.

### Services

| Service | Image | Role |
|---|---|---|
| `db` | `postgres:16-alpine` | Stores `enriched_unified_data`. Not published to the host. |
| `backend` | multi-stage `python:3.12-slim` | FastAPI + agentic workflow, data seeding, non-root user, healthcheck. |
| `frontend` | multi-stage `node:20-alpine` → `nginx:1.27-alpine` | React (Vite) UI; nginx serves static files and reverse-proxies `/api` (same origin, SSE buffering disabled). |

Startup order is enforced with healthchecks: `db` healthy → `backend` healthy (seeded, catalog loaded) → `frontend`.

---

## 2. Architecture

```
 Browser (React) ──POST /api/query/stream (SSE)──► nginx ──► FastAPI
                                                               │
                                                     VOCWorkflow (async state machine)
                                                               │
   ┌───────────────────────────────────────────────────────────┴─────────────┐
   │ 1. Intent router      regex fast-path for small talk, else gpt-4o-mini     │
   │      └─ conversational / out-of-scope ──► reply (no DB access)             │
   │ 2. Response planner   question + live data catalog ──► JSON plan:          │
   │                       filters, group_by, verbatim tone, chart type          │
   │ 3. Slice validation   filters validated, grounded to real values,          │
   │                       rendered as SQL; slice profiled (counts, dates)       │
   │                       └─ empty slice ──► honest "no data" answer            │
   │ 4. In parallel (asyncio.gather):                                            │
   │    a. NL→SQL ─► AST guard ─► read-only execution ─┐                         │
   │         ▲            self-repair (≤3 attempts) ◄──┘ error / 0 rows          │
   │         └─ all attempts fail ─► deterministic standard aggregation          │
   │    b. Verbatim extraction  2–4 quotes, same slice, diversified              │
   │    c. Source distribution  RATINGS_REVIEWS / HML / WOTS share               │
   │ 5. Narrative synthesizer  summary, KPI, takeaways (JSON)                    │
   │                           └─ KPI number not in data ─► replaced (grounding) │
   └─────────────────────────────────────────────────────────────────────────────┘
                                                               │
                         UI contract: period · summary · KPI · table · chart ·
                         quotes · source bar · takeaways · "how this was built"
```

### Why a custom async state machine (not LangGraph/CrewAI)

The workflow is a fixed DAG with one loop (SQL repair) and one fan-out (three branches). A ~150-line explicit orchestrator (`backend/app/workflow/engine.py`) makes state passing, timing, streaming events and failure handling obvious and testable, with no framework abstractions to learn or upgrade. Each node is a plain async function over a typed `WorkflowState`, so swapping in LangGraph later would be mechanical.

### Key design decisions

**One slice, many readers.** The planner never writes a WHERE clause by hand. It emits a structured filter spec (`{"column", "op", "values"}`) that is validated against a column whitelist, grounded against real catalog values (e.g. `"microsoft"` → `Microsoft`, `"Currys"` → `Currys UK`) and rendered into SQL. The LLM then writes its aggregation `FROM slice`, a CTE the guard injects from that validated spec. Verbatims, source distribution and the period banner use the same filters as parameterised queries. Result: the table, chart, quotes and channel bar are guaranteed to describe exactly the same rows.

**Parallelism earlier than the reference diagram.** Verbatims and source distribution depend only on the validated slice, not on the generated SQL, so they run concurrently *with* NL→SQL generation rather than after it. This removes one LLM round-trip from the critical path while still satisfying "parallel verbatim extraction and source distribution".

**Two-level grain is enforced in three places.**
1. Prompts state the rule explicitly: `COUNT(DISTINCT uuid)` for customers/feedback units, `COUNT(*)` for topic mentions.
2. The SQL conventions require both `mentions` and `feedback_units` columns.
3. Deterministic code (slice stats, source distribution) labels its basis, and the UI shows "feedback units" vs "topic mentions" separately. Channel share is computed on unique feedback units.

**Honest failure modes.** If a filter value doesn't exist (e.g. a brand not in the data) the slice becomes empty and the user is told so, instead of the filter being silently dropped and the answer covering the wrong data. Warnings ("matched 'Currys' to Currys UK") are surfaced in the UI.

**Hallucination guard on the KPI.** Every number in the model's KPI must exist in the computed data (exact to 1 decimal, or a whole-number rounding). Otherwise a deterministic KPI from the data replaces it.

**Graceful degradation.** SQL that fails all repair attempts falls back to a known-good aggregation; failed quote/source branches produce warnings rather than a failed answer; a failed synthesis call falls back to a data-only summary.

---

## 3. Security & SQL-injection guardrails

Defence in depth: no single layer is trusted on its own.

| Layer | Control |
|---|---|
| Input | Pydantic validation, 1,000-char question limit, history capped; nginx 16 KB body limit. |
| Filters | Column whitelist; values grounded against the catalog; deterministic queries use bind parameters (`$1`) only. |
| SQL guard (`sql_guard.py`, sqlglot AST) | Exactly one statement; must be `SELECT`/set operation; rejects DML/DDL, `SELECT INTO`, `FOR UPDATE`, `COPY`, `SET`, transaction control; blocks `pg_*`, `lo_*`, `dblink`, `current_setting`, `set_config`, XML query functions; only `slice`/`enriched_unified_data`/own CTEs, no schema-qualified tables; LIMIT enforced (≤200). |
| Database | Separate `voc_reader` role: `SELECT` on one table only, no `CREATE`, `default_transaction_read_only = on`; every query runs in a `READ ONLY` transaction with `statement_timeout` (10 s). The admin role is used only at startup for seeding. |
| Secrets | `OPENAI_API_KEY` and DB passwords from env (`.env` is git-ignored); `SecretStr` prevents accidental logging. |
| Network | Postgres is not published to the host; browser talks only to nginx (same origin). |
| Runtime | Backend runs as non-root (`uid 10001`); stack traces never returned to clients (request id instead). |
| Cost/abuse | Semaphore caps concurrent analyses (8); OpenAI timeouts and bounded retries. |

---

## 4. API

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | DB connectivity and whether an OpenAI key is configured. |
| `GET` | `/api/meta` | Dataset summary (rows, feedback units, date range, brands, retailers, themes) and example questions. |
| `POST` | `/api/query` | `{"question": str, "history": [str]}` → full JSON result. |
| `POST` | `/api/query/stream` | Same input; Server-Sent Events: `start`, `step` (per node, with status/ms/detail), then one `result`. Used by the UI. |

Result `type` is one of `analysis`, `conversation`, `empty`, `error`. An `analysis` result contains `period`, `headline`, `executive_summary`, `kpi`, `table {columns, rows}`, `chart {type, x, y}`, `verbatims`, `source_distribution`, `takeaways`, `slice_stats`, `applied_filters`, `sql {final, attempts, used_fallback}`, `trace`, `usage` and `warnings`.

Example:
```bash
curl -s localhost:8000/api/query -H 'content-type: application/json' \
  -d '{"question":"Compare customer sentiment and feedback volume across Best Buy, Amazon, and Currys UK."}' | jq '.kpi, .table.rows'
```

---

## 5. UI output contract

Every analytical answer renders these discrete components (`frontend/src/components/AnalysisView.jsx`):

1. **Analysis period banner:** observed min–max `engagement_date` of the matching slice, plus feedback units, mentions and net sentiment.
2. **Executive summary:** 2–3 sentences answering the question.
3. **Standout KPI callout:** grounded metric with its base.
4. **Summary data table:** zero NULLs (enforced in SQL conventions *and* post-processing), with an inline sentiment-mix bar.
5. **Interactive chart:** Recharts bar / grouped / stacked / pie / line with tooltips; type and axes proposed by the model and validated against the result columns.
6. **Customer verbatim quotes:** 2–4 real quotes from the slice with channel, retailer, star rating, sentiment badge, product and subtheme. Comparison questions get one quote per compared entity; perception questions get positive and negative.
7. **Source distribution:** share of unique feedback units by `RATINGS_REVIEWS` / `HML` / `WOTS`.
8. **Key findings & actionable takeaways:** 3–4 items, each with an owning team.

Also shown: a live pipeline tracker while the answer is built (the three parallel branches are drawn side by side), and a "How this answer was built" panel with the applied filters, rejected SQL attempts and their errors, executed SQL, step timings and token usage. The UI supports light and dark mode and narrow screens.

---

## 6. Benchmark queries

| Query | Expected behaviour |
|---|---|
| *What are the primary battery and power complaints reported by customers across Surface devices?* | `product_family ILIKE 'Surface%'` + `maintheme = 'Battery & Power Management'`, grouped by subtheme, ranked by negative mentions; negative quotes. |
| *Compare customer sentiment and feedback volume across Best Buy, Amazon, and Currys UK.* | `retailer_name IN (...)`, grouped by retailer, positive vs negative %; one quote per retailer; grouped bar chart. |
| *How do customers perceive the Copilot AI integration and what are the main positive vs negative sentiments?* | `subtheme ILIKE '%Copilot%'`; sentiment breakdown; two positive + two negative quotes. |
| *Hi, what can this VOC platform do?* | Regex fast path: capability summary, **no LLM call and no DB access**. |

---

## 7. Tests

```bash
# unit tests (SQL guard, filter grounding, KPI grounding): no DB needed
cd backend && pip install -r requirements-dev.txt && pytest

# full suite inside Docker, including end-to-end workflow tests against Postgres
docker compose up -d db
docker compose run --rm -e VOC_TEST_DB=1 backend sh -c \
  "pip install --user --no-index --find-links=/wheels -r requirements-dev.txt && python -m pytest -q"
```

The integration tests use a scripted fake LLM to verify, against real data:
- the self-repair loop (broken SQL → repaired);
- catalog grounding (`"battery & power management"` → canonical value);
- the zero-NULL table;
- quotes strictly inside the slice and of the requested tone;
- source distribution reconciling with `COUNT(DISTINCT uuid)`;
- table mentions summing to slice mentions (grain check);
- hallucinated KPI replacement;
- greeting fast path making zero LLM calls;
- destructive SQL blocked with fallback, and the reader role being unable to write.

---

## 8. Project layout

```
.
├── docker-compose.yml
├── .env.example
├── data/                         # dataset mounted read-only into the backend
├── backend/
│   ├── Dockerfile                # multi-stage, non-root, healthcheck
│   ├── app/
│   │   ├── main.py               # FastAPI app, lifespan (seed → catalog → workflow), SSE
│   │   ├── config.py             # env-driven settings
│   │   ├── db.py                 # admin vs read-only pools, READ ONLY transactions
│   │   ├── seed.py               # xlsx/csv → Postgres COPY, indexes, reader role
│   │   ├── catalog.py            # real column values for prompt grounding
│   │   ├── filters.py            # filter spec validation + parameterised/literal rendering
│   │   ├── sql_guard.py          # sqlglot AST guard + slice CTE injection
│   │   ├── llm.py                # OpenAI JSON-mode wrapper, usage tracking
│   │   ├── prompts.py
│   │   └── workflow/
│   │       ├── state.py          # typed WorkflowState
│   │       ├── nodes.py          # router, planner, slice, SQL+repair, verbatims, sources, synth
│   │       └── engine.py         # orchestrator, parallel branches, events, result contract
│   └── tests/
└── frontend/
    ├── Dockerfile                # node build → nginx
    ├── nginx.conf                # static + /api reverse proxy (SSE unbuffered)
    └── src/                      # React components per UI block
```

---

## 9. Configuration

All settings are environment variables (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | – (required) | OpenAI key. |
| `OPENAI_MODEL_FAST` | `gpt-4o-mini` | Router, planner, SQL generation/repair. |
| `OPENAI_MODEL_SYNTH` | `gpt-4o-mini` | Narrative synthesis (`gpt-4o` for richer prose). |
| `POSTGRES_*` | see file | Admin connection (seeding only). |
| `READONLY_DB_USER` / `READONLY_DB_PASSWORD` | `voc_reader` | Role used for all analytical queries. |
| `DATA_FILE_NAME` | `enriched_unified_data.xlsx` | File in `./data` to seed. |
| `FORCE_RESEED` | `false` | Reload data on startup. |
| `MAX_SQL_ATTEMPTS` | `3` | Generation + repair attempts before fallback. |
| `SQL_STATEMENT_TIMEOUT_MS` | `10000` | Per-query timeout. |

---

## 10. Limitations & next steps

- **Conversation memory** is light: the last three analytical questions are passed to the planner to resolve follow-ups ("what about Apple?"); there is no server-side session store.
- **Evaluation:** add a golden-question suite that asserts generated filters/SQL for a fixed set of questions, run in CI against the real model.
- **Observability:** step timings and token usage are returned per request; production would export them as OpenTelemetry traces and add per-user rate limiting at the gateway.
- **Caching:** identical questions could be served from a cache keyed on the normalised plan.
- **Scale:** at millions of rows, pre-aggregated materialised views per theme/brand/month would keep latency flat.
