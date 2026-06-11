"""Tests for the lineage provider and tool dispatch."""

from lineage_mcp import WIDGET_URI, tools
from lineage_mcp.data import FOCUS_ID
from lineage_mcp.provider import LineageProvider


def test_initial_view_has_one_hop_each_direction():
    g = LineageProvider().view(FOCUS_ID)
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {
        FOCUS_ID, "encounters", "patients", "stg_charges", "avg_los", "vw_visits"
    }
    layers = {n["id"]: n["layer"] for n in g["nodes"]}
    assert layers[FOCUS_ID] == 0
    assert layers["encounters"] == -1 and layers["stg_charges"] == -1
    assert layers["avg_los"] == 1 and layers["vw_visits"] == 1
    assert len(g["edges"]) == 5


def test_focus_node_flagged():
    g = LineageProvider().view(FOCUS_ID)
    focus = [n for n in g["nodes"] if n["isFocus"]]
    assert len(focus) == 1 and focus[0]["id"] == FOCUS_ID


def test_expand_reveals_second_upstream_hop_then_exhausts():
    p = LineageProvider()
    g = p.view(FOCUS_ID)
    visible = [n["id"] for n in g["nodes"]]

    # Expand one specific node's upstream neighbours.
    exp = p.expand(FOCUS_ID, "encounters", "upstream", visible)
    added = {n["id"] for n in exp["addedNodes"]}
    assert added == {"raw_admissions", "raw_ed_visits"}
    assert exp["addedEdges"] == [
        {"source": "raw_admissions", "target": "encounters"},
        {"source": "raw_ed_visits", "target": "encounters"},
    ]

    # Already-visible neighbours are not re-added.
    again = p.expand(FOCUS_ID, "encounters", "upstream", list(visible) + list(added))
    assert again["addedNodes"] == []


def test_expand_downstream():
    p = LineageProvider()
    g = p.view(FOCUS_ID)
    visible = [n["id"] for n in g["nodes"]]
    exp = p.expand(FOCUS_ID, "vw_visits", "downstream", visible)
    assert {n["id"] for n in exp["addedNodes"]} == {"exec_dashboard", "census_report"}


def test_expand_rejects_bad_direction():
    p = LineageProvider()
    try:
        p.expand(FOCUS_ID, FOCUS_ID, "sideways", [FOCUS_ID])
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("bad direction should raise")


def test_describe_node():
    d = LineageProvider().describe("fct_patient_visits")
    assert d["kind"] == "fact"
    assert d["owner"] == "analytics-eng"
    assert "visit_id" in d["columns"]
    assert d["upstream"] == ["encounters", "patients", "stg_charges"]
    assert d["downstream"] == ["avg_los", "vw_visits"]


def test_unknown_focus_raises():
    try:
        LineageProvider().view("does_not_exist")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("unknown focus should raise KeyError")


def test_view_lineage_tool_carries_ui_meta():
    r = tools.call_tool("view_lineage", {"node": FOCUS_ID})
    assert r["_meta"]["ui"]["resourceUri"] == WIDGET_URI
    assert len(r["structuredContent"]["nodes"]) == 6


def test_expand_tool_dispatch():
    g = tools.call_tool("view_lineage", {})["structuredContent"]
    visible = [n["id"] for n in g["nodes"]]
    r = tools.call_tool(
        "expand_lineage_node",
        {"node": "encounters", "direction": "upstream", "visible_node_ids": visible},
    )["structuredContent"]
    assert {n["id"] for n in r["addedNodes"]} == {"raw_admissions", "raw_ed_visits"}


def test_describe_node_tool_dispatch():
    r = tools.call_tool("describe_node", {"node": "vw_visits"})["structuredContent"]
    assert r["kind"] == "view"
    assert r["owner"] == "analytics-eng"


def test_node_payload_has_counts():
    g = tools.call_tool("view_lineage", {})["structuredContent"]
    fct = next(n for n in g["nodes"] if n["id"] == "fct_patient_visits")
    assert fct["upstreamCount"] == 3 and fct["downstreamCount"] == 2


def test_widget_resource_served_as_mcp_app():
    html = tools.read_resource(WIDGET_URI)
    # The vendored bundle is a real MCP App built with the official SDK.
    assert "Data lineage" in html
    assert "describe_node" in html         # click-a-node interaction
    assert "profile=mcp-app" in html       # SDK references the MCP Apps MIME type


def test_widget_resource_mime_is_mcp_app():
    res = next(r for r in tools.RESOURCES if r["uri"] == WIDGET_URI)
    assert res["mimeType"] == "text/html;profile=mcp-app"
    assert tools.RESOURCE_MIME_TYPE == "text/html;profile=mcp-app"


def test_view_lineage_declares_ui_meta_in_schema():
    spec = next(t for t in tools.TOOLS if t["name"] == "view_lineage")
    assert spec["_meta"]["ui"]["resourceUri"] == WIDGET_URI
    # legacy flat key also present for older hosts
    assert spec["_meta"]["ui/resourceUri"] == WIDGET_URI
