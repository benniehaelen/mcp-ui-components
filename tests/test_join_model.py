"""Tests for the SQL -> join-diagram model parser and its tool dispatch."""

import pytest

from lineage_mcp import JOIN_WIDGET_URI, tools
from lineage_mcp.data import EXAMPLE_JOIN_SQL
from lineage_mcp.provider import JoinDiagramProvider
from lineage_mcp.sql_joins import parse_join_diagram


@pytest.fixture(scope="module")
def model():
    return JoinDiagramProvider().join_diagram()  # bundled example


def _card(model, name):
    return next(c for c in model["tables"] if c["name"] == name)


def test_example_has_four_cards_with_spine(model):
    names = [c["name"] for c in model["tables"]]
    assert names == [
        "encounters",
        "facilities",
        "providers",
        "patients",
    ]
    assert model["tableCount"] == 4
    spine = [c for c in model["tables"] if c["role"] == "spine"]
    assert len(spine) == 1 and spine[0]["name"] == "encounters"


def test_three_joins_two_inner_one_left(model):
    assert model["joinCounts"] == {"INNER": 2, "LEFT": 1}
    # every connector fans out from the spine card (matches the hand diagram)
    spine_id = _card(model, "encounters")["id"]
    assert all(j["from"] == spine_id for j in model["joins"])
    by_to = {j["toName"]: j for j in model["joins"]}
    assert by_to["facilities"]["type"] == "INNER"
    assert by_to["facilities"]["keys"] == ["facility_id", "region_code"]
    assert by_to["providers"]["type"] == "INNER"
    assert by_to["providers"]["keys"] == ["facility_id", "encounter_id"]
    assert by_to["patients"]["type"] == "LEFT"
    # connector colour matches the destination card accent
    assert by_to["patients"]["color"] == _card(model, "patients")["accent"]


def test_join_keys_per_card(model):
    assert _card(model, "encounters")["joinKeys"] == [
        "facility_id", "region_code", "encounter_id",
    ]
    assert _card(model, "facilities")["joinKeys"] == [
        "facility_id", "region_code",
    ]
    assert _card(model, "providers")["joinKeys"] == [
        "facility_id", "encounter_id",
    ]


def test_cte_membership_and_join_type_per_card(model):
    assert _card(model, "encounters")["cte"] == "base_encounters"
    assert _card(model, "facilities")["cte"] == "base_encounters"
    assert _card(model, "providers")["cte"] == "attending_providers"
    assert _card(model, "patients")["cte"] == "patient_gender"
    # how each non-spine table entered the query
    assert _card(model, "facilities")["joinType"] == "INNER"
    assert _card(model, "patients")["joinType"] == "LEFT"


def test_derived_outputs_attributed(model):
    enc = [d["name"] for d in _card(model, "encounters")["derivedOutputs"]]
    assert "encounter_month" in enc and "encounter_key" in enc
    prov = [d["name"] for d in _card(model, "providers")["derivedOutputs"]]
    assert prov == ["attending_provider_name"]


def test_filters_attributed_to_right_card(model):
    enc_filters = " ".join(f["expr"] for f in _card(model, "encounters")["filters"])
    assert "is_current = 1" in enc_filters
    fac_filters = " ".join(f["expr"] for f in _card(model, "facilities")["filters"])
    assert "is_active = 1" in fac_filters
    assert "status" in fac_filters
    # the LEFT-joined table has no standalone WHERE filter
    assert _card(model, "patients")["filters"] == []


def test_metadata(model):
    assert model["dataset"] == "analytics-prod.clinical_core"
    assert model["scan"] == "549.2 GB"
    assert model["period"] == {"start": "2025-01-01", "end": "2026-01-01"}
    assert model["nl"].startswith("For calendar year 2025")
    assert model["sql"] == EXAMPLE_JOIN_SQL


def test_simple_two_table_query_without_ctes():
    sql = (
        "SELECT o.id, c.name FROM orders o "
        "LEFT JOIN customers c ON o.customer_id = c.id "
        "WHERE o.status = 'paid'"
    )
    m = parse_join_diagram(sql, title="orders")
    assert [c["name"] for c in m["tables"]] == ["orders", "customers"]
    assert m["joins"][0]["type"] == "LEFT"
    # the diagram lists the driving-side join column
    assert m["joins"][0]["keys"] == ["customer_id"]
    # with no CTEs, the main query's projections/filters populate the cards
    assert _card(m, "orders")["selectedColumns"] == ["id"]
    assert any("status" in f["expr"] for f in _card(m, "orders")["filters"])
    assert _card(m, "orders")["role"] == "spine"


def test_unparseable_sql_raises():
    with pytest.raises(ValueError):
        parse_join_diagram("this is not sql ((((")


# ---- tool dispatch --------------------------------------------------------
def test_view_join_diagram_tool_carries_ui_meta():
    r = tools.call_tool("view_join_diagram", {})
    assert r["_meta"]["ui"]["resourceUri"] == JOIN_WIDGET_URI
    assert r["structuredContent"]["tableCount"] == 4


def test_parse_join_sql_tool_dispatch():
    sql = "SELECT a.x FROM a JOIN b ON a.k = b.k"
    r = tools.call_tool("parse_join_sql", {"sql": sql})["structuredContent"]
    assert {c["name"] for c in r["tables"]} == {"a", "b"}
    assert r["joins"][0]["keys"] == ["k"]


def test_parse_join_sql_requires_sql():
    with pytest.raises(ValueError):
        tools.call_tool("parse_join_sql", {})


def test_view_join_diagram_declares_ui_meta_in_schema():
    spec = next(t for t in tools.TOOLS if t["name"] == "view_join_diagram")
    assert spec["_meta"]["ui"]["resourceUri"] == JOIN_WIDGET_URI
    assert spec["_meta"]["ui/resourceUri"] == JOIN_WIDGET_URI


def test_join_resource_registered():
    res = next(r for r in tools.RESOURCES if r["uri"] == JOIN_WIDGET_URI)
    assert res["mimeType"] == "text/html;profile=mcp-app"


def test_join_widget_resource_is_built_mcp_app():
    html = tools.read_resource(JOIN_WIDGET_URI)
    assert "Join diagram" in html
    assert "connector-svg" in html            # the diagram canvas
    assert "parse_join_sql" in html           # governed edit-SQL round-trip is wired

