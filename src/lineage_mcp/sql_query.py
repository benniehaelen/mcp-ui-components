"""Parse SQL into a *query-plan model* — a logical execution pipeline.

Where :mod:`sql_joins` extracts only the join graph, this module decomposes an
*entire* SQL statement into the operators an experienced SQL user reasons about,
laid out in the order the query logically executes:

    scan → join → filter (WHERE) → aggregate (GROUP BY) → having → window
         → project (SELECT) → distinct → sort (ORDER BY) → limit

Each CTE / derived subquery / set-operation arm becomes its own **lane** (block);
a lane that another lane reads from is wired to the consuming operator with a
**cross-edge**, so the whole statement reads as an EXPLAIN-style dataflow.

Backed by ``sqlglot`` (pure-Python, multi-dialect). The model is JSON-serialisable
and flows unchanged through the MCP tool result out to the ``query-plan`` widget.

Reuses the small AST helpers from :mod:`sql_joins` (``_from_node``, ``_split_and``,
``_join_type``, ``_strip_aliases``, ``_dedup_append``, ``_PALETTE``) so the two
parsers stay consistent. Constructs it cannot model (recursive CTEs, exotic FROM
sources) degrade to partial operators rather than raising.
"""

from __future__ import annotations

import re
from collections import Counter

import sqlglot
from sqlglot import exp

from .sql_joins import (
    _PALETTE,
    _dedup_append,
    _from_node,
    _join_type,
    _split_and,
    _strip_aliases,
)


def _setop_title(node) -> str:
    """A human label for a set operation (UNION ALL / UNION / INTERSECT / EXCEPT)."""
    if isinstance(node, exp.Union):
        return "UNION" if node.args.get("distinct") else "UNION ALL"
    if isinstance(node, exp.Intersect):
        return "INTERSECT"
    if isinstance(node, exp.Except):
        return "EXCEPT"
    return "SET OP"


def parse_query_plan(
    sql: str,
    nl: str | None = None,
    title: str | None = None,
    dialect: str = "bigquery",
    scan: str | None = None,
) -> dict:
    """Parse ``sql`` into a query-plan model dict.

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

    blocks: list[dict] = []
    edges: list[dict] = []
    registry: dict[str, str] = {}   # CTE / derived alias -> block id
    counter = [0]

    def new_bid() -> str:
        bid = f"b{counter[0]}"
        counter[0] += 1
        return bid

    def accent(bid: str) -> str:
        return _PALETTE[int(bid[1:]) % len(_PALETTE)]["accent"]

    def make_op(otype, opid, opt_title, detail=None, tables=None, columns=None,
                source_ref=None, badge=None) -> dict:
        return {
            "id": opid, "type": otype, "title": opt_title,
            "detail": detail or [], "tables": tables or [], "columns": columns or [],
            "sourceRef": source_ref, "badge": badge,
        }

    # ---- per-operator builders --------------------------------------------
    def scan_op(src, bid: str, ops: list) -> dict:
        opid = f"{bid}.op{len(ops)}"
        if isinstance(src, exp.Subquery):
            alias = src.alias or f"subq{counter[0]}"
            dbid = new_bid()
            build_block(src.this, dbid, "derived", alias)
            registry[alias] = dbid
            edges.append({"from": dbid, "toBlock": bid, "toOp": opid,
                          "label": alias, "color": accent(dbid)})
            return make_op("scan", opid, f"Scan {alias}", [], [alias], [],
                           source_ref=alias, badge="DERIVED")
        if isinstance(src, exp.Table):
            name, alias = src.name, src.alias
            disp = f"{name} {alias}" if alias and alias != name else name
            if name in registry:
                pbid = registry[name]
                edges.append({"from": pbid, "toBlock": bid, "toOp": opid,
                              "label": name, "color": accent(pbid)})
                return make_op("scan", opid, f"Scan {disp}", [], [name], [],
                               source_ref=name, badge="CTE")
            return make_op("scan", opid, f"Scan {disp}", [], [name], [], badge="TABLE")
        return make_op("scan", opid, f"Scan {src.sql(dialect=dialect)[:40]}", [], [], [])

    def join_op(join, bid: str, ops: list) -> dict:
        opid = f"{bid}.op{len(ops)}"
        jt = join.this
        jtype = _join_type(join)
        detail: list[str] = []
        cols: list[str] = []
        tables: list[str] = []
        source_ref = None

        if isinstance(jt, exp.Subquery):
            name = jt.alias or f"subq{counter[0]}"
            dbid = new_bid()
            build_block(jt.this, dbid, "derived", name)
            registry[name] = dbid
            edges.append({"from": dbid, "toBlock": bid, "toOp": opid,
                          "label": name, "color": accent(dbid)})
            tables = [name]
            source_ref = name
            disp = name
        elif isinstance(jt, exp.Table):
            name, alias = jt.name, jt.alias
            disp = f"{name} {alias}" if alias and alias != name else name
            tables = [name]
            if name in registry:
                pbid = registry[name]
                source_ref = name
                edges.append({"from": pbid, "toBlock": bid, "toOp": opid,
                              "label": name, "color": accent(pbid)})
        else:
            disp = jt.sql(dialect=dialect)[:30]

        on = join.args.get("on")
        if on is not None:
            for c in on.find_all(exp.Column):
                _dedup_append(cols, c.name)
            detail.append(f"ON {_strip_aliases(on, dialect)}")
        using = join.args.get("using")
        if using:
            keys = [ident.name for ident in using]
            for k in keys:
                _dedup_append(cols, k)
            detail.append(f"USING ({', '.join(keys)})")

        return make_op("join", opid, f"{jtype} JOIN {disp}", detail, tables, cols,
                       source_ref=source_ref, badge=jtype)

    def filter_op(where, bid: str, ops: list) -> dict:
        opid = f"{bid}.op{len(ops)}"
        preds = _split_and(where.this)
        detail = [_strip_aliases(p, dialect) for p in preds]
        cols: list[str] = []
        tables: list[str] = []
        for p in preds:
            for c in p.find_all(exp.Column):
                _dedup_append(cols, c.name)
                if c.table:
                    _dedup_append(tables, c.table)
        return make_op("filter", opid, "WHERE", detail, tables, cols)

    def aggregate_op(group, aggs, bid: str, ops: list) -> dict:
        opid = f"{bid}.op{len(ops)}"
        detail: list[str] = []
        cols: list[str] = []
        if group is not None:
            for g in group.expressions:
                detail.append(f"by {g.sql(dialect=dialect)}")
                for c in g.find_all(exp.Column):
                    _dedup_append(cols, c.name)
        detail.extend(aggs)
        title_txt = "GROUP BY" if group is not None else "AGGREGATE"
        return make_op("aggregate", opid, title_txt, detail, [], cols)

    def having_op(having, bid: str, ops: list) -> dict:
        opid = f"{bid}.op{len(ops)}"
        detail = [_strip_aliases(p, dialect) for p in _split_and(having.this)]
        return make_op("having", opid, "HAVING", detail, [], [])

    def window_op(wins, qualify, bid: str, ops: list) -> dict:
        opid = f"{bid}.op{len(ops)}"
        detail = list(wins)
        if qualify is not None:
            detail.append("QUALIFY " + _strip_aliases(qualify.this, dialect))
        return make_op("window", opid, "WINDOW", detail, [], [])

    def project_op(select, bid: str, ops: list):
        opid = f"{bid}.op{len(ops)}"
        outs: list[str] = []
        detail: list[str] = []
        cols: list[str] = []
        for proj in select.expressions:
            out_name = proj.alias_or_name
            inner = proj.this if isinstance(proj, exp.Alias) else proj
            outs.append(out_name or inner.sql(dialect=dialect))
            if isinstance(inner, exp.Column):
                detail.append(out_name or inner.name)
            else:
                detail.append(f"{out_name} := {inner.sql(dialect=dialect)}")
            for c in proj.find_all(exp.Column):
                _dedup_append(cols, c.name)
        n = len(outs)
        title_txt = f"SELECT {n} col" + ("" if n == 1 else "s")
        return make_op("project", opid, title_txt, detail, [], cols), outs

    def distinct_op(bid: str, ops: list) -> dict:
        opid = f"{bid}.op{len(ops)}"
        return make_op("distinct", opid, "DISTINCT", ["rows deduplicated"], [], [],
                       badge="DISTINCT")

    def sort_op(order, bid: str, ops: list) -> dict:
        opid = f"{bid}.op{len(ops)}"
        detail = [o.sql(dialect=dialect) for o in order.expressions]
        cols: list[str] = []
        for c in order.find_all(exp.Column):
            _dedup_append(cols, c.name)
        return make_op("sort", opid, "ORDER BY", detail, [], cols)

    def limit_op(limit, offset, bid: str, ops: list) -> dict:
        opid = f"{bid}.op{len(ops)}"
        detail: list[str] = []
        if limit is not None and limit.expression is not None:
            detail.append("LIMIT " + limit.expression.sql(dialect=dialect))
        if offset is not None and offset.expression is not None:
            detail.append("OFFSET " + offset.expression.sql(dialect=dialect))
        return make_op("limit", opid, "LIMIT", detail, [], [])

    # ---- block builders ----------------------------------------------------
    def _agg_exprs(select) -> list:
        seen: list[str] = []
        for agg in select.find_all(exp.AggFunc):
            # a windowed aggregate (SUM(x) OVER …) belongs to the WINDOW stage,
            # not the GROUP BY stage — skip it here.
            if agg.find_ancestor(exp.Window) is not None:
                continue
            _dedup_append(seen, agg.sql(dialect=dialect))
        return seen

    def _window_specs(select) -> list:
        seen: list[str] = []
        for w in select.find_all(exp.Window):
            _dedup_append(seen, w.sql(dialect=dialect))
        return seen

    def build_block(select, bid: str, kind: str, name: str) -> None:
        ops: list[dict] = []
        from_node = _from_node(select)
        if from_node is not None:
            ops.append(scan_op(from_node.this, bid, ops))
        for join in select.args.get("joins") or []:
            ops.append(join_op(join, bid, ops))
        where = select.args.get("where")
        if where is not None:
            ops.append(filter_op(where, bid, ops))
        group = select.args.get("group")
        aggs = _agg_exprs(select)
        if group is not None or aggs:
            ops.append(aggregate_op(group, aggs, bid, ops))
        having = select.args.get("having")
        if having is not None:
            ops.append(having_op(having, bid, ops))
        wins, qualify = _window_specs(select), select.args.get("qualify")
        if wins or qualify is not None:
            ops.append(window_op(wins, qualify, bid, ops))
        proj, outs = project_op(select, bid, ops)
        ops.append(proj)
        if select.args.get("distinct"):
            ops.append(distinct_op(bid, ops))
        order = select.args.get("order")
        if order is not None:
            ops.append(sort_op(order, bid, ops))
        limit, offset = select.args.get("limit"), select.args.get("offset")
        if limit is not None or offset is not None:
            ops.append(limit_op(limit, offset, bid, ops))

        pal = _PALETTE[int(bid[1:]) % len(_PALETTE)]
        sql_terms: list[str] = []
        for o in ops:
            for t in o["tables"]:
                _dedup_append(sql_terms, t)
        label = {
            "cte": f"CTE {name}", "main": "Main query",
            "derived": f"Subquery {name}", "setop": name,
        }.get(kind, name)
        blocks.append({
            "id": bid, "kind": kind, "name": name, "label": label,
            "accent": pal["accent"], "header": pal["header"],
            "operators": ops, "outputColumns": outs, "sqlTerms": sql_terms,
        })

    def build_setop(node) -> str:
        left_bid = build_query(node.this, "main", "query")
        right_bid = build_query(node.expression, "main", "query")
        sbid = new_bid()
        label = _setop_title(node)
        op = make_op("setop", f"{sbid}.op0", label, [label], [], [])
        for abid in (left_bid, right_bid):
            edges.append({"from": abid, "toBlock": sbid, "toOp": op["id"],
                          "label": label, "color": accent(abid)})
        pal = _PALETTE[int(sbid[1:]) % len(_PALETTE)]
        blocks.append({
            "id": sbid, "kind": "setop", "name": label, "label": label,
            "accent": pal["accent"], "header": pal["header"],
            "operators": [op], "outputColumns": [], "sqlTerms": [],
        })
        return sbid

    def build_query(node, kind: str, name: str) -> str:
        if isinstance(node, (exp.Union, exp.Intersect, exp.Except)):
            return build_setop(node)
        bid = new_bid()
        build_block(node, bid, kind, name)
        return bid

    # ---- enumerate lanes: CTEs (declaration order), then the main body -----
    with_node = root.args.get("with") or root.args.get("with_")  # sqlglot key quirk
    for cte in (with_node.expressions if with_node else []):
        bid = new_bid()
        build_block(cte.this, bid, "cte", cte.alias)
        registry[cte.alias] = bid     # register after building (CTEs ref earlier CTEs)
    build_query(root, "main", "main")

    # ---- statement-level metadata (mirrors sql_joins) ---------------------
    ds_counts = Counter(
        ".".join(p for p in (t.catalog, t.db) if p)
        for t in root.find_all(exp.Table)
        if (t.catalog or t.db)
    )
    dataset = ds_counts.most_common(1)[0][0] if ds_counts else ""
    dates = sorted(set(re.findall(r"\d{4}-\d{2}-\d{2}", sql)))
    period = {"start": dates[0], "end": dates[-1]} if dates else None

    return {
        "title": title or "Query Plan",
        "dataset": dataset,
        "dialect": dialect,
        "nl": nl,
        "sql": sql,
        "scan": scan,
        "period": period,
        "blockCount": len(blocks),
        "operatorCount": sum(len(b["operators"]) for b in blocks),
        "blocks": blocks,
        "edges": edges,
    }
