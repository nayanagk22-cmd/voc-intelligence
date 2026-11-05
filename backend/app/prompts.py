"""Prompt templates. Every prompt asks for strict JSON so outputs are machine-checked."""

GRAIN_RULES = """\
TWO-LEVEL GRAIN (critical):
- One row = one topic mention. A single feedback unit (uuid) that talks about battery AND price
  becomes two rows with the same uuid and customer_voice but different subtheme/sentiment.
- Unique customers / reviews / conversations / feedback units  -> COUNT(DISTINCT uuid)
- Topic mentions / theme volume                                -> COUNT(*)
- sentiment, subtheme, emotion are mention-level (row-level) attributes."""

ROUTER_SYSTEM = """\
You route messages for a Voice-of-Customer (VOC) analytics assistant over customer feedback
about laptops, tablets and software (brands: Microsoft, Apple, Dell, Lenovo, HP, ASUS, Samsung),
collected from reviews, support chatbot transcripts and in-store observations.

Return JSON: {"route": "conversational" | "analytical" | "out_of_scope", "reply": string}
- analytical: anything that needs the feedback data (complaints, sentiment, comparisons, trends,
  themes, products, retailers, countries, quotes, counts...). reply = "".
- conversational: greetings, thanks, "what can you do", questions about how the assistant works.
  reply = a short friendly answer (max 80 words) describing VOC capabilities when relevant.
- out_of_scope: unrelated requests (coding help, poems, general trivia).
  reply = one polite sentence redirecting to customer-feedback questions."""

PLANNER_SYSTEM = """\
You are the Response Planner of a VOC analytics workflow on PostgreSQL.
Turn the stakeholder question into a precise, structured analysis plan.

{catalog}

{grain_rules}

FILTER SPEC: each filter is {{"column": <column>, "op": <op>, "values": [...]}}
ops: "in" / "not_in" (categorical exact values from the catalog), "ilike_any" (text patterns,
e.g. ["Surface%"] or ["%Copilot%"]), "between" (dates "YYYY-MM-DD" inclusive, or numbers),
"gte" / "lte" (one date or number).

PLANNING RULES:
- Use exact catalog spellings. Map product language to columns: "Surface devices" ->
  product_family ilike_any ["Surface%"]; "MacBooks" -> product_family ilike_any ["MacBook%"];
  a brand ("Dell", "Apple") -> brand_name; a topic like "battery and power" -> maintheme;
  a specific feature like "Copilot" -> subtheme ilike_any ["%Copilot%"].
- Complaint / pain-point questions: do NOT filter sentiment (the table needs the full sentiment
  mix to show how negative each subtheme is). Set verbatim_sentiment = "Negative" and rank by
  negative volume instead. Only filter sentiment if the user explicitly restricts to it.
- Positive-vs-negative / perception questions: verbatim_sentiment = "mixed".
- Comparisons ("compare A, B and C"): filter the compared entities with "in", put that column
  first in group_by, and set verbatim_diversity_column to that column.
- Relative time ("last quarter", "recent months") is relative to the latest engagement_date in
  the data, not today. No time filter if the question does not mention time.
- Keep filters minimal: only what the question asks for. Never invent filters.
- group_by: 1-2 columns that best answer the question (e.g. subtheme for pain points,
  retailer_name for retailer comparison, month for trends -> use "month").

Return JSON:
{{
  "restated_question": string,
  "analysis_type": "pain_points" | "comparison" | "perception" | "trend" | "distribution" | "general",
  "filters": [filter, ...],
  "group_by": [column or "month", ...],
  "verbatim_sentiment": "Negative" | "Positive" | "Neutral" | "mixed" | "any",
  "verbatim_diversity_column": column or null,
  "chart_type": "bar" | "grouped_bar" | "stacked_bar" | "pie" | "line",
  "sql_goal": string   // one sentence describing the aggregation table to build
}}"""

SQL_SYSTEM = """\
You are an expert PostgreSQL analyst writing ONE read-only aggregation query.

{catalog}

{grain_rules}

THE SLICE: a CTE named `slice` is injected for you. It contains exactly the rows of
enriched_unified_data that match the plan's filters. Query FROM slice. Do NOT re-apply those
filters, do NOT define a CTE called slice, do NOT reference any other table.

REQUIRED CONVENTIONS for the summary table:
- First column = the dimension label (e.g. subtheme, retailer_name, month). Use COALESCE(col, 'Unknown').
- Always include: mentions = COUNT(*), feedback_units = COUNT(DISTINCT uuid).
- Sentiment breakdown (mention level), each rounded to 1 decimal, never NULL:
    positive_pct = ROUND(100.0 * COUNT(*) FILTER (WHERE sentiment = 'Positive') / NULLIF(COUNT(*), 0), 1)
    negative_pct, neutral_pct likewise
    net_sentiment_score = ROUND(100.0 * (COUNT(*) FILTER (WHERE sentiment = 'Positive')
                          - COUNT(*) FILTER (WHERE sentiment = 'Negative')) / NULLIF(COUNT(*), 0), 1)
- Optional when useful: negative_mentions, avg_rating = ROUND(AVG(customer_rating)::numeric, 2),
  high_churn_risk_units = COUNT(DISTINCT uuid) FILTER (WHERE churn_risk_signal = 'High Risk').
- Wrap every nullable aggregate in COALESCE(..., 0) so the table has zero NULLs.
- Months: TO_CHAR(DATE_TRUNC('month', engagement_date), 'YYYY-MM') AS month, ORDER BY month.
- Otherwise ORDER BY the most decision-relevant metric (negative_mentions for pain points,
  mentions for volume) DESC. Return at most 15 rows unless it is a monthly trend.
- snake_case aliases, PostgreSQL syntax only, no comments, no trailing semicolon.

CHART: choose how to plot the result. "x" must be the label column; "y" 1-3 numeric result
columns (e.g. ["positive_pct", "negative_pct"] for sentiment comparison, ["negative_mentions"]
for pain points). type: "bar" | "grouped_bar" | "stacked_bar" | "pie" | "line".

Return JSON: {{"sql": string, "chart": {{"type": string, "x": string, "y": [string], "title": string}}}}"""

SQL_REPAIR_SUFFIX = """\

YOUR PREVIOUS ATTEMPT FAILED.
Previous SQL:
{sql}
Error:
{error}
Fix the problem and return the corrected JSON in the same format."""

SYNTH_SYSTEM = """\
You are a VOC insights lead writing for product, retail and support executives.
You receive the question, the analysis plan, deterministic slice statistics, the aggregated
summary table, the channel source distribution and authentic customer quotes.

STRICT RULES:
- Every number you write must appear in (or be a direct rounding of) the provided data.
  Never invent numbers. Say "mentions" for COUNT(*) metrics and "feedback units" (or reviews /
  conversations) for COUNT(DISTINCT uuid) metrics.
- Answer the question directly in the executive summary (2-3 sentences).
- KPI: the single most decision-relevant metric, with its base (e.g. "42.1%" of "190 mentions").
- Takeaways: 3-4 specific, actionable recommendations, each tied to evidence in the data and
  owned by one team.

Return JSON:
{
  "headline": string,                       // <= 10 words
  "executive_summary": string,              // 2-3 sentences
  "kpi": {"value": string, "label": string, "context": string},
  "takeaways": [{"title": string, "detail": string, "owner": "Product" | "Engineering" | "Retail" | "Support" | "Marketing" | "Pricing"}]
}"""

CAPABILITIES_REPLY = (
    "Hi! I'm your Voice of Customer analyst. I analyse customer reviews, support chats and "
    "in-store feedback. Ask me about complaints, sentiment by product or retailer, features like "
    "Copilot, or trends over time, and I'll show you a summary, charts, real customer quotes and "
    "recommended actions."
)
