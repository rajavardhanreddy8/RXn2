-- DuckDB candidate-generation template for a pinned SureChEMBL snapshot.
-- Update the four read_parquet paths to the registered dated release.
-- This query never establishes a gold same-drug label; it emits typed candidates.

CREATE SCHEMA IF NOT EXISTS staging;

-- Populate this table only from reviewed local structure/active-moiety records.
CREATE TABLE IF NOT EXISTS staging.seed_compound (
  seed_id VARCHAR PRIMARY KEY,
  active_moiety_id VARCHAR NOT NULL,
  inchi_key VARCHAR,
  connectivity_key VARCHAR,
  preferred_name VARCHAR,
  review_status VARCHAR NOT NULL
);

CREATE OR REPLACE TABLE staging.surechembl_matched_compound AS
SELECT
  s.seed_id,
  s.active_moiety_id,
  c.id AS surechembl_compound_id,
  c.smiles,
  c.inchi,
  c.inchi_key,
  c.mol_weight,
  CASE
    WHEN s.inchi_key IS NOT NULL AND c.inchi_key = s.inchi_key THEN 'exact_structure'
    WHEN s.connectivity_key IS NOT NULL AND substr(c.inchi_key, 1, 14) = s.connectivity_key THEN 'same_connectivity'
    ELSE 'unmatched'
  END AS candidate_relationship
FROM read_parquet('data/raw/surechembl_bulk/2026-07-17/compounds.parquet') c
JOIN staging.seed_compound s
  ON (s.inchi_key IS NOT NULL AND c.inchi_key = s.inchi_key)
  OR (s.connectivity_key IS NOT NULL AND substr(c.inchi_key, 1, 14) = s.connectivity_key)
WHERE s.review_status = 'accepted';

CREATE OR REPLACE TABLE staging.surechembl_candidate_patent AS
SELECT DISTINCT
  m.seed_id,
  m.active_moiety_id,
  m.surechembl_compound_id,
  m.candidate_relationship,
  p.id AS surechembl_patent_id,
  p.patent_number,
  p.country,
  p.publication_date,
  p.family_id,
  p.title,
  p.assignee,
  p.cpc,
  p.ipcr,
  pcm.field_id
FROM staging.surechembl_matched_compound m
JOIN read_parquet('data/raw/surechembl_bulk/2026-07-17/patent_compound_map.parquet') pcm
  ON pcm.compound_id = m.surechembl_compound_id
JOIN read_parquet('data/raw/surechembl_bulk/2026-07-17/patents.parquet') p
  ON p.id = pcm.patent_id;

-- Keep an immutable candidate export for downstream full-text acquisition.
COPY (
  SELECT * FROM staging.surechembl_candidate_patent
  ORDER BY seed_id, publication_date, patent_number, surechembl_compound_id
)
TO 'data/processed/surechembl_candidate_patents.parquet'
(FORMAT PARQUET, COMPRESSION ZSTD);

-- High-signal QA summaries. Persist these in a quality report for the release.
SELECT candidate_relationship, count(*) AS candidate_rows,
       count(DISTINCT seed_id) AS seeds,
       count(DISTINCT patent_number) AS patents
FROM staging.surechembl_candidate_patent
GROUP BY candidate_relationship
ORDER BY candidate_relationship;

SELECT country, count(DISTINCT patent_number) AS patents
FROM staging.surechembl_candidate_patent
GROUP BY country
ORDER BY patents DESC, country;

SELECT count(*) AS missing_family_rows
FROM staging.surechembl_candidate_patent
WHERE family_id IS NULL OR family_id = -1;
