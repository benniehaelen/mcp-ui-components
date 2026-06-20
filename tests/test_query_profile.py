"""Tests for the SQL -> query-profile (parse-derived analytics) parser and tools."""

import pytest

from mcp_ui_components import QUERY_PROFILE_URI, registry as tools
from mcp_ui_components.shared.examples import EXAMPLE_JOIN_SQL
from mcp_ui_components.components.query_profile.provider import QueryProfileProvider
from mcp_ui_components.components.query_profile.sql import profile_query


@pytest.fixture(scope="module")
def model():
    return QueryProfileProvider().query_profile()  # bundled example


def test_example_headline_counts(model):
    assert model["tableCount"] == 4
    assert model["joinCounts"] == {"INNER": 2, "LEFT": 1}
    assert model["joinTotal"] == 3
    # three CTE lanes + the main query
    assert model["blockCount"] == 4


def test_example_operator_mix(model):
    ops = model["operatorCounts"]
    # one scan + one project per scope (4 scopes)
    assert ops["scan"] == 4
    assert ops["project"] == 4
    # joins across the statement, the two WHEREs, and the main GROUP BY / ORDER BY
    assert ops["join"] == 3
    assert ops["filter"] == 2
    assert ops["aggregate"] == 1
    assert ops["sort"] == 1
    assert model["operatorCount"] == sum(ops.values())


def test_operator_counts_are_in_pipeline_order(model):
    order = ["scan", "join", "filter", "aggregate", "having",
             "window", "project", "distinct", "sort", "limit"]
    keys = list(model["operatorCounts"].keys())
    assert keys == [k for k in order if k in keys]


def test_complexity_scored_and_labelled(model):
    assert model["complexity"]["label"] in {"Low", "Medium", "High"}
    assert model["complexity"]["score"] > 0
    # the bundled example is a multi-CTE aggregate -> High
    assert model["complexity"]["label"] == "High"


def test_metadata(model):
    assert model["dataset"] == "analytics-prod.clinical_core"
    assert model["scan"] == "549.2 GB"
    assert model["period"] == {"start": "2025-01-01", "end": "2026-01-01"}
    assert model["sql"] == EXAMPLE_JOIN_SQL


def test_simple_query_without_ctes():
    m = profile_query(
        "SELECT a.x FROM a LEFT JOIN b ON a.k = b.k WHERE a.y > 1 ORDER BY a.x"
    )
    assert m["tableCount"] == 2
    assert m["joinCounts"] == {"LEFT": 1}
    assert m["blockCount"] == 1
    assert m["operatorCounts"] == {
        "scan": 1, "join": 1, "filter": 1, "project": 1, "sort": 1
    }
    assert m["complexity"]["label"] == "Low"


def test_distinct_and_limit_detected():
    m = profile_query("SELECT DISTINCT x FROM t LIMIT 10")
    assert m["operatorCounts"]["distinct"] == 1
    assert m["operatorCounts"]["limit"] == 1


def test_cte_reference_is_not_counted_as_table():
    m = profile_query("WITH c AS (SELECT id FROM base) SELECT id FROM c")
    # only the physical table `base`, not the CTE `c`
    assert m["tableCount"] == 1
    assert m["blockCount"] == 2


def test_unparseable_sql_raises():
    with pytest.raises(ValueError):
        profile_query("this is not sql ((((")


# ---- tool dispatch --------------------------------------------------------
def test_view_query_profile_tool_carries_ui_meta():
    r = tools.call_tool("view_query_profile", {})
    assert r["_meta"]["ui"]["resourceUri"] == QUERY_PROFILE_URI
    assert r["structuredContent"]["tableCount"] == 4


def test_parse_query_profile_tool_dispatch():
    r = tools.call_tool(
        "parse_query_profile", {"sql": "SELECT a.x FROM a JOIN b ON a.k = b.k"}
    )["structuredContent"]
    assert r["tableCount"] == 2
    assert r["joinCounts"] == {"INNER": 1}


def test_parse_query_profile_requires_sql():
    with pytest.raises(ValueError):
        tools.call_tool("parse_query_profile", {})


def test_view_query_profile_declares_ui_meta_in_schema():
    spec = next(t for t in tools.TOOLS if t["name"] == "view_query_profile")
    assert spec["_meta"]["ui"]["resourceUri"] == QUERY_PROFILE_URI
    assert spec["_meta"]["ui/resourceUri"] == QUERY_PROFILE_URI


def test_query_profile_resource_registered():
    res = next(r for r in tools.RESOURCES if r["uri"] == QUERY_PROFILE_URI)
    assert res["mimeType"] == "text/html;profile=mcp-app"


def test_query_profile_resource_is_built_mcp_app():
    html = tools.read_resource(QUERY_PROFILE_URI)
    assert "Query profile" in html
    assert "parse_query_profile" in html       # governed edit-SQL round-trip is wired
    assert "profile=mcp-app" in html           # SDK references the MCP Apps MIME type
