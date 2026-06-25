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

The request root has the twelve pipeline steps as its children, in order:

| # | Step | Kind | Notes |
| --- | --- | --- | --- |
| 1 | Discovery | embedding | vector search the top-k candidate tables |
| 2 | Route Zones | resolver | pick the governed data zone to read from |
| 3 | Query Planning | resolver | decompose the question into a plan |
| 4 | Domain Disambiguation | resolver | map ambiguous terms to canonical fields |
| 5 | Resolve Joins with KG | neo4j | resolve the join graph (lazy KG sub-lookups) |
| 6 | Recency Resolution | resolver | pin the request to the latest load |
| 7 | Grain Resolution | resolver | choose the group-by grain |
| 8 | Business Rules | resolver | apply metric definitions (late plan) |
| 9 | Date Resolution | resolver | rewrite the relative date window |
| 10 | Generate SQL | claude_api | the LLM call that emits the SQL (token cost) |
| 11 | Validate (Dry Run) | guardrail | BigQuery dry run + SQ-001..SQ-012 (billed nothing) |
| 12 | Execute SQL | bigquery | the billed BigQuery run (bytes cost) |

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
  run: all twelve steps execute end to end, the dry-run validation passes every
  guardrail (SQ-001 through SQ-012), and `Execute SQL` runs. Step 5, `Resolve
  Joins with KG`, carries `has_lazy_children`, so its three per-table
  knowledge-graph lookups (`kg.resolve.encounters`, `kg.resolve.patients`,
  `kg.resolve.stg_charges`) arrive only when the user expands it, via
  `expand_span_children`.
- `trc_blocked` - blocked at the dry-run validation step (SQ-007, no direct
  identifier columns). The pipeline runs through `Generate SQL`, but `Validate
  (Dry Run)` fails the guardrail, so `Execute SQL` never runs. The widget badges
  the blocking step and treats the trace as blocked.

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
