"""Lineage provider.

The provider is the only thing that touches lineage data. In the diagram this is
the box labelled *Lineage provider — Neo4j / BigQuery / Dataplex*, sitting behind
a governance boundary. Here it is backed by an in-memory graph, but the interface
is what a Neo4j/BigQuery adapter would implement:

    view(focus)      -> the initial subgraph around a focus node
    expand(node_id)  -> the next upstream hop for a node, or the upstream
                        frontier of a set of already-visible nodes

Returned payloads are plain JSON-serialisable dicts so they can flow unchanged
through the MCP tool result and out to the widget.
"""

from __future__ import annotations

from .data import (
    FOCUS_ID,
    INITIAL_VISIBLE,
    NODE_DETAILS,
    Graph,
    Node,
    build_full_graph,
)

# Layer offsets relative to the focus node, used by the widget to lay out columns.
# Negative = upstream (to the left), positive = downstream (to the right).
_DOWNSTREAM_KINDS = {"metric", "view"}


class LineageProvider:
    def __init__(self, graph: Graph | None = None) -> None:
        self._g = graph or build_full_graph()

    # -- helpers -----------------------------------------------------------
    def _layer(self, node_id: str, focus: str) -> int:
        """Signed distance from the focus along the lineage direction."""
        if node_id == focus:
            return 0
        # BFS upstream
        seen = {focus}
        frontier = [focus]
        depth = 0
        while frontier:
            depth -= 1
            nxt = []
            for n in frontier:
                for p in self._g.parents(n):
                    if p not in seen:
                        seen.add(p)
                        if p == node_id:
                            return depth
                        nxt.append(p)
            frontier = nxt
        # BFS downstream
        seen = {focus}
        frontier = [focus]
        depth = 0
        while frontier:
            depth += 1
            nxt = []
            for n in frontier:
                for c in self._g.children(n):
                    if c not in seen:
                        seen.add(c)
                        if c == node_id:
                            return depth
                        nxt.append(c)
            frontier = nxt
        return 0

    def _node_payload(self, node: Node, focus: str) -> dict:
        return {
            "id": node.id,
            "label": node.label,
            "kind": node.kind,
            "description": node.description,
            "layer": self._layer(node.id, focus),
            "isFocus": node.id == focus,
            # Total neighbours in the full graph, so the widget can show
            # expand/collapse affordances by comparing against what's visible.
            "upstreamCount": len(self._g.parents(node.id)),
            "downstreamCount": len(self._g.children(node.id)),
        }

    def _subgraph(self, node_ids: set[str], focus: str) -> dict:
        nodes = [self._node_payload(self._g.nodes[n], focus)
                 for n in node_ids if n in self._g.nodes]
        edges = [{"source": e.source, "target": e.target}
                 for e in self._g.edges
                 if e.source in node_ids and e.target in node_ids]
        nodes.sort(key=lambda n: (n["layer"], n["label"]))
        return {"focus": focus, "nodes": nodes, "edges": edges}

    # -- public API --------------------------------------------------------
    def view(self, focus: str = FOCUS_ID) -> dict:
        """Initial lineage view around ``focus`` (one hop each direction)."""
        if focus not in self._g.nodes:
            raise KeyError(f"unknown node: {focus!r}")
        visible = set(INITIAL_VISIBLE) if focus == FOCUS_ID else self._one_hop(focus)
        return self._subgraph(visible, focus)

    def _one_hop(self, focus: str) -> set[str]:
        ids = {focus}
        ids.update(self._g.parents(focus))
        ids.update(self._g.children(focus))
        return ids

    def expand(
        self,
        focus: str,
        node: str,
        direction: str,
        visible_node_ids: list[str] | None = None,
    ) -> dict:
        """Reveal a single node's direct neighbours in one direction.

        ``direction`` is "upstream" (parents) or "downstream" (children).
        Neighbours already visible to the widget are skipped. The widget merges
        the returned nodes/edges into its graph.
        """
        if direction not in ("upstream", "downstream"):
            raise ValueError("direction must be 'upstream' or 'downstream'")
        if node not in self._g.nodes:
            raise KeyError(f"unknown node: {node!r}")

        visible = set(visible_node_ids or [])
        neighbours = (self._g.parents(node) if direction == "upstream"
                      else self._g.children(node))

        added_nodes: list[dict] = []
        added_edges: list[dict] = []
        for nb in neighbours:
            if nb in visible:
                continue
            added_nodes.append(self._node_payload(self._g.nodes[nb], focus))
            if direction == "upstream":
                added_edges.append({"source": nb, "target": node})
            else:
                added_edges.append({"source": node, "target": nb})

        return {
            "focus": focus,
            "node": node,
            "direction": direction,
            "addedNodes": added_nodes,
            "addedEdges": added_edges,
        }

    def describe(self, node: str) -> dict:
        """Rich detail for a single node (the 'click a node' interaction)."""
        if node not in self._g.nodes:
            raise KeyError(f"unknown node: {node!r}")
        n = self._g.nodes[node]
        details = NODE_DETAILS.get(node, {})
        return {
            "id": n.id,
            "label": n.label,
            "kind": n.kind,
            "description": n.description,
            "upstream": [self._g.nodes[p].label for p in self._g.parents(node)],
            "downstream": [self._g.nodes[c].label for c in self._g.children(node)],
            **details,
        }
