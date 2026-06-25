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

Span ``kind`` drives the accent colour in the widget:

    root       the request root
    embedding  question embedding lookup
    resolver   semantic-resolver pipeline and its steps
    neo4j      a graph-backed resolver step
    guardrail  policy evaluation (SQ-001 through SQ-012)
    bigquery   the executed query
    claude_api the response formatting call
    cache      a cache lookup
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 1 GiB, used to size the seeded BigQuery scan.
_GIB = 1024 ** 3


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


def _guardrail_events(blocking: str | None = None) -> list:
    """Per-rule verdicts for the twelve standing SQL guardrails (SQ-001..SQ-012).

    With no ``blocking`` rule every rule passes; otherwise the named rule fails
    and the rest pass, modelling the guardrail-blocked path.
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
    """A clean NL-to-SQL request that resolves, passes guardrails, and runs.

    The resolver pipeline carries ``has_lazy_children`` so its six steps arrive
    via expand_span_children, exercising the lazy path.
    """
    spans = [
        Span(
            "s0", None, "nl_to_sql.request", "root",
            start_ms=0, duration_ms=2143, status="ok",
            attributes={"service.name": "nl-to-sql", "request.zone": "curated"},
        ),
        Span(
            "s1", "s0", "embed.question", "embedding",
            start_ms=20, duration_ms=95, status="ok",
            attributes={
                "embedding.model": "text-embedding-3-large",
                "embedding.vectors": 1,
                "cache.hit": False,
            },
        ),
        Span(
            "s2", "s0", "resolve.pipeline", "resolver",
            start_ms=130, duration_ms=360, status="ok", has_lazy_children=True,
            attributes={
                "resolver.steps": 6,
                "zone": "curated",
                "resolver.note": "expand to see each resolver step",
            },
        ),
        Span(
            "s3", "s0", "guardrails.evaluate", "guardrail",
            start_ms=500, duration_ms=180, status="ok",
            attributes={
                "guardrail.rule_id": "SQ-001..SQ-012",
                "guardrail.verdict": "pass",
                "guardrail.citation": "policy://sql-governance/all (12 rules)",
            },
            events=_guardrail_events(),
        ),
        Span(
            "s4", "s0", "bigquery.execute", "bigquery",
            start_ms=690, duration_ms=1290, status="ok",
            bytes_scanned=int(1.5 * _GIB), bytes_billed=int(1.5 * _GIB),
            attributes={
                "db.system": "bigquery",
                "db.statement": "SELECT facility_id, AVG(los_days) AS avg_los "
                                "FROM curated.fct_patient_visits "
                                "WHERE discharge_ts >= ... GROUP BY facility_id",
                "bq.job_id": "job_us_8f1c2a",
                "bq.bytes_scanned": int(1.5 * _GIB),
                "bq.bytes_billed": int(1.5 * _GIB),
                "bq.slot_ms": 41020,
                "bq.cache_hit": False,
            },
            links=[{
                "rel": "lineage",
                "tool": "view_lineage",
                "args": {"node": "fct_patient_visits"},
                "label": "View lineage for fct_patient_visits",
            }],
        ),
        Span(
            "s5", "s0", "format.response", "claude_api",
            start_ms=1990, duration_ms=150, status="ok",
            tokens_in=4120, tokens_out=380, cached_tokens_in=3072,
            attributes={
                "gen_ai.system": "anthropic",
                "gen_ai.request.model": "claude-opus-4-8",
                "gen_ai.usage.input_tokens": 4120,
                "gen_ai.usage.output_tokens": 380,
                "gen_ai.usage.cached_input_tokens": 3072,
                "gen_ai.prompt.cache_hit": True,
            },
        ),
        # Lazy resolver steps (parent s2). Hidden on the initial view; revealed
        # by expand_span_children.
        Span(
            "s6", "s2", "resolve.DateResolver", "resolver",
            start_ms=135, duration_ms=40, status="ok",
            attributes={
                "resolver.name": "DateResolver",
                "resolver.match": "last 90 days",
                "resolver.rewrite": "discharge_ts >= DATE_SUB(CURRENT_DATE(), "
                                    "INTERVAL 90 DAY)",
                "resolver.confidence": 0.98,
            },
        ),
        Span(
            "s7", "s2", "resolve.RecencyResolver", "resolver",
            start_ms=178, duration_ms=35, status="ok",
            attributes={
                "resolver.name": "RecencyResolver",
                "resolver.match": "as of latest load",
                "resolver.rewrite": "snapshot_date = (SELECT MAX(snapshot_date) ...)",
                "resolver.confidence": 0.91,
            },
        ),
        Span(
            "s8", "s2", "resolve.GrainResolver", "resolver",
            start_ms=216, duration_ms=30, status="ok",
            attributes={
                "resolver.name": "GrainResolver",
                "resolver.match": "by facility",
                "resolver.rewrite": "GROUP BY facility_id",
                "resolver.confidence": 0.95,
            },
        ),
        Span(
            "s9", "s2", "resolve.ZoneRouter", "resolver",
            start_ms=250, duration_ms=45, status="ok",
            attributes={
                "resolver.name": "ZoneRouter",
                "resolver.match": "patient visit metrics",
                "resolver.rewrite": "route to curated.fct_patient_visits",
                "zone": "curated",
                "resolver.confidence": 0.97,
            },
        ),
        Span(
            "s10", "s2", "resolve.BusinessRuleResolver", "resolver",
            start_ms=298, duration_ms=60, status="ok",
            attributes={
                "resolver.name": "BusinessRuleResolver",
                "resolver.match": "length of stay",
                "resolver.rewrite": "los_days = DATE_DIFF(discharge_ts, admit_ts, DAY)",
                "resolver.confidence": 0.93,
            },
        ),
        Span(
            "s11", "s2", "resolve.AuthorityReranker", "neo4j",
            start_ms=360, duration_ms=120, status="ok",
            attributes={
                "resolver.name": "AuthorityReranker",
                "db.system": "neo4j",
                "db.statement": "MATCH (m:Metric {name:'avg_los'})-[:DERIVES_FROM]->"
                                "(t:Table) RETURN t ORDER BY t.authority DESC",
                "resolver.match": "avg_los -> fct_patient_visits",
                "resolver.rewrite": "prefer conformed fact over staging view",
                "resolver.confidence": 0.99,
            },
            links=[{
                "rel": "lineage",
                "tool": "view_lineage",
                "args": {"node": "avg_los"},
                "label": "View lineage for avg_los",
            }],
        ),
    ]
    return Trace(
        trace_id="trc_ok",
        question="average length of stay by facility for the last 90 days",
        started_at="2026-06-24T17:02:11.482Z",
        wall_ms=2143,
        status="ok",
        spans=spans,
    )


def _trace_blocked() -> Trace:
    """A request blocked at the guardrail stage (SQ-007, direct identifiers).

    Resolution and embedding still run, but evaluation fails and the query is
    never executed, so there is no bigquery or claude_api span. The widget badges
    the blocking guardrail span and treats the trace status as blocked.
    """
    spans = [
        Span(
            "b0", None, "nl_to_sql.request", "root",
            start_ms=0, duration_ms=548, status="error",
            attributes={
                "service.name": "nl-to-sql",
                "request.zone": "curated",
                "request.outcome": "guardrail_blocked",
            },
        ),
        Span(
            "b1", "b0", "embed.question", "embedding",
            start_ms=18, duration_ms=92, status="ok",
            attributes={
                "embedding.model": "text-embedding-3-large",
                "embedding.vectors": 1,
                "cache.hit": True,
            },
        ),
        Span(
            "b2", "b0", "resolve.pipeline", "resolver",
            start_ms=120, duration_ms=240, status="ok",
            attributes={
                "resolver.steps": 6,
                "zone": "curated",
                "resolver.note": "resolved, but blocked downstream",
            },
        ),
        Span(
            "b3", "b0", "guardrails.evaluate", "guardrail",
            start_ms=370, duration_ms=160, status="error",
            attributes={
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
        wall_ms=548,
        status="guardrail_blocked",
        spans=spans,
    )


def build_traces() -> dict[str, Trace]:
    """All seed traces, keyed by trace id."""
    traces = [_trace_ok(), _trace_blocked()]
    return {t.trace_id: t for t in traces}
