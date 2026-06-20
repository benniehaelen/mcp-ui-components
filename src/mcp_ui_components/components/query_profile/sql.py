"""Parse SQL into a compact *query-profile model* — parse-derived analytics.

No execution: every figure is read straight off the ``sqlglot`` AST — distinct
physical tables, joins by type, the operator mix the query-plan control would
draw (counted per scope), the number of CTE/subquery lanes, and a heuristic
complexity score. Shares the small AST helpers in :mod:`...shared.sql` with the
other SQL controls but does not depend on their parsers, so the component stays
self-contained.
"""

from __future__ import annotations

import re
from collections import Counter

import sqlglot
from sqlglot import exp

from ...shared.sql import _from_node, _join_type

# Operator buckets, in the order the query logically executes (mirrors the
# query-plan pipeline so the two controls read consistently).
_OP_ORDER = [
    "scan", "join", "filter", "aggregate", "having",
    "window", "project", "distinct", "sort", "limit",
]


def _has_aggregate(select: exp.Select) -> bool:
    """A GROUP BY, or a non-windowed aggregate in the projection / HAVING."""
    if select.args.get("group") is not None:
        return True
    sources = list(select.expressions)
    having = select.args.get("having")
    if having is not None:
        sources.append(having)
    for node in sources:
        for agg in node.find_all(exp.AggFunc):
            if agg.find_ancestor(exp.Window) is None:
                return True
    return False


def _has_window(select: exp.Select) -> bool:
    """A QUALIFY clause, or a window function in the projection."""
    if select.args.get("qualify") is not None:
        return True
    return any(any(True for _ in proj.find_all(exp.Window)) for proj in select.expressions)


def _scope_ops(select: exp.Select) -> Counter:
    """Tally the operators a single SELECT scope contributes to the pipeline.

    Uses the scope's own clause args (not a deep ``find_all`` over the whole
    tree) so CTE bodies and the main query aren't double-counted.
    """
    ops: Counter = Counter()
    if _from_node(select) is not None:
        ops["scan"] += 1
    ops["join"] += len(select.args.get("joins") or [])
    if select.args.get("where") is not None:
        ops["filter"] += 1
    if _has_aggregate(select):
        ops["aggregate"] += 1
    if select.args.get("having") is not None:
        ops["having"] += 1
    if _has_window(select):
        ops["window"] += 1
    ops["project"] += 1
    if select.args.get("distinct"):
        ops["distinct"] += 1
    if select.args.get("order") is not None:
        ops["sort"] += 1
    if select.args.get("limit") is not None or select.args.get("offset") is not None:
        ops["limit"] += 1
    return ops


def profile_query(
    sql: str,
    nl: str | None = None,
    title: str | None = None,
    dialect: str = "bigquery",
    scan: str | None = None,
) -> dict:
    """Parse ``sql`` into a query-profile model dict.

    Raises ``ValueError`` if the SQL cannot be parsed into anything renderable.
    """
    try:
        root = sqlglot.parse_one(sql, read=dialect)
    except Exception as exc:  # sqlglot raises ParseError subclasses
        raise ValueError(f"could not parse SQL: {exc}") from exc
    if root is None:
        raise ValueError("empty SQL")
    if root.find(exp.Select) is None and not isinstance(
        root, (exp.Union, exp.Intersect, exp.Except)
    ):
        raise ValueError("no SELECT statement found in SQL")

    # lanes: each CTE body + the main query
    with_node = root.args.get("with") or root.args.get("with_")
    ctes = list(with_node.expressions) if with_node else []
    cte_names = {c.alias for c in ctes}
    scopes = [c.this for c in ctes if isinstance(c.this, exp.Select)]
    main = root if isinstance(root, exp.Select) else root.find(exp.Select)
    if main is not None:
        scopes.append(main)

    op_counts: Counter = Counter()
    for scope in scopes:
        op_counts.update(_scope_ops(scope))

    # distinct physical tables (CTE references don't count as tables)
    tables: set[str] = set()
    for t in root.find_all(exp.Table):
        if t.name in cte_names:
            continue
        full = ".".join(p for p in (t.catalog, t.db, t.name) if p)
        tables.add(full or t.name)

    join_counts = Counter(_join_type(j) for j in root.find_all(exp.Join))
    join_total = sum(join_counts.values())
    op_total = sum(op_counts.values())
    block_count = len(scopes)

    # heuristic complexity: tables + joins (weighted) + operators + CTE lanes
    score = len(tables) + 2 * join_total + op_total + 2 * max(0, block_count - 1)
    label = "Low" if score <= 10 else "Medium" if score <= 24 else "High"

    ds_counts = Counter(
        ".".join(p for p in (t.catalog, t.db) if p)
        for t in root.find_all(exp.Table)
        if (t.catalog or t.db)
    )
    dataset = ds_counts.most_common(1)[0][0] if ds_counts else ""
    dates = sorted(set(re.findall(r"\d{4}-\d{2}-\d{2}", sql)))
    period = {"start": dates[0], "end": dates[-1]} if dates else None

    return {
        "title": title or "Query Profile",
        "dataset": dataset,
        "dialect": dialect,
        "nl": nl,
        "sql": sql,
        "scan": scan,
        "period": period,
        "tableCount": len(tables),
        "joinCounts": dict(join_counts),
        "joinTotal": join_total,
        "blockCount": block_count,
        "operatorCount": op_total,
        "operatorCounts": {k: op_counts[k] for k in _OP_ORDER if op_counts.get(k)},
        "complexity": {"score": score, "label": label},
    }
