"""Seed traces for the trace + cost waterfall.

This is the in-memory stand-in for what would, in production, be the
OpenTelemetry span export landed in BigQuery (``_AllSpans``), the token-usage log
(``mcp_usage_log``), and ``INFORMATION_SCHEMA.JOBS_BY_PROJECT`` for bytes billed
and slot ms. The governance boundary (de-identification, zone filtering applied
before any attribute such as ``db.statement`` reaches the widget) is modelled by
the provider, not here.

Each trace is one NL-to-SQL request. Spans are a flat list with ``parent_span_id``
references; the widget builds the tree. ``start_ms`` is the offset from trace
start so the widget can position bars without parsing timestamps. A span carries
the raw cost inputs (tokens and bytes) rather than precomputed dollars, so the
provider derives every dollar figure from one rate card and the roll-ups stay
internally consistent.

The request root has the **twelve pipeline steps** as its children, in order:

     1  Discovery               vector search the top-k candidate tables
     2  Route Zones             pick the governed data zone to read from
     3  Query Planning          decompose the question into a plan
     4  Domain Disambiguation   map ambiguous terms to canonical fields
     5  Resolve Joins with KG   resolve the join graph from the knowledge graph
     6  Recency Resolution      pin the request to the latest load
     7  Grain Resolution        choose the group-by grain
     8  Business Rules          apply metric definitions (late plan)
     9  Date Resolution         rewrite the relative date window
    10  Generate SQL            the LLM call that emits the SQL (token cost)
    11  Validate (Dry Run)      BigQuery dry run + guardrail evaluation (free)
    12  Execute SQL             the billed BigQuery run (bytes cost)

Step 5 carries ``has_lazy_children`` so its per-table knowledge-graph lookups
arrive via ``expand_span_children``, exercising the lazy path. Span ``kind`` drives
the accent colour in the widget:

    root       the request root
    embedding  the discovery vector search
    resolver   a semantic-resolver / planning step
    neo4j      a knowledge-graph-backed step
    guardrail  the dry-run validation gate (SQ-001 through SQ-012)
    bigquery   the executed query
    claude_api the SQL-generation call
    cache      a cache lookup
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 1 GiB, used to size the seeded BigQuery scan.
_GIB = 1024 ** 3
_SCAN = int(1.5 * _GIB)  # bytes scanned by the seeded query


@dataclass(frozen=True)
class Span:
    span_id: str
    parent_span_id: str | None
    name: str
    kind: str
    start_ms: int  # offset from trace start
    duration_ms: int
    status: str = "ok"  # ok | error
    has_lazy_children: bool = False
    # Raw cost inputs. Only the spans that actually incur cost set these; the
    # provider turns them into dollars via the rate card so totals never drift.
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens_in: int = 0
    bytes_scanned: int = 0
    bytes_billed: int = 0
    # OTel-flavored detail surfaced by describe_span.
    attributes: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    # Cross-widget jumps offered in the details panel.
    links: list = field(default_factory=list)


@dataclass(frozen=True)
class Trace:
    trace_id: str
    question: str
    started_at: str
    wall_ms: int
    status: str  # ok | error | guardrail_blocked
    spans: list[Span]


# The trace shown when view_trace_waterfall is called with no id (most recent).
FOCUS_TRACE_ID = "trc_ok"

_LINEAGE_LINK = {
    "rel": "lineage",
    "tool": "view_lineage",
    "args": {"node": "fct_patient_visits"},
    "label": "View lineage for fct_patient_visits",
}


def _guardrail_events(blocking: str | None = None) -> list:
    """Per-rule verdicts for the twelve standing SQL guardrails (SQ-001..SQ-012).

    Evaluated during the dry-run validation step. With no ``blocking`` rule every
    rule passes; otherwise the named rule fails and the rest pass, modelling the
    guardrail-blocked path.
    """
    citations = {
        "SQ-001": "no SELECT *",
        "SQ-002": "qualified table names only",
        "SQ-003": "bounded time range required",
        "SQ-004": "row limit enforced on detail queries",
        "SQ-005": "no cross-zone join without a router decision",
        "SQ-006": "aggregate-only access to the curated zone",
        "SQ-007": "no direct identifier columns in projection",
        "SQ-008": "no DDL or DML",
        "SQ-009": "approved functions only",
        "SQ-010": "cost ceiling on bytes billed",
        "SQ-011": "no unparameterised string predicates",
        "SQ-012": "result de-identification applied",
    }
    events = []
    for rule_id, citation in citations.items():
        verdict = "fail" if rule_id == blocking else "pass"
        events.append({
            "name": "guardrail.rule",
            "guardrail.rule_id": rule_id,
            "guardrail.verdict": verdict,
            "guardrail.citation": f"policy://sql-governance/{rule_id} ({citation})",
        })
    return events


def _trace_ok() -> Trace:
    """A clean NL-to-SQL request: all twelve pipeline steps run end to end.

    Step 5 (Resolve Joins with KG) carries ``has_lazy_children`` so its three
    per-table knowledge-graph lookups arrive via expand_span_children.
    """
    spans = [
        Span(
            "s0", None, "nl_to_sql.request", "root",
            start_ms=0, duration_ms=2410, status="ok",
            attributes={"service.name": "nl-to-sql", "request.zone": "curated",
                        "pipeline.steps": 12},
        ),
        Span(
            "s1", "s0", "Discovery", "embedding",
            start_ms=15, duration_ms=130, status="ok",
            attributes={
                "step": 1,
                "summary": "Vector search the top-k candidate tables for the question.",
                "embedding.model": "text-embedding-3-large",
                "retrieval.top_k": 8,
                "discovery.tables": "fct_patient_visits, encounters, patients, stg_charges",
                "cache.hit": False,
            },
        ),
        Span(
            "s2", "s0", "Route Zones", "resolver",
            start_ms=150, duration_ms=45, status="ok",
            attributes={
                "step": 2,
                "summary": "Choose the governed data zone the query may read from.",
                "resolver.name": "ZoneRouter",
                "zones.considered": "raw, staging, curated",
                "zone": "curated",
                "route.decision": "curated (aggregate-only)",
            },
        ),
        Span(
            "s3", "s0", "Query Planning", "resolver",
            start_ms=200, duration_ms=90, status="ok",
            attributes={
                "step": 3,
                "summary": "Decompose the question into an ordered resolution plan.",
                "resolver.name": "QueryPlanner",
                "plan.intent": "average length of stay by facility, last 90 days",
                "plan.steps": 4,
            },
        ),
        Span(
            "s4", "s0", "Domain Disambiguation", "resolver",
            start_ms=295, duration_ms=70, status="ok",
            attributes={
                "step": 4,
                "summary": "Map ambiguous terms to canonical fields and entities.",
                "resolver.name": "DomainDisambiguator",
                "resolver.match": "length of stay, facility",
                "resolver.rewrite": "los -> los_days; facility -> facility_id",
            },
        ),
        Span(
            "s5", "s0", "Resolve Joins with KG", "neo4j",
            start_ms=370, duration_ms=160, status="ok", has_lazy_children=True,
            attributes={
                "step": 5,
                "summary": "Resolve the join graph from the knowledge graph.",
                "db.system": "neo4j",
                "db.statement": "MATCH (f:Table {name:'fct_patient_visits'})-[:JOINS]->(t) "
                                "RETURN t, rel.keys",
                "joins.resolved": 3,
                "kg.note": "expand to see each per-table knowledge-graph lookup",
            },
            links=[_LINEAGE_LINK],
        ),
        Span(
            "s6", "s0", "Recency Resolution", "resolver",
            start_ms=535, duration_ms=40, status="ok",
            attributes={
                "step": 6,
                "summary": "Pin the request to the latest available data load.",
                "resolver.name": "RecencyResolver",
                "resolver.match": "as of latest load",
                "resolver.rewrite": "snapshot_date = (SELECT MAX(snapshot_date) ...)",
            },
        ),
        Span(
            "s7", "s0", "Grain Resolution", "resolver",
            start_ms=580, duration_ms=35, status="ok",
            attributes={
                "step": 7,
                "summary": "Choose the group-by grain for the aggregate.",
                "resolver.name": "GrainResolver",
                "resolver.match": "by facility",
                "resolver.rewrite": "GROUP BY facility_id",
            },
        ),
        Span(
            "s8", "s0", "Business Rules", "resolver",
            start_ms=620, duration_ms=55, status="ok",
            attributes={
                "step": 8,
                "summary": "Apply metric definitions late in the plan.",
                "resolver.name": "BusinessRuleResolver",
                "resolver.match": "length of stay",
                "resolver.rewrite": "los_days = DATE_DIFF(discharge_ts, admit_ts, DAY)",
            },
        ),
        Span(
            "s9", "s0", "Date Resolution", "resolver",
            start_ms=680, duration_ms=40, status="ok",
            attributes={
                "step": 9,
                "summary": "Rewrite the relative date window into a bounded range.",
                "resolver.name": "DateResolver",
                "resolver.match": "last 90 days",
                "resolver.rewrite": "discharge_ts >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)",
            },
        ),
        Span(
            "s10", "s0", "Generate SQL", "claude_api",
            start_ms=730, duration_ms=320, status="ok",
            tokens_in=4120, tokens_out=380, cached_tokens_in=3072,
            attributes={
                "step": 10,
                "summary": "The LLM call that emits the SQL from the resolved plan.",
                "gen_ai.system": "anthropic",
                "gen_ai.request.model": "claude-opus-4-8",
                "gen_ai.usage.input_tokens": 4120,
                "gen_ai.usage.output_tokens": 380,
                "gen_ai.usage.cached_input_tokens": 3072,
                "gen_ai.prompt.cache_hit": True,
            },
        ),
        Span(
            "s11", "s0", "Validate (Dry Run)", "guardrail",
            start_ms=1060, duration_ms=150, status="ok",
            bytes_scanned=_SCAN, bytes_billed=0,  # a dry run is billed nothing
            attributes={
                "step": 11,
                "summary": "BigQuery dry run plus guardrail evaluation. Billed nothing.",
                "db.system": "bigquery",
                "bq.dry_run": True,
                "bq.bytes_estimated": _SCAN,
                "guardrail.rule_id": "SQ-001..SQ-012",
                "guardrail.verdict": "pass",
                "guardrail.citation": "policy://sql-governance/all (12 rules)",
            },
            events=_guardrail_events(),
        ),
        Span(
            "s12", "s0", "Execute SQL", "bigquery",
            start_ms=1220, duration_ms=1180, status="ok",
            bytes_scanned=_SCAN, bytes_billed=_SCAN,
            attributes={
                "step": 12,
                "summary": "The billed BigQuery run that returns the result.",
                "db.system": "bigquery",
                "db.statement": "SELECT facility_id, AVG(los_days) AS avg_los "
                                "FROM curated.fct_patient_visits "
                                "WHERE discharge_ts >= ... GROUP BY facility_id",
                "bq.job_id": "job_us_8f1c2a",
                "bq.bytes_scanned": _SCAN,
                "bq.bytes_billed": _SCAN,
                "bq.slot_ms": 41020,
                "bq.cache_hit": False,
            },
            links=[_LINEAGE_LINK],
        ),
        # Lazy children of step 5 (Resolve Joins with KG). Hidden on the initial
        # view; revealed by expand_span_children. One knowledge-graph lookup per
        # joined table, tying back to the lineage viewer's vocabulary.
        Span(
            "s5a", "s5", "kg.resolve.encounters", "neo4j",
            start_ms=378, duration_ms=42, status="ok",
            attributes={
                "db.system": "neo4j",
                "kg.match": "fct_patient_visits -[encounter_id]-> encounters",
                "join.keys": "encounter_id",
            },
        ),
        Span(
            "s5b", "s5", "kg.resolve.patients", "neo4j",
            start_ms=422, duration_ms=44, status="ok",
            attributes={
                "db.system": "neo4j",
                "kg.match": "fct_patient_visits -[patient_id]-> patients",
                "join.keys": "patient_id",
            },
        ),
        Span(
            "s5c", "s5", "kg.resolve.stg_charges", "neo4j",
            start_ms=468, duration_ms=58, status="ok",
            attributes={
                "db.system": "neo4j",
                "kg.match": "fct_patient_visits -[encounter_id]-> stg_charges",
                "join.keys": "encounter_id",
            },
        ),
    ]
    return Trace(
        trace_id="trc_ok",
        question="average length of stay by facility for the last 90 days",
        started_at="2026-06-24T17:02:11.482Z",
        wall_ms=2410,
        status="ok",
        spans=spans,
    )


def _trace_blocked() -> Trace:
    """A request blocked at the dry-run validation step (SQ-007, direct identifiers).

    The pipeline runs through Generate SQL, but the dry-run validation fails the
    guardrail, so Execute SQL never runs. The widget badges the blocking step and
    treats the trace status as blocked.
    """
    spans = [
        Span(
            "b0", None, "nl_to_sql.request", "root",
            start_ms=0, duration_ms=1140, status="error",
            attributes={
                "service.name": "nl-to-sql",
                "request.zone": "curated",
                "request.outcome": "guardrail_blocked",
                "pipeline.steps": 11,
            },
        ),
        Span(
            "b1", "b0", "Discovery", "embedding",
            start_ms=15, duration_ms=110, status="ok",
            attributes={"step": 1, "embedding.model": "text-embedding-3-large",
                        "retrieval.top_k": 8, "cache.hit": True},
        ),
        Span(
            "b2", "b0", "Route Zones", "resolver",
            start_ms=140, duration_ms=40, status="ok",
            attributes={"step": 2, "resolver.name": "ZoneRouter", "zone": "curated"},
        ),
        Span(
            "b3", "b0", "Query Planning", "resolver",
            start_ms=185, duration_ms=80, status="ok",
            attributes={"step": 3, "resolver.name": "QueryPlanner",
                        "plan.intent": "list patient identifiers seen in the ED"},
        ),
        Span(
            "b4", "b0", "Domain Disambiguation", "resolver",
            start_ms=270, duration_ms=60, status="ok",
            attributes={"step": 4, "resolver.name": "DomainDisambiguator",
                        "resolver.match": "patient name, date of birth"},
        ),
        Span(
            "b5", "b0", "Resolve Joins with KG", "neo4j",
            start_ms=335, duration_ms=150, status="ok",
            attributes={"step": 5, "db.system": "neo4j", "joins.resolved": 1},
        ),
        Span(
            "b6", "b0", "Recency Resolution", "resolver",
            start_ms=490, duration_ms=35, status="ok",
            attributes={"step": 6, "resolver.name": "RecencyResolver"},
        ),
        Span(
            "b7", "b0", "Grain Resolution", "resolver",
            start_ms=530, duration_ms=30, status="ok",
            attributes={"step": 7, "resolver.name": "GrainResolver"},
        ),
        Span(
            "b8", "b0", "Business Rules", "resolver",
            start_ms=565, duration_ms=50, status="ok",
            attributes={"step": 8, "resolver.name": "BusinessRuleResolver"},
        ),
        Span(
            "b9", "b0", "Date Resolution", "resolver",
            start_ms=620, duration_ms=35, status="ok",
            attributes={"step": 9, "resolver.name": "DateResolver",
                        "resolver.match": "last night"},
        ),
        Span(
            "b10", "b0", "Generate SQL", "claude_api",
            start_ms=660, duration_ms=300, status="ok",
            tokens_in=3880, tokens_out=410, cached_tokens_in=3072,
            attributes={
                "step": 10,
                "gen_ai.system": "anthropic",
                "gen_ai.request.model": "claude-opus-4-8",
                "gen_ai.usage.input_tokens": 3880,
                "gen_ai.usage.output_tokens": 410,
                "gen_ai.usage.cached_input_tokens": 3072,
            },
        ),
        Span(
            "b11", "b0", "Validate (Dry Run)", "guardrail",
            start_ms=965, duration_ms=170, status="error",
            bytes_scanned=int(0.4 * _GIB), bytes_billed=0,
            attributes={
                "step": 11,
                "summary": "Dry-run validation failed a guardrail; the query was not executed.",
                "db.system": "bigquery",
                "bq.dry_run": True,
                "guardrail.rule_id": "SQ-007",
                "guardrail.verdict": "fail",
                "guardrail.citation": "policy://sql-governance/SQ-007 "
                                      "(no direct identifier columns in projection)",
                "guardrail.matched_sql": "SELECT patient_name, date_of_birth FROM ...",
            },
            events=_guardrail_events(blocking="SQ-007"),
        ),
    ]
    return Trace(
        trace_id="trc_blocked",
        question="list every patient name and full date of birth seen in the ED last night",
        started_at="2026-06-24T15:48:03.117Z",
        wall_ms=1140,
        status="guardrail_blocked",
        spans=spans,
    )


def build_traces() -> dict[str, Trace]:
    """All seed traces, keyed by trace id."""
    traces = [_trace_ok(), _trace_blocked()]
    return {t.trace_id: t for t in traces}
