"""Seed lineage graph.

This is the in-memory stand-in for what would, in production, be a Neo4j
knowledge graph or BigQuery / Dataplex lineage feed. The governance boundary
(de-identification, zone filtering) is modelled by the provider, not here.

The graph below is the one drawn in the architecture diagram, plus a second
upstream hop that is hidden until the widget asks for it via
``expand_lineage_node`` — so the "Expand upstream" button has something to fetch.

Node ``kind`` drives the accent colour in the widget; the values match the
diagram's legend:

    source   #5b7fa6   raw / source tables
    staging  #c08a3e   staging models
    fact     #4e8a5f   fact tables (the focus)
    metric   #7d5ba6   downstream metrics
    view     #3e8f96   downstream views
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    kind: str  # source | staging | fact | metric | view
    description: str = ""


@dataclass(frozen=True)
class Edge:
    source: str  # upstream node id
    target: str  # downstream node id


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    def add_node(self, node: Node) -> None:
        self.nodes[node.id] = node

    def add_edge(self, source: str, target: str) -> None:
        self.edges.append(Edge(source, target))

    def parents(self, node_id: str) -> list[str]:
        return [e.source for e in self.edges if e.target == node_id]

    def children(self, node_id: str) -> list[str]:
        return [e.target for e in self.edges if e.source == node_id]


# The focus of the lineage view.
FOCUS_ID = "fct_patient_visits"


def build_full_graph() -> Graph:
    """The complete graph, including hops not revealed until expansion."""
    g = Graph()

    # --- Focus -------------------------------------------------------------
    g.add_node(Node(FOCUS_ID, "fct_patient_visits", "fact",
                    "One row per completed patient visit. Conformed fact."))

    # --- First upstream hop (visible on initial view_lineage) --------------
    g.add_node(Node("encounters", "encounters", "source",
                    "EHR encounter events."))
    g.add_node(Node("patients", "patients", "source",
                    "Patient master, de-identified at the governance boundary."))
    g.add_node(Node("stg_charges", "stg_charges", "staging",
                    "Cleaned charge lines staged for joining."))
    g.add_edge("encounters", FOCUS_ID)
    g.add_edge("patients", FOCUS_ID)
    g.add_edge("stg_charges", FOCUS_ID)

    # --- Downstream hop (visible on initial view_lineage) ------------------
    g.add_node(Node("avg_los", "avg_los", "metric",
                    "Average length-of-stay metric."))
    g.add_node(Node("vw_visits", "vw_visits", "view",
                    "Reporting view over the visits fact."))
    g.add_edge(FOCUS_ID, "avg_los")
    g.add_edge(FOCUS_ID, "vw_visits")

    # --- Second downstream hop (hidden; revealed by expanding downstream) --
    g.add_node(Node("los_trend", "los_trend", "metric",
                    "Monthly length-of-stay trend."))
    g.add_node(Node("exec_dashboard", "exec_dashboard", "view",
                    "Executive operations dashboard."))
    g.add_node(Node("census_report", "census_report", "view",
                    "Daily patient census report."))
    g.add_edge("avg_los", "los_trend")
    g.add_edge("vw_visits", "exec_dashboard")
    g.add_edge("vw_visits", "census_report")

    # --- Second upstream hop (hidden; fetched by "Expand upstream") --------
    g.add_node(Node("raw_admissions", "raw_admissions", "source",
                    "Admission/discharge/transfer feed."))
    g.add_node(Node("raw_ed_visits", "raw_ed_visits", "source",
                    "Emergency department visit feed."))
    g.add_node(Node("raw_patients", "raw_patients", "source",
                    "Source patient records (pre de-identification)."))
    g.add_node(Node("raw_charges", "raw_charges", "source",
                    "Billing charge master extract."))
    g.add_edge("raw_admissions", "encounters")
    g.add_edge("raw_ed_visits", "encounters")
    g.add_edge("raw_patients", "patients")
    g.add_edge("raw_charges", "stg_charges")

    return g


# Node ids that are part of the *initial* view (the focus plus one hop each
# direction). Expanding a node reveals its hidden neighbours.
INITIAL_VISIBLE = {
    FOCUS_ID,
    "encounters",
    "patients",
    "stg_charges",
    "avg_los",
    "vw_visits",
}


# Rich per-node details, returned by the `describe_node` tool when the user
# clicks a node. In production this would come from the catalog / lineage
# provider; here it is mocked but plausible.
NODE_DETAILS: dict[str, dict] = {
    "fct_patient_visits": {
        "owner": "analytics-eng", "rows": 1_248_000, "updated": "2026-06-10",
        "grain": "one row per completed visit",
        "columns": ["visit_id", "patient_id", "encounter_id", "admit_ts",
                    "discharge_ts", "los_days", "total_charges"],
    },
    "encounters": {
        "owner": "ehr-integrations", "rows": 3_910_220, "updated": "2026-06-10",
        "grain": "one row per encounter event",
        "columns": ["encounter_id", "patient_id", "type", "ts", "facility"],
    },
    "patients": {
        "owner": "mdm", "rows": 412_300, "updated": "2026-06-09",
        "grain": "one row per de-identified patient",
        "columns": ["patient_id", "birth_year", "sex", "zip3"],
    },
    "stg_charges": {
        "owner": "finance-data", "rows": 8_004_551, "updated": "2026-06-10",
        "grain": "one row per charge line",
        "columns": ["charge_id", "encounter_id", "code", "amount"],
    },
    "avg_los": {
        "owner": "analytics-eng", "rows": 1, "updated": "2026-06-10",
        "grain": "single aggregate metric",
        "columns": ["avg_los_days"],
    },
    "vw_visits": {
        "owner": "analytics-eng", "rows": 1_248_000, "updated": "2026-06-10",
        "grain": "reporting view over the visits fact",
        "columns": ["visit_id", "patient_id", "los_days", "total_charges"],
    },
    "raw_admissions": {
        "owner": "ehr-integrations", "rows": 2_100_000, "updated": "2026-06-11",
        "grain": "ADT admission feed", "columns": ["adt_id", "patient_id", "ts"],
    },
    "raw_ed_visits": {
        "owner": "ehr-integrations", "rows": 1_810_220, "updated": "2026-06-11",
        "grain": "ED visit feed", "columns": ["ed_id", "patient_id", "ts"],
    },
    "raw_patients": {
        "owner": "ehr-integrations", "rows": 430_000, "updated": "2026-06-11",
        "grain": "source patient records (pre de-id)",
        "columns": ["mrn", "name", "dob", "address"],
    },
    "raw_charges": {
        "owner": "finance-data", "rows": 8_200_000, "updated": "2026-06-11",
        "grain": "billing charge master extract",
        "columns": ["charge_id", "mrn", "cpt", "amount"],
    },
    "los_trend": {
        "owner": "analytics-eng", "rows": 36, "updated": "2026-06-10",
        "grain": "one row per month", "columns": ["month", "avg_los_days"],
    },
    "exec_dashboard": {
        "owner": "bi", "rows": 0, "updated": "2026-06-10",
        "grain": "Looker dashboard", "columns": ["tile", "metric"],
    },
    "census_report": {
        "owner": "bi", "rows": 365, "updated": "2026-06-10",
        "grain": "one row per day", "columns": ["date", "census", "admits"],
    },
}
