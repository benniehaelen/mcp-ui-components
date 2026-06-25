# Trace + cost waterfall

The operational twin of the lineage viewer. The lineage viewer answers "where did
this data come from?"; this control answers "how did this one NL-to-SQL request
run, and where did the time and money go?"

Given a single trace (one NL-to-SQL request), `view_trace_waterfall` returns the
request's OpenTelemetry spans as a flat list with `parent_span_id` references and
a `start_ms` offset per span. The widget builds the tree and lays the spans out as
a horizontal waterfall on a shared time axis, colored by `kind` (resolver,
guardrail, bigquery, claude_api, neo4j, embedding, cache, root). The host renders
it inline, and every interaction is proxied back through the host as a governed
MCP tool call, subject to the same auth, guardrails, and traces as a prompt. No
interaction reaches Cloud Trace or BigQuery directly.

## Interaction to proxied-call map

| Interaction in the widget | Proxied call | Result |
| --- | --- | --- |
| Click a span bar | `describe_span` | Details panel for that span |
| Click a span's cost chip | `get_span_cost_breakdown` | Cost decomposition panel |
| `+` on a span with lazy children | `expand_span_children` | Reveals that span's children |
| `-` on a span | none (local collapse) | Collapse that branch |
| Open the trace picker | `list_recent_traces` | Dropdown of recent traces |
| Pick a different trace | `view_trace_waterfall` | Recenter the waterfall on it |
| Toggle the chip dimension (latency / tokens / $) | none (local re-render) | Re-color and re-scale the chips |
| Reset view | none (local) | Restore the initial waterfall |

This is the same call-flow as the lineage viewer; see
[`python-from-typescript.md`](python-from-typescript.md) for the step-by-step path
a `callServerTool` takes from the sandboxed widget, through the host, to the Python
provider and back.

## Seed data

The provider ships two traces so the picker, the lazy-expand path, and the error
treatment are all demonstrable:

- `trc_ok` - "average length of stay by facility for the last 90 days". A clean
  run: embed, resolve, guardrails pass (SQ-001 through SQ-012), BigQuery executes,
  Claude formats the response. The `resolve.pipeline` span carries
  `has_lazy_children`, so its six resolver steps (DateResolver, RecencyResolver,
  GrainResolver, ZoneRouter, BusinessRuleResolver, AuthorityReranker) arrive only
  when the user expands it, via `expand_span_children`.
- `trc_blocked` - blocked at the guardrail stage (SQ-007, no direct identifier
  columns). Resolution still runs, but evaluation fails and the query is never
  executed, so there is no `bigquery.execute` or `format.response` span. The widget
  badges the blocking guardrail span and treats the trace as blocked.

## Cost model

Each span carries the raw cost inputs (tokens and bytes); the provider turns them
into dollars with one rate card so the roll-ups stay internally consistent
(`total_cost_usd` always equals the Claude part plus the BigQuery part). The rate
cards (Claude per-MTok input / cached-input / output, BigQuery per-TiB billed) live
in `provider.py` config, never in the widget, so they are easy to update.

## Production adapter (documented, not wired)

The seed `TraceProvider` is the only data source in this repo. In production the
same `get_trace` / `expand` / `describe_span` / `cost_breakdown` / `list_recent`
interface would be backed by:

- the OpenTelemetry span export landed in BigQuery (`_AllSpans`), optionally
  cross-checked against Cloud Trace, for the span tree;
- `mcp_usage_log` for token cost;
- `INFORMATION_SCHEMA.JOBS_BY_PROJECT`, keyed by the job id captured on the
  `bigquery.execute` span, for bytes billed and slot ms.

Everything crosses the governance boundary first: de-identification and zone
filtering are applied before any attribute (especially `db.statement`) reaches the
widget, exactly as in the lineage provider contract.

## Screenshots

Live VS Code / offline-host screenshots (light and dark) are a follow-up; capture
them by running `python demo/host.py` and opening
`http://localhost:8080/?tool=view_trace_waterfall&call=true`.
