"""Tests for the SQL -> query-plan (logical execution pipeline) parser and tools."""

import pytest

from mcp_ui_components import QUERY_PLAN_URI, registry as tools
from mcp_ui_components.shared.examples import EXAMPLE_JOIN_SQL
from mcp_ui_components.components.query_plan.provider import QueryPlanProvider
from mcp_ui_components.components.query_plan.sql import parse_query_plan


@pytest.fixture(scope="module")
def model():
    return QueryPlanProvider().query_plan()  # bundled example


def _block(model, name):
    return next(b for b in model["blocks"] if b["name"] == name)


def _types(block):
    return [o["type"] for o in block["operators"]]


def _op(block, otype):
    return next(o for o in block["operators"] if o["type"] == otype)


# ---- bundled example ------------------------------------------------------
def test_example_has_four_blocks(model):
    names = [b["name"] for b in model["blocks"]]
    assert names == ["base_encounters", "attending_providers", "patient_gender", "main"]
    assert model["blockCount"] == 4
    assert model["operatorCount"] == 15


def test_block_kinds(model):
    kinds = [b["kind"] for b in model["blocks"]]
    assert kinds.count("cte") == 3
    assert kinds.count("main") == 1


def test_base_encounters_operator_order(model):
    assert _types(_block(model, "base_encounters")) == ["scan", "join", "filter", "project"]


def test_main_operator_order(model):
    assert _types(_block(model, "main")) == [
        "scan", "join", "join", "aggregate", "project", "sort",
    ]


def test_scan_operators_reference_sources(model):
    b0_scan = _op(_block(model, "base_encounters"), "scan")
    assert b0_scan["tables"] == ["encounters"]
    assert b0_scan["badge"] == "TABLE"
    assert b0_scan["sourceRef"] is None

    main_scan = _op(_block(model, "main"), "scan")
    assert main_scan["sourceRef"] == "base_encounters"
    assert main_scan["badge"] == "CTE"


def test_main_join_operators(model):
    joins = [o for o in _block(model, "main")["operators"] if o["type"] == "join"]
    assert {j["badge"] for j in joins} == {"INNER", "LEFT"}
    inner = next(j for j in joins if j["badge"] == "INNER")
    assert "attending_providers" in inner["title"]
    assert "facility_id" in inner["columns"] and "encounter_id" in inner["columns"]
    assert inner["detail"] and inner["detail"][0].startswith("ON ")


def test_filter_operator_detail(model):
    detail = " ".join(_op(_block(model, "base_encounters"), "filter")["detail"])
    assert "is_current = 1" in detail
    assert "status" in detail
    assert "2025-01-01" in detail and "2026-01-01" in detail


def test_aggregate_operator(model):
    detail = " ".join(_op(_block(model, "main"), "aggregate")["detail"])
    assert "facility_name" in detail
    assert "attending_provider_name" in detail
    assert "COUNT(DISTINCT" in detail


def test_project_outputs(model):
    outs = _block(model, "main")["outputColumns"]
    assert "patient_gender_mix" in outs
    assert "total_encounters" in outs


def test_sort_operator(model):
    detail = _op(_block(model, "main"), "sort")["detail"]
    assert any("total_encounters DESC" in d for d in detail)


def test_cross_edges(model):
    main_id = _block(model, "main")["id"]
    assert len(model["edges"]) == 3
    assert all(e["toBlock"] == main_id for e in model["edges"])
    assert {e["label"] for e in model["edges"]} == {
        "base_encounters", "attending_providers", "patient_gender",
    }
    # edge colour matches the producing block accent
    for e in model["edges"]:
        producer = next(b for b in model["blocks"] if b["id"] == e["from"])
        assert e["color"] == producer["accent"]


def test_metadata(model):
    assert model["dataset"] == "analytics-prod.clinical_core"
    assert model["scan"] == "549.2 GB"
    assert model["period"] == {"start": "2025-01-01", "end": "2026-01-01"}
    assert model["nl"].startswith("For calendar year 2025")
    assert model["sql"] == EXAMPLE_JOIN_SQL


# ---- synthetic clause coverage --------------------------------------------
def test_window_and_qualify_yield_window_not_aggregate():
    m = parse_query_plan(
        "SELECT a, SUM(x) OVER (PARTITION BY a ORDER BY b) AS s FROM t "
        "QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1"
    )
    types = _types(m["blocks"][0])
    assert "window" in types
    assert "aggregate" not in types  # windowed aggregate is not a GROUP BY stage
    detail = " ".join(_op(m["blocks"][0], "window")["detail"])
    assert "OVER" in detail and "QUALIFY" in detail


def test_distinct_and_limit():
    m = parse_query_plan("SELECT DISTINCT a, b FROM t ORDER BY a LIMIT 10 OFFSET 5")
    types = _types(m["blocks"][0])
    assert types == ["scan", "project", "distinct", "sort", "limit"]
    limit_detail = _op(m["blocks"][0], "limit")["detail"]
    assert "LIMIT 10" in limit_detail and "OFFSET 5" in limit_detail


def test_set_operation():
    m = parse_query_plan("SELECT a FROM t1 UNION ALL SELECT a FROM t2")
    kinds = [b["kind"] for b in m["blocks"]]
    assert kinds.count("main") == 2          # two arms
    setop = next(b for b in m["blocks"] if b["kind"] == "setop")
    assert setop["name"] == "UNION ALL"
    assert len(m["edges"]) == 2
    assert all(e["toBlock"] == setop["id"] for e in m["edges"])


def test_derived_table_block():
    m = parse_query_plan(
        "SELECT d.x FROM (SELECT x FROM raw) d JOIN other o ON d.x = o.x"
    )
    derived = next(b for b in m["blocks"] if b["kind"] == "derived")
    assert derived["name"] == "d"
    main = next(b for b in m["blocks"] if b["kind"] == "main")
    assert _op(main, "scan")["sourceRef"] == "d"
    assert any(e["label"] == "d" and e["toBlock"] == main["id"] for e in m["edges"])


def test_unparseable_sql_raises():
    with pytest.raises(ValueError):
        parse_query_plan("this is not sql ((((")


# ---- tool dispatch --------------------------------------------------------
def test_view_query_plan_tool_carries_ui_meta():
    r = tools.call_tool("view_query_plan", {})
    assert r["_meta"]["ui"]["resourceUri"] == QUERY_PLAN_URI
    assert r["structuredContent"]["blockCount"] == 4


def test_parse_query_plan_tool_dispatch():
    sql = "SELECT a.x FROM a JOIN b ON a.k = b.k"
    r = tools.call_tool("parse_query_plan", {"sql": sql})["structuredContent"]
    assert r["blocks"][0]["kind"] == "main"
    assert _types(r["blocks"][0])[:2] == ["scan", "join"]


def test_parse_query_plan_requires_sql():
    with pytest.raises(ValueError):
        tools.call_tool("parse_query_plan", {})


def test_view_query_plan_declares_ui_meta_in_schema():
    spec = next(t for t in tools.TOOLS if t["name"] == "view_query_plan")
    assert spec["_meta"]["ui"]["resourceUri"] == QUERY_PLAN_URI
    assert spec["_meta"]["ui/resourceUri"] == QUERY_PLAN_URI


def test_query_plan_resource_registered():
    res = next(r for r in tools.RESOURCES if r["uri"] == QUERY_PLAN_URI)
    assert res["mimeType"] == "text/html;profile=mcp-app"


def test_query_plan_widget_resource_is_built_mcp_app():
    html = tools.read_resource(QUERY_PLAN_URI)
    assert "Query plan" in html
    assert "parse_query_plan" in html         # governed edit-SQL round-trip is wired
