"""Trace + cost provider.

The provider is the only thing that touches trace data. In production this is the
box sitting behind the governance boundary that reads the OpenTelemetry span
export and the cost logs:

    get_trace(trace_id)               -> the span waterfall for one request
    describe_span(span_id)            -> full OTel detail for one span
    cost_breakdown(span_id, scope)    -> the dollar decomposition for a span/subtree
    expand(span_id, visible_span_ids) -> a lazy span's not-yet-shown children
    list_recent(limit, status)        -> recent traces for the picker

Returned payloads are plain JSON-serialisable dicts so they flow unchanged through
the MCP tool result and out to the widget.

Production adapter (documented, not wired):
  * Spans come from the OpenTelemetry export landed in BigQuery (``_AllSpans``),
    optionally cross-checked against Cloud Trace.
  * Token cost joins from ``mcp_usage_log``.
  * BigQuery bytes billed and slot ms come from
    ``INFORMATION_SCHEMA.JOBS_BY_PROJECT``, keyed by the job id captured on the
    ``bigquery.execute`` span.
  * Everything crosses the governance boundary first: de-identification and zone
    filtering are applied before any attribute (especially ``db.statement``)
    reaches the widget. The view / expand / describe interface is exactly what
    that adapter would implement, identical to the lineage provider contract.

Rate cards live here (config), never in the widget, so they are easy to update.
"""

from __future__ import annotations

from .data import FOCUS_TRACE_ID, Span, Trace, build_traces

# Rate cards. Claude per-million-token rates (input, cached input, output) and
# the BigQuery on-demand rate per tebibyte billed. Update here, not in the widget.
RATE_CARD = {
    "in_per_mtok_usd": 3.0,
    "cached_in_per_mtok_usd": 0.3,
    "out_per_mtok_usd": 15.0,
}
BQ_RATE_PER_TIB_USD = 5.0

_MTOK = 1_000_000
_TIB = 1024 ** 4


class TraceProvider:
    def __init__(self, traces: dict[str, Trace] | None = None) -> None:
        self._traces = traces or build_traces()
        # Span ids are globally unique across the seed traces, so one flat index
        # lets describe/cost/expand take just a span id (per the tool schemas).
        self._index: dict[str, tuple[str, Span]] = {}
        for trace in self._traces.values():
            for span in trace.spans:
                self._index[span.span_id] = (trace.trace_id, span)

    # -- cost helpers ------------------------------------------------------
    def _claude_cost(self, tokens_in: int, cached_in: int, tokens_out: int) -> float:
        uncached_in = max(0, tokens_in - cached_in)
        cost = (
            uncached_in / _MTOK * RATE_CARD["in_per_mtok_usd"]
            + cached_in / _MTOK * RATE_CARD["cached_in_per_mtok_usd"]
            + tokens_out / _MTOK * RATE_CARD["out_per_mtok_usd"]
        )
        return round(cost, 6)

    def _bq_cost(self, bytes_billed: int) -> float:
        return round(bytes_billed / _TIB * BQ_RATE_PER_TIB_USD, 6)

    def _span_cost(self, span: Span) -> dict:
        """What a single span is responsible for (not its subtree)."""
        token_cost = self._claude_cost(
            span.tokens_in, span.cached_tokens_in, span.tokens_out)
        bq = self._bq_cost(span.bytes_billed)
        return {
            "token_cost_usd": token_cost,
            "bytes_billed": span.bytes_billed,
            "total_cost_usd": round(token_cost + bq, 6),
        }

    def _totals(self, trace: Trace) -> dict:
        tokens_in = sum(s.tokens_in for s in trace.spans)
        tokens_out = sum(s.tokens_out for s in trace.spans)
        cached_in = sum(s.cached_tokens_in for s in trace.spans)
        bytes_scanned = sum(s.bytes_scanned for s in trace.spans)
        bytes_billed = sum(s.bytes_billed for s in trace.spans)
        token_cost = self._claude_cost(tokens_in, cached_in, tokens_out)
        bq = self._bq_cost(bytes_billed)
        return {
            "token_cost_usd": token_cost,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cached_tokens_in": cached_in,
            "bytes_scanned": bytes_scanned,
            "bytes_billed": bytes_billed,
            "bigquery_cost_usd": bq,
            "total_cost_usd": round(token_cost + bq, 6),
        }

    # -- shape helpers -----------------------------------------------------
    def _span_payload(self, span: Span) -> dict:
        return {
            "span_id": span.span_id,
            "parent_span_id": span.parent_span_id,
            "name": span.name,
            "kind": span.kind,
            "start_ms": span.start_ms,
            "duration_ms": span.duration_ms,
            "status": span.status,
            "has_lazy_children": span.has_lazy_children,
            # Raw token total so the widget can re-scale the "tokens" dimension
            # locally without a round-trip; dollars come from cost.
            "tokens": span.tokens_in + span.tokens_out,
            "cost": self._span_cost(span),
        }

    def _lazy_parent_ids(self, trace: Trace) -> set[str]:
        return {s.span_id for s in trace.spans if s.has_lazy_children}

    def _resolve(self, trace_id: str | None) -> Trace:
        if not trace_id:
            return self._most_recent()
        try:
            return self._traces[trace_id]
        except KeyError:
            raise KeyError(f"unknown trace: {trace_id!r}")

    def _most_recent(self) -> Trace:
        ordered = sorted(
            self._traces.values(), key=lambda t: t.started_at, reverse=True)
        return ordered[0] if ordered else self._traces[FOCUS_TRACE_ID]

    def _find(self, span_id: str) -> tuple[Trace, Span]:
        try:
            trace_id, span = self._index[span_id]
        except KeyError:
            raise KeyError(f"unknown span: {span_id!r}")
        return self._traces[trace_id], span

    def _subtree(self, trace: Trace, root_id: str) -> list[Span]:
        """The span plus every descendant, by walking parent references."""
        by_parent: dict[str | None, list[Span]] = {}
        for s in trace.spans:
            by_parent.setdefault(s.parent_span_id, []).append(s)
        out: list[Span] = []
        stack = [root_id]
        while stack:
            sid = stack.pop()
            _, span = self._index[sid]
            out.append(span)
            stack.extend(child.span_id for child in by_parent.get(sid, []))
        return out

    # -- public API --------------------------------------------------------
    def get_trace(self, trace_id: str | None = None) -> dict:
        """The waterfall payload for one request.

        Children of a span flagged ``has_lazy_children`` are withheld; the widget
        fetches them on demand via :meth:`expand`.
        """
        trace = self._resolve(trace_id)
        lazy = self._lazy_parent_ids(trace)
        visible = [s for s in trace.spans if s.parent_span_id not in lazy]
        return {
            "trace_id": trace.trace_id,
            "question": trace.question,
            "started_at": trace.started_at,
            "wall_ms": trace.wall_ms,
            "status": trace.status,
            "totals": self._totals(trace),
            "spans": [self._span_payload(s) for s in visible],
        }

    def describe_span(self, span_id: str) -> dict:
        """Full OTel-shaped detail for one span (the click-a-span interaction)."""
        trace, span = self._find(span_id)
        return {
            "span_id": span.span_id,
            "trace_id": trace.trace_id,
            "name": span.name,
            "kind": span.kind,
            "status": span.status,
            "start_ms": span.start_ms,
            "duration_ms": span.duration_ms,
            "attributes": dict(span.attributes),
            "events": [dict(e) for e in span.events],
            "cost": self._span_cost(span),
            "links": [dict(link) for link in span.links],
        }

    def cost_breakdown(self, span_id: str, scope: str = "span") -> dict:
        """Decompose a span's (or its subtree's) cost into Claude + BigQuery."""
        if scope not in ("span", "subtree"):
            raise ValueError("scope must be 'span' or 'subtree'")
        trace, span = self._find(span_id)
        members = self._subtree(trace, span_id) if scope == "subtree" else [span]

        tokens_in = sum(s.tokens_in for s in members)
        tokens_out = sum(s.tokens_out for s in members)
        cached_in = sum(s.cached_tokens_in for s in members)
        uncached_in = max(0, tokens_in - cached_in)
        bytes_scanned = sum(s.bytes_scanned for s in members)
        bytes_billed = sum(s.bytes_billed for s in members)

        claude_cost = self._claude_cost(tokens_in, cached_in, tokens_out)
        bq_cost = self._bq_cost(bytes_billed)
        return {
            "span_id": span_id,
            "scope": scope,
            "claude_api": {
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cached_tokens_in": cached_in,
                "uncached_tokens_in": uncached_in,
                "rate_card": {
                    "in_per_mtok_usd": RATE_CARD["in_per_mtok_usd"],
                    "cached_in_per_mtok_usd": RATE_CARD["cached_in_per_mtok_usd"],
                    "out_per_mtok_usd": RATE_CARD["out_per_mtok_usd"],
                },
                "cost_usd": claude_cost,
            },
            "bigquery": {
                "bytes_scanned": bytes_scanned,
                "bytes_billed": bytes_billed,
                "tib_billed": round(bytes_billed / _TIB, 6),
                "rate_per_tib_usd": BQ_RATE_PER_TIB_USD,
                "cost_usd": bq_cost,
            },
            "total_cost_usd": round(claude_cost + bq_cost, 6),
        }

    def expand(self, span_id: str, visible_span_ids: list[str] | None = None) -> dict:
        """Reveal a span's not-yet-shown direct children (the + affordance)."""
        trace, _ = self._find(span_id)
        visible = set(visible_span_ids or [])
        children = [
            s for s in trace.spans
            if s.parent_span_id == span_id and s.span_id not in visible
        ]
        return {"spans": [self._span_payload(s) for s in children]}

    def list_recent(self, limit: int = 10, status: str = "any") -> list[dict]:
        """Recent traces for the picker, newest first."""
        limit = max(1, min(int(limit or 10), 50))
        ordered = sorted(
            self._traces.values(), key=lambda t: t.started_at, reverse=True)
        if status and status != "any":
            ordered = [t for t in ordered if t.status == status]
        return [
            {
                "trace_id": t.trace_id,
                "question": t.question,
                "started_at": t.started_at,
                "wall_ms": t.wall_ms,
                "status": t.status,
                "total_cost_usd": self._totals(t)["total_cost_usd"],
            }
            for t in ordered[:limit]
        ]
