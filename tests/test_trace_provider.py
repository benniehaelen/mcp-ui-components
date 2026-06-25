"""Tests for the trace + cost waterfall provider and tool dispatch."""

from mcp_ui_components import TRACE_WATERFALL_URI, registry as tools
from mcp_ui_components.components.trace_waterfall.data import FOCUS_TRACE_ID
from mcp_ui_components.components.trace_waterfall.provider import (
    BQ_RATE_PER_TIB_USD,
    RATE_CARD,
    TraceProvider,
)

# The six resolver steps that arrive lazily under resolve.pipeline.
RESOLVER_STEPS = {
    "resolve.DateResolver", "resolve.RecencyResolver", "resolve.GrainResolver",
    "resolve.ZoneRouter", "resolve.BusinessRuleResolver", "resolve.AuthorityReranker",
}


def test_default_trace_is_most_recent():
    t = TraceProvider().get_trace(None)
    assert t["trace_id"] == FOCUS_TRACE_ID
    assert t["status"] == "ok"


def test_initial_view_withholds_lazy_resolver_children():
    t = TraceProvider().get_trace("trc_ok")
    names = {s["name"] for s in t["spans"]}
    # the six resolver steps are hidden until expand_span_children
    assert names.isdisjoint(RESOLVER_STEPS)
    assert "resolve.pipeline" in names
    pipeline = next(s for s in t["spans"] if s["name"] == "resolve.pipeline")
    assert pipeline["has_lazy_children"] is True
    assert len(t["spans"]) == 6


def test_expand_reveals_exactly_the_six_resolver_spans():
    p = TraceProvider()
    view = p.get_trace("trc_ok")
    visible = [s["span_id"] for s in view["spans"]]
    exp = p.expand("s2", visible)
    assert {s["name"] for s in exp["spans"]} == RESOLVER_STEPS
    # already-visible children are not re-added
    again = p.expand("s2", visible + [s["span_id"] for s in exp["spans"]])
    assert again["spans"] == []


def test_describe_span_carries_otel_detail_and_links():
    d = TraceProvider().describe_span("s4")  # bigquery.execute
    assert d["kind"] == "bigquery"
    assert d["attributes"]["db.system"] == "bigquery"
    assert d["attributes"]["bq.cache_hit"] is False
    assert d["links"][0]["tool"] == "view_lineage"
    assert d["links"][0]["args"]["node"] == "fct_patient_visits"


def test_describe_guardrail_has_per_rule_events():
    d = TraceProvider().describe_span("b3")  # the blocking guardrail span
    rule_ids = {e["guardrail.rule_id"] for e in d["events"]}
    assert "SQ-007" in rule_ids
    blocked = next(e for e in d["events"] if e["guardrail.rule_id"] == "SQ-007")
    assert blocked["guardrail.verdict"] == "fail"
    assert all(
        e["guardrail.verdict"] == "pass"
        for e in d["events"] if e["guardrail.rule_id"] != "SQ-007"
    )


def test_cost_breakdown_total_equals_claude_plus_bigquery_span_scope():
    cb = TraceProvider().cost_breakdown("s5", "span")  # claude_api span
    assert cb["scope"] == "span"
    assert cb["claude_api"]["cost_usd"] > 0
    assert cb["bigquery"]["cost_usd"] == 0
    assert cb["total_cost_usd"] == round(
        cb["claude_api"]["cost_usd"] + cb["bigquery"]["cost_usd"], 6)


def test_cost_breakdown_subtree_sums_claude_and_bigquery():
    cb = TraceProvider().cost_breakdown("s0", "subtree")  # whole request
    # the subtree rolls up both the Claude format call and the BigQuery scan
    assert cb["claude_api"]["cost_usd"] > 0
    assert cb["bigquery"]["cost_usd"] > 0
    assert cb["total_cost_usd"] == round(
        cb["claude_api"]["cost_usd"] + cb["bigquery"]["cost_usd"], 6)
    # the roll-up equals the trace totals
    totals = TraceProvider().get_trace("trc_ok")["totals"]
    assert cb["total_cost_usd"] == totals["total_cost_usd"]


def test_cost_breakdown_uses_provider_rate_cards():
    cb = TraceProvider().cost_breakdown("s5", "span")
    assert cb["claude_api"]["rate_card"]["in_per_mtok_usd"] == RATE_CARD["in_per_mtok_usd"]
    assert cb["bigquery"]["rate_per_tib_usd"] == BQ_RATE_PER_TIB_USD
    # uncached = total in - cached in
    ca = cb["claude_api"]
    assert ca["uncached_tokens_in"] == ca["tokens_in"] - ca["cached_tokens_in"]


def test_cost_breakdown_rejects_bad_scope():
    try:
        TraceProvider().cost_breakdown("s0", "sideways")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("bad scope should raise")


def test_list_recent_returns_both_traces_and_filters_by_status():
    p = TraceProvider()
    rows = p.list_recent(10, "any")
    assert {r["trace_id"] for r in rows} == {"trc_ok", "trc_blocked"}
    # newest first
    assert rows[0]["trace_id"] == "trc_ok"
    blocked = p.list_recent(10, "guardrail_blocked")
    assert [r["trace_id"] for r in blocked] == ["trc_blocked"]


def test_blocked_trace_status_and_blocking_span():
    t = TraceProvider().get_trace("trc_blocked")
    assert t["status"] == "guardrail_blocked"
    guardrail = next(s for s in t["spans"] if s["name"] == "guardrails.evaluate")
    assert guardrail["status"] == "error"
    # the blocked request never reaches BigQuery
    assert "bigquery.execute" not in {s["name"] for s in t["spans"]}


def test_unknown_trace_and_span_raise():
    p = TraceProvider()
    for call in (lambda: p.get_trace("nope"), lambda: p.describe_span("nope")):
        try:
            call()
        except KeyError:
            pass
        else:  # pragma: no cover
            raise AssertionError("unknown id should raise KeyError")


# ---- tool dispatch (through the shared registry) --------------------------
def test_view_trace_waterfall_tool_carries_ui_meta():
    r = tools.call_tool("view_trace_waterfall", {})
    assert r["_meta"]["ui"]["resourceUri"] == TRACE_WATERFALL_URI
    assert r["structuredContent"]["trace_id"] == FOCUS_TRACE_ID


def test_expand_tool_dispatch():
    view = tools.call_tool("view_trace_waterfall", {"trace_id": "trc_ok"})["structuredContent"]
    visible = [s["span_id"] for s in view["spans"]]
    r = tools.call_tool(
        "expand_span_children",
        {"span_id": "s2", "visible_span_ids": visible},
    )["structuredContent"]
    assert {s["name"] for s in r["spans"]} == RESOLVER_STEPS


def test_cost_breakdown_tool_dispatch():
    r = tools.call_tool(
        "get_span_cost_breakdown", {"span_id": "s0", "scope": "subtree"},
    )["structuredContent"]
    assert r["total_cost_usd"] == round(
        r["claude_api"]["cost_usd"] + r["bigquery"]["cost_usd"], 6)


def test_list_recent_tool_dispatch():
    r = tools.call_tool("list_recent_traces", {})["structuredContent"]
    assert {t["trace_id"] for t in r["traces"]} == {"trc_ok", "trc_blocked"}


def test_widget_resource_served_as_mcp_app():
    html = tools.read_resource(TRACE_WATERFALL_URI)
    assert "Trace waterfall" in html
    assert "describe_span" in html          # click-a-span interaction
    assert "expand_span_children" in html   # lazy-expand interaction
    assert "profile=mcp-app" in html        # SDK references the MCP Apps MIME type


def test_widget_resource_mime_is_mcp_app():
    res = next(r for r in tools.RESOURCES if r["uri"] == TRACE_WATERFALL_URI)
    assert res["mimeType"] == "text/html;profile=mcp-app"
    assert tools.RESOURCE_MIME_TYPE == "text/html;profile=mcp-app"


def test_view_trace_waterfall_declares_ui_meta_in_schema():
    spec = next(t for t in tools.TOOLS if t["name"] == "view_trace_waterfall")
    assert spec["_meta"]["ui"]["resourceUri"] == TRACE_WATERFALL_URI
    # legacy flat key also present for older hosts
    assert spec["_meta"]["ui/resourceUri"] == TRACE_WATERFALL_URI
