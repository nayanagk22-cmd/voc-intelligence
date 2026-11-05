import pytest

from app.sql_guard import SQLGuardError, validate_and_compose

WHERE = "brand_name = ANY(ARRAY['Microsoft']::text[])"


def test_injects_slice_cte_and_limit():
    sql = validate_and_compose("SELECT subtheme, COUNT(*) AS mentions FROM slice GROUP BY 1", WHERE, 200)
    assert sql.startswith("WITH slice AS (SELECT * FROM enriched_unified_data WHERE")
    assert sql.endswith("LIMIT 200")


def test_slice_cte_is_first_when_model_has_own_ctes():
    sql = validate_and_compose("WITH t AS (SELECT * FROM slice) SELECT COUNT(*) FROM t", WHERE)
    assert sql.index("slice AS") < sql.index("t AS")


def test_caps_excessive_limit():
    assert validate_and_compose("SELECT * FROM slice LIMIT 100000", WHERE, 50).endswith("LIMIT 50")


@pytest.mark.parametrize("bad", [
    "DELETE FROM slice",
    "UPDATE enriched_unified_data SET sentiment = 'Positive'",
    "DROP TABLE enriched_unified_data",
    "SELECT 1; DROP TABLE enriched_unified_data",
    "SELECT * INTO stolen FROM slice",
    "SELECT * FROM slice FOR UPDATE",
    "SELECT pg_sleep(10)",
    "SELECT current_setting('server_version')",
    "SELECT * FROM pg_catalog.pg_roles",
    "SELECT * FROM users",
    "COPY enriched_unified_data TO '/tmp/x'",
    "WITH slice AS (SELECT 1) SELECT * FROM slice",
    "",
])
def test_rejects_unsafe_sql(bad):
    with pytest.raises(SQLGuardError):
        validate_and_compose(bad, WHERE)


def test_kpi_grounding_rejects_invented_decimals():
    from app.workflow.nodes import kpi_is_grounded
    from app.workflow.state import WorkflowState

    st = WorkflowState(question="q")
    st.table_rows = [{"label": "a", "negative_pct": 37.9, "mentions": 87}]
    assert kpi_is_grounded({"value": "37.9%"}, st)
    assert kpi_is_grounded({"value": "38%"}, st)       # rounding of 37.9
    assert not kpi_is_grounded({"value": "35.6%"}, st)  # invented
    assert not kpi_is_grounded({"value": "high"}, st)
