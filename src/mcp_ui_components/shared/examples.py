"""Seed SQL example for the SQL controls.

This is the query the original hand-authored join diagram was built from. Both the
``view_join_diagram`` and ``view_query_plan`` tools parse it when no SQL is supplied,
so each widget has something to render out of the box. Any other BigQuery query can
be passed instead.
"""

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
