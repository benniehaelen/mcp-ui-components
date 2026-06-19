"""Small ``sqlglot`` AST helpers shared by both SQL parsers.

The join-diagram parser (:mod:`...components.join_diagram.sql`) and the query-plan
parser (:mod:`...components.query_plan.sql`) reason about the same SQL trees, so the
leaf helpers and the card/lane colour palette live here to keep the two parsers
consistent.
"""

from __future__ import annotations

from sqlglot import exp

# Per-card / per-lane accent + header colours, assigned by index. Mirrors the
# palette of the original hand-authored diagram (blue spine, then green/amber/pink…).
_PALETTE = [
    {"accent": "#3b82f6", "header": "#1d4ed8"},  # blue   (spine)
    {"accent": "#22c55e", "header": "#15803d"},  # green
    {"accent": "#eab308", "header": "#a16207"},  # amber
    {"accent": "#ec4899", "header": "#9d174d"},  # pink
    {"accent": "#8b5cf6", "header": "#6d28d9"},  # violet
    {"accent": "#06b6d4", "header": "#0e7490"},  # cyan
    {"accent": "#f97316", "header": "#c2410c"},  # orange
    {"accent": "#14b8a6", "header": "#0f766e"},  # teal
]


def _from_node(select: exp.Select):
    """The FROM clause of a SELECT (sqlglot uses the ``from``/``from_`` key)."""
    return select.args.get("from") or select.args.get("from_")


def _split_and(node) -> list:
    """Flatten a chain of ``AND`` predicates into a list of leaf predicates."""
    if isinstance(node, exp.And):
        return _split_and(node.this) + _split_and(node.expression)
    return [node]


def _join_type(join: exp.Join) -> str:
    """Normalise a join to INNER / LEFT / RIGHT / FULL."""
    side = (join.args.get("side") or "").upper()
    if side:
        return side
    kind = (join.args.get("kind") or "").upper()
    return kind or "INNER"


def _strip_aliases(node, dialect: str) -> str:
    """Render a predicate without ``alias.`` qualifiers, e.g. ``status = 1``."""
    clone = node.copy()
    for col in clone.find_all(exp.Column):
        col.set("table", None)
    return clone.sql(dialect=dialect)


def _dedup_append(items: list, value) -> None:
    if value is not None and value not in items:
        items.append(value)
