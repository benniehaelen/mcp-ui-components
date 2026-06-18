"""Parse SQL into a *join-diagram model* the widget can render.

The lineage viewer renders a hand-built graph; the join diagram instead renders a
model **derived from a SQL query**. Given a (BigQuery-flavoured) SQL string this
module produces a JSON-serialisable dict describing:

  * one **card per physical table** (join keys, selected columns, derived outputs,
    filters), and
  * one **join per relationship** between those cards.

A query that uses CTEs is *flattened*: each CTE is collapsed onto the physical
table that drives it (its ``FROM`` table — the "representative"), so a join
written between two CTEs is drawn between the underlying tables. This reproduces
the layout of the original hand-authored diagram, where every connector fans out
from the spine table.

Backed by ``sqlglot`` (pure-Python, multi-dialect). The model flows unchanged
through the MCP tool result out to the widget.

First-cut scope: ``WITH`` CTEs + the top-level ``SELECT``, equi-joins via
``ON``/``USING``, ``INNER``/``LEFT``/``RIGHT``/``FULL`` joins, one driving table
per scope. Derived-table subqueries, set operations and non-equi joins are
ignored gracefully rather than raising.
"""

from __future__ import annotations

import re
from collections import Counter

import sqlglot
from sqlglot import exp

# Per-card accent + header colours, assigned by card index. Mirrors the palette
# of the original hand-authored diagram (blue spine, then green/amber/pink…).
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


# ---------------------------------------------------------------------------
# Small AST helpers
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def parse_join_diagram(
    sql: str,
    nl: str | None = None,
    title: str | None = None,
    dialect: str = "bigquery",
    scan: str | None = None,
) -> dict:
    """Parse ``sql`` into a join-diagram model dict.

    Raises ``ValueError`` if the SQL cannot be parsed at all.
    """
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception as exc:  # sqlglot raises ParseError subclasses
        raise ValueError(f"could not parse SQL: {exc}") from exc

    if not isinstance(tree, exp.Select):
        # e.g. a top-level UNION — fall back to its first SELECT so we still
        # render something useful rather than failing outright.
        found = tree.find(exp.Select)
        if found is None:
            raise ValueError("no SELECT statement found in SQL")
        tree = found

    ctes = list(tree.find_all(exp.CTE))
    cte_by_name = {c.alias: c for c in ctes}
    cte_names = set(cte_by_name)

    # --- physical-table card registry --------------------------------------
    cards: dict[str, dict] = {}   # fullName -> card
    order: list[str] = []         # fullName, first-seen order (drives ids/colours)

    def _full_name(t: exp.Table) -> str:
        return ".".join(p for p in (t.catalog, t.db, t.name) if p)

    def ensure_card(t: exp.Table) -> dict:
        full = _full_name(t)
        if full not in cards:
            idx = len(order)
            pal = _PALETTE[idx % len(_PALETTE)]
            cards[full] = {
                "id": f"t{idx}",
                "name": t.name,
                "fullName": full,
                "dataset": ".".join(p for p in (t.catalog, t.db) if p),
                "alias": t.alias or None,
                "cte": None,
                "role": "",          # "spine" for the driving table
                "joinType": None,    # INNER/LEFT/… that brought this table in
                "accent": pal["accent"],
                "header": pal["header"],
                "joinKeys": [],
                "selectedColumns": [],
                "derivedOutputs": [],   # {name, expr}
                "filters": [],          # {column, expr, clause}
            }
            order.append(full)
        return cards[full]

    # Resolve a source table to its physical card, following CTE references to
    # the table that drives the referenced CTE (its representative).
    _resolving: set[str] = set()

    def resolve_source(t):
        if not isinstance(t, exp.Table):
            return None
        name = t.name
        if name in cte_names and name not in _resolving:
            _resolving.add(name)
            try:
                inner_from = _from_node(cte_by_name[name].this)
                if inner_from is not None:
                    return resolve_source(inner_from.this)
                return None
            finally:
                _resolving.discard(name)
        return ensure_card(t)

    # --- walk every scope (each CTE body + the main query) -----------------
    diagram_joins: list[dict] = []
    scopes: list[tuple[str | None, exp.Select]] = [(c.alias, c.this) for c in ctes]
    scopes.append((None, tree))

    for scope_name, select in scopes:
        from_node = _from_node(select)
        if from_node is None:
            continue
        driving = from_node.this

        # alias -> physical card for this scope
        alias_to_card: dict[str, dict] = {}
        driving_card = resolve_source(driving)
        if isinstance(driving, exp.Table) and driving_card is not None:
            alias_to_card[driving.alias or driving.name] = driving_card

        joins = select.args.get("joins") or []
        for join in joins:
            jt = join.this
            if isinstance(jt, exp.Table):
                jc = resolve_source(jt)
                if jc is not None:
                    alias_to_card[jt.alias or jt.name] = jc

        # Stamp the CTE membership on every card seen in this scope.
        if scope_name:
            for card in alias_to_card.values():
                if card["cte"] is None:
                    card["cte"] = scope_name

        # ---- joins (always processed; this is what the diagram draws) ------
        for join in joins:
            jt = join.this
            to_card = alias_to_card.get(jt.alias or jt.name) if isinstance(jt, exp.Table) else None
            if to_card is None or driving_card is None:
                continue
            jtype = _join_type(join)
            keys: list[str] = []

            on = join.args.get("on")
            if on is not None:
                for eq in on.find_all(exp.EQ):
                    left, right = eq.this, eq.expression
                    # the diagram key list (shared column names)
                    if isinstance(left, exp.Column):
                        _dedup_append(keys, left.name)
                    elif isinstance(right, exp.Column):
                        _dedup_append(keys, right.name)
                    # attribute each side to its card's Join Keys section
                    for side in (left, right):
                        if isinstance(side, exp.Column) and side.table in alias_to_card:
                            _dedup_append(alias_to_card[side.table]["joinKeys"], side.name)

            using = join.args.get("using")
            if using:
                for ident in using:
                    name = ident.name
                    _dedup_append(keys, name)
                    _dedup_append(driving_card["joinKeys"], name)
                    _dedup_append(to_card["joinKeys"], name)

            if to_card["joinType"] is None:
                to_card["joinType"] = jtype

            diagram_joins.append({
                "from": driving_card["id"],
                "to": to_card["id"],
                "fromName": driving_card["name"],
                "toName": to_card["name"],
                "keys": keys,
                "type": jtype,
                "color": to_card["accent"],
            })

        # ---- columns + filters --------------------------------------------
        # Only attribute projections/filters from CTE bodies (or from the main
        # query when there are no CTEs). The final SELECT's outputs are report
        # columns, not table-level columns, so they are not shown on cards.
        process_columns = scope_name is not None or not ctes
        if not process_columns:
            continue

        for proj in select.expressions:
            out_name = proj.alias_or_name
            inner = proj.this if isinstance(proj, exp.Alias) else proj
            if isinstance(inner, exp.Column):
                card = alias_to_card.get(inner.table)
                if card is not None:
                    _dedup_append(card["selectedColumns"], out_name or inner.name)
            else:
                tallies = Counter(c.table for c in inner.find_all(exp.Column) if c.table)
                if tallies:
                    primary = tallies.most_common(1)[0][0]
                    card = alias_to_card.get(primary)
                    if card is not None:
                        card["derivedOutputs"].append(
                            {"name": out_name, "expr": inner.sql(dialect=dialect)}
                        )

        where = select.args.get("where")
        if where is not None:
            for pred in _split_and(where.this):
                tables = {c.table for c in pred.find_all(exp.Column) if c.table}
                targets = {alias_to_card[t]["id"]: alias_to_card[t]
                           for t in tables if t in alias_to_card}
                if len(targets) != 1:
                    continue  # cross-table or unknown predicate — skip
                card = next(iter(targets.values()))
                first_col = next(iter(pred.find_all(exp.Column)), None)
                card["filters"].append({
                    "column": first_col.name if first_col else "",
                    "expr": _strip_aliases(pred, dialect),
                    "clause": "WHERE",
                })

    # --- spine + metadata --------------------------------------------------
    main_from = _from_node(tree)
    if main_from is not None:
        spine = resolve_source(main_from.this)
        if spine is not None:
            spine["role"] = "spine"

    table_list = [cards[full] for full in order]
    dataset = ""
    ds_counts = Counter(c["dataset"] for c in table_list if c["dataset"])
    if ds_counts:
        dataset = ds_counts.most_common(1)[0][0]

    dates = sorted(set(re.findall(r"\d{4}-\d{2}-\d{2}", sql)))
    period = {"start": dates[0], "end": dates[-1]} if dates else None

    for j in diagram_joins:
        keys = " + ".join(j["keys"]) if j["keys"] else "—"
        j["label"] = f"{j['type']} JOIN · {j['fromName']} → {j['toName']} · ON {keys}"

    return {
        "title": title or "Join Diagram",
        "dataset": dataset,
        "tableCount": len(table_list),
        "joinCounts": dict(Counter(j["type"] for j in diagram_joins)),
        "scan": scan,
        "period": period,
        "dialect": dialect,
        "nl": nl,
        "sql": sql,
        "tables": table_list,
        "joins": diagram_joins,
    }
