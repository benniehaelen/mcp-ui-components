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


# ---------------------------------------------------------------------------
# Seed example for the join-diagram widget.
#
# This is the query the original hand-authored join diagram was built from. The
# `view_join_diagram` tool parses it (via `sql_joins.parse_join_diagram`) into a
# model when no SQL is supplied, so the widget has something to render out of the
# box. Any other BigQuery query can be passed instead.
# ---------------------------------------------------------------------------
EXAMPLE_JOIN_TITLE = "monthly-encounter-provider-facility-2025"

EXAMPLE_JOIN_NL = (
    "For calendar year 2025, summarize monthly encounter counts by facility and "
    "attending provider specialty. Include facility name, month, attending "
    "provider name or specialty, patient gender mix, and total encounters. "
    "Restrict the analysis to active facilities only, using encounter records "
    "for the base population, patient records for demographics, associated party "
    "records for the attending provider, and facility master data for facility "
    "attributes."
)

EXAMPLE_JOIN_SQL = """WITH base_encounters AS (
  SELECT
    e.facility_id,
    e.encounter_id,
    e.region_code,
    f.facility_name,
    DATE_TRUNC(DATE(SAFE_CAST(e.admitted_at AS DATETIME)), MONTH) AS encounter_month,
    FORMAT('%s|%s', e.facility_id, e.encounter_id) AS encounter_key
  FROM `analytics-prod.clinical_core.encounters` e
  INNER JOIN `analytics-prod.clinical_core.facilities` f
    ON e.facility_id = f.facility_id
   AND e.region_code = f.region_code
  WHERE e.is_current = 1
    AND SAFE_CAST(e.admitted_at AS DATETIME) >= DATETIME '2025-01-01 00:00:00'
    AND SAFE_CAST(e.admitted_at AS DATETIME) < DATETIME '2026-01-01 00:00:00'
    AND f.is_active = 1
    AND LOWER(f.status) = 'active'
),
attending_providers AS (
  SELECT
    ep.facility_id,
    ep.encounter_id,
    COALESCE(
      NULLIF(TRIM(CONCAT(COALESCE(ep.provider_first_name, ''), ' ', COALESCE(ep.provider_last_name, ''))), ''),
      ep.provider_npi,
      ep.provider_source_id
    ) AS attending_provider_name
  FROM `analytics-prod.clinical_core.providers` ep
  WHERE ep.is_current = 1
    AND REGEXP_CONTAINS(LOWER(ep.role), r'attending')
),
patient_gender AS (
  SELECT
    p.facility_id,
    p.encounter_id,
    p.gender_code
  FROM `analytics-prod.clinical_core.patients` p
)
SELECT
  b.facility_name,
  b.encounter_month AS month,
  ap.attending_provider_name,
  FORMAT(
    'F %.1f%% | M %.1f%% | Other/Unknown %.1f%%',
    100 * SAFE_DIVIDE(COUNT(DISTINCT IF(LOWER(COALESCE(pg.gender_code, '')) IN ('f', 'female'), b.encounter_key, NULL)), COUNT(DISTINCT b.encounter_key)),
    100 * SAFE_DIVIDE(COUNT(DISTINCT IF(LOWER(COALESCE(pg.gender_code, '')) IN ('m', 'male'), b.encounter_key, NULL)), COUNT(DISTINCT b.encounter_key)),
    100 * SAFE_DIVIDE(COUNT(DISTINCT IF(LOWER(COALESCE(pg.gender_code, '')) NOT IN ('f', 'female', 'm', 'male'), b.encounter_key, NULL)), COUNT(DISTINCT b.encounter_key))
  ) AS patient_gender_mix,
  COUNT(DISTINCT b.encounter_key) AS total_encounters
FROM base_encounters b
INNER JOIN attending_providers ap
  ON b.facility_id = ap.facility_id
 AND b.encounter_id = ap.encounter_id
LEFT JOIN patient_gender pg
  ON b.facility_id = pg.facility_id
 AND b.encounter_id = pg.encounter_id
GROUP BY
  b.facility_name,
  month,
  ap.attending_provider_name
ORDER BY
  month,
  b.facility_name,
  total_encounters DESC"""

# Scan size comes from a BigQuery dry-run, not from the SQL itself; carried as
# display-only metadata for the seed example.
EXAMPLE_JOIN_SCAN = "549.2 GB"
