from app.catalog import Catalog
from app.filters import normalize_filters, render_where

CAT = Catalog(values={
    "brand_name": ["Microsoft", "Apple"],
    "retailer_name": ["Best Buy", "Amazon", "Amazon EU", "Currys UK"],
    "sentiment": ["Positive", "Negative", "Neutral"],
})


def test_canonicalises_case_and_whitelists_columns():
    f, w = normalize_filters([
        {"column": "brand_name", "op": "in", "values": ["microsoft"]},
        {"column": "password; DROP", "op": "in", "values": ["x"]},
    ], CAT)
    assert [x.values for x in f] == [["Microsoft"]]
    assert any("unknown column" in m for m in w)


def test_unknown_value_produces_empty_slice_not_silent_broadening():
    f, w = normalize_filters([{"column": "brand_name", "op": "in", "values": ["Nokia"]}], CAT)
    sql, params = render_where(f)
    assert sql == "FALSE" and params == []
    assert w


def test_partial_match_and_parameterised_render():
    f, _ = normalize_filters([
        {"column": "retailer_name", "op": "in", "values": ["Best Buy", "Currys"]},
        {"column": "engagement_date", "op": "between", "values": ["2026-01-01", "2026-03-31"]},
        {"column": "subtheme", "op": "ilike_any", "values": ["Copilot"]},
    ], CAT)
    sql, params = render_where(f)
    assert "$1::text[]" in sql and "$2::timestamp" in sql and "$3::timestamp" in sql
    assert params[0] == ["Best Buy", "Currys UK"]
    assert str(params[2]).startswith("2026-04-01")  # inclusive end date
    assert params[3] == ["%Copilot%"]


def test_literal_render_escapes_quotes():
    f, _ = normalize_filters([{"column": "customer_voice", "op": "ilike_any", "values": ["it's bad"]}], CAT)
    sql, _ = render_where(f, parameterised=False)
    assert "it''s bad" in sql
