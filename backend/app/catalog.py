"""Data catalog: real column values loaded once at startup.

The catalog is injected into the planner / SQL prompts so the model filters on
values that actually exist (e.g. "Surface devices" -> product_family values that
start with "Surface"), and it is used to validate/normalise planner filters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import TABLE_NAME
from .db import Database

CATEGORICAL_COLUMNS = [
    "data_source_type", "brand_name", "product_family", "product_type", "retailer_name",
    "country", "region", "maintheme", "subtheme", "sentiment", "emotion", "customer_intent",
    "churn_risk_signal", "price_perception", "urgency_level", "shopping_journey_stage",
]
NUMERIC_COLUMNS = ["sentiment_intensity", "sentiment_score", "emotional_intensity", "customer_rating"]
DATE_COLUMNS = ["engagement_date", "created_at"]
FREE_TEXT_COLUMNS = [
    "uuid", "customer_voice_id", "customer_voice", "product_name", "explanation",
    "product_aspects", "urgency_reason",
]
ALL_COLUMNS = CATEGORICAL_COLUMNS + NUMERIC_COLUMNS + DATE_COLUMNS + FREE_TEXT_COLUMNS

COLUMN_DOCS = {
    "uuid": "feedback unit id; several rows share a uuid when one interaction mentions several subthemes",
    "data_source_type": "channel: RATINGS_REVIEWS (reviews), HML (chatbot/support), WOTS (in-store field observations)",
    "engagement_date": "timestamp of the customer interaction (primary time axis)",
    "created_at": "ingestion timestamp (do not use for analysis periods)",
    "customer_voice_id": "source feedback id",
    "customer_voice": "raw customer verbatim",
    "product_name": "full SKU title",
    "product_family": "product family",
    "product_type": "hardware or software",
    "brand_name": "brand",
    "retailer_name": "retailer / channel partner",
    "country": "country",
    "region": "Americas, EMEA, Asia",
    "maintheme": "high-level topic",
    "subtheme": "detailed topic (child of maintheme)",
    "explanation": "LLM rationale for the topic classification",
    "sentiment": "Positive, Negative, Neutral",
    "sentiment_intensity": "1 (mild) .. 5 (intense)",
    "sentiment_score": "-1 negative, 0 neutral, +1 positive",
    "emotion": "detected emotion",
    "emotional_intensity": "1 .. 5",
    "customer_intent": "intent of the customer",
    "product_aspects": "text like ['Battery', 'Power']",
    "churn_risk_signal": "churn risk tier",
    "price_perception": "value perception",
    "urgency_level": "urgency tier",
    "urgency_reason": "why that urgency",
    "shopping_journey_stage": "Discover, Learn, Buy, Engage",
    "customer_rating": "1-5 stars, may be NULL (e.g. chatbot)",
}


@dataclass
class Catalog:
    row_count: int = 0
    feedback_units: int = 0
    min_date: str = ""
    max_date: str = ""
    values: dict[str, list[str]] = field(default_factory=dict)
    subthemes_by_theme: dict[str, list[str]] = field(default_factory=dict)
    families_by_brand: dict[str, list[str]] = field(default_factory=dict)

    def canonical(self, column: str, value: str) -> str | None:
        """Case-insensitive match of a value to its canonical spelling."""
        lookup = {v.lower(): v for v in self.values.get(column, [])}
        return lookup.get(str(value).strip().lower())

    def prompt_block(self) -> str:
        lines = [
            f"TABLE {TABLE_NAME}: {self.row_count} rows (topic mentions), "
            f"{self.feedback_units} distinct feedback units (uuid). "
            f"engagement_date range: {self.min_date} to {self.max_date}.",
            "",
            "COLUMNS:",
        ]
        for col in ALL_COLUMNS:
            kind = (
                "categorical" if col in CATEGORICAL_COLUMNS else
                "integer" if col in NUMERIC_COLUMNS else
                "timestamp" if col in DATE_COLUMNS else "text"
            )
            lines.append(f"- {col} ({kind}): {COLUMN_DOCS[col]}")
        lines += ["", "EXACT CATEGORICAL VALUES (use these spellings):"]
        for col in CATEGORICAL_COLUMNS:
            if col in ("subtheme", "product_family"):
                continue
            lines.append(f"- {col}: {', '.join(self.values.get(col, []))}")
        lines.append("- product_family by brand_name:")
        for brand, fams in self.families_by_brand.items():
            lines.append(f"    {brand}: {', '.join(fams)}")
        lines.append("- subtheme by maintheme:")
        for theme, subs in self.subthemes_by_theme.items():
            lines.append(f"    {theme}: {', '.join(subs)}")
        return "\n".join(lines)


async def load_catalog(db: Database) -> Catalog:
    cat = Catalog()
    summary = await db.fetch_readonly(
        f"SELECT COUNT(*) AS n, COUNT(DISTINCT uuid) AS u, "
        f"MIN(engagement_date)::date AS lo, MAX(engagement_date)::date AS hi FROM {TABLE_NAME}"
    )
    s = summary[0]
    cat.row_count, cat.feedback_units = s["n"], s["u"]
    cat.min_date, cat.max_date = str(s["lo"]), str(s["hi"])

    for col in CATEGORICAL_COLUMNS:
        rows = await db.fetch_readonly(
            f"SELECT {col} AS v FROM {TABLE_NAME} WHERE {col} IS NOT NULL "
            f"GROUP BY {col} ORDER BY COUNT(*) DESC"
        )
        cat.values[col] = [r["v"] for r in rows]

    for r in await db.fetch_readonly(
        f"SELECT maintheme, subtheme FROM {TABLE_NAME} WHERE maintheme IS NOT NULL "
        f"AND subtheme IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2"
    ):
        cat.subthemes_by_theme.setdefault(r["maintheme"], []).append(r["subtheme"])

    for r in await db.fetch_readonly(
        f"SELECT brand_name, product_family FROM {TABLE_NAME} WHERE brand_name IS NOT NULL "
        f"AND product_family IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2"
    ):
        cat.families_by_brand.setdefault(r["brand_name"], []).append(r["product_family"])
    return cat
