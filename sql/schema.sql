PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS source (
  source_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  authority TEXT NOT NULL,
  role TEXT NOT NULL,
  collection_mode TEXT NOT NULL,
  runtime_dependency INTEGER NOT NULL CHECK (runtime_dependency IN (0, 1)),
  automated_acquisition_allowed INTEGER NOT NULL CHECK (automated_acquisition_allowed IN (0, 1)),
  redistribution TEXT NOT NULL,
  license_code TEXT NOT NULL,
  homepage TEXT,
  registry_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_release (
  release_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES source(source_id),
  released_on TEXT,
  acquired_at TEXT NOT NULL,
  parser_version TEXT,
  schema_version TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS artifact (
  artifact_id TEXT PRIMARY KEY,
  release_id TEXT NOT NULL REFERENCES source_release(release_id),
  relative_path TEXT,
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
  media_type TEXT,
  UNIQUE (release_id, sha256)
);

CREATE TABLE IF NOT EXISTS ingestion_run (
  ingestion_run_id TEXT PRIMARY KEY,
  release_id TEXT NOT NULL REFERENCES source_release(release_id),
  source_id TEXT NOT NULL REFERENCES source(source_id),
  parser_version TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
  input_rows INTEGER NOT NULL CHECK (input_rows >= 0),
  accepted_rows INTEGER NOT NULL CHECK (accepted_rows >= 0),
  excluded_rows INTEGER NOT NULL CHECK (excluded_rows >= 0),
  rejected_rows INTEGER NOT NULL CHECK (rejected_rows >= 0),
  reason_counts_json TEXT NOT NULL,
  details_json TEXT NOT NULL,
  CHECK (input_rows = accepted_rows + excluded_rows + rejected_rows),
  UNIQUE (release_id, parser_version)
);

CREATE TABLE IF NOT EXISTS patent_family (
  family_id TEXT PRIMARY KEY,
  family_type TEXT NOT NULL DEFAULT 'source_reported',
  source_id TEXT REFERENCES source(source_id),
  confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
);

CREATE TABLE IF NOT EXISTS patent_document (
  publication_number TEXT PRIMARY KEY,
  country_code TEXT NOT NULL,
  kind_code TEXT,
  publication_date TEXT,
  title TEXT,
  artifact_id TEXT REFERENCES artifact(artifact_id),
  source_id TEXT NOT NULL REFERENCES source(source_id),
  source_document_id TEXT,
  parser_version TEXT,
  raw_record_json TEXT
);

CREATE TABLE IF NOT EXISTS patent_family_member (
  family_id TEXT NOT NULL REFERENCES patent_family(family_id),
  publication_number TEXT NOT NULL REFERENCES patent_document(publication_number),
  relationship TEXT NOT NULL DEFAULT 'member',
  PRIMARY KEY (family_id, publication_number)
);

CREATE TABLE IF NOT EXISTS active_moiety (
  active_moiety_id TEXT PRIMARY KEY,
  preferred_name TEXT,
  structure_key TEXT,
  structure_source TEXT,
  review_status TEXT NOT NULL DEFAULT 'unreviewed'
);

CREATE TABLE IF NOT EXISTS compound (
  compound_id TEXT PRIMARY KEY,
  preferred_name TEXT,
  smiles TEXT,
  inchi TEXT,
  inchi_key TEXT,
  connectivity_key TEXT,
  active_moiety_id TEXT REFERENCES active_moiety(active_moiety_id),
  material_form TEXT,
  source_id TEXT REFERENCES source(source_id),
  review_status TEXT NOT NULL DEFAULT 'unreviewed'
);

CREATE TABLE IF NOT EXISTS compound_relationship (
  relationship_id TEXT PRIMARY KEY,
  subject_compound_id TEXT NOT NULL REFERENCES compound(compound_id),
  object_compound_id TEXT NOT NULL REFERENCES compound(compound_id),
  relationship_type TEXT NOT NULL,
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  review_status TEXT NOT NULL,
  evidence_span_id TEXT
);

CREATE TABLE IF NOT EXISTS drug_entity (
  drug_id TEXT PRIMARY KEY,
  preferred_name TEXT NOT NULL,
  active_moiety_id TEXT REFERENCES active_moiety(active_moiety_id),
  modality TEXT NOT NULL DEFAULT 'small_molecule',
  review_status TEXT NOT NULL DEFAULT 'unreviewed'
);

CREATE TABLE IF NOT EXISTS drug_compound (
  drug_id TEXT NOT NULL REFERENCES drug_entity(drug_id),
  compound_id TEXT NOT NULL REFERENCES compound(compound_id),
  relationship_type TEXT NOT NULL,
  review_status TEXT NOT NULL,
  PRIMARY KEY (drug_id, compound_id, relationship_type)
);

CREATE TABLE IF NOT EXISTS drug_alias (
  drug_id TEXT NOT NULL REFERENCES drug_entity(drug_id),
  alias TEXT NOT NULL,
  normalized_alias TEXT NOT NULL,
  alias_type TEXT NOT NULL,
  source_id TEXT NOT NULL REFERENCES source(source_id),
  PRIMARY KEY (drug_id, normalized_alias, alias_type, source_id)
);

CREATE TABLE IF NOT EXISTS drug_identifier (
  drug_id TEXT NOT NULL REFERENCES drug_entity(drug_id),
  namespace TEXT NOT NULL,
  identifier_value TEXT NOT NULL,
  source_id TEXT NOT NULL REFERENCES source(source_id),
  PRIMARY KEY (namespace, identifier_value, source_id)
);

CREATE TABLE IF NOT EXISTS regulatory_product (
  regulatory_product_id TEXT PRIMARY KEY,
  jurisdiction TEXT NOT NULL,
  application_number TEXT,
  product_number TEXT,
  trade_name TEXT,
  dosage_form TEXT,
  route TEXT,
  strength TEXT,
  approval_date TEXT,
  marketing_status TEXT NOT NULL DEFAULT 'unknown',
  applicant TEXT,
  source_id TEXT NOT NULL REFERENCES source(source_id),
  raw_record_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS regulatory_product_drug (
  regulatory_product_id TEXT NOT NULL REFERENCES regulatory_product(regulatory_product_id),
  drug_id TEXT NOT NULL REFERENCES drug_entity(drug_id),
  relationship_type TEXT NOT NULL DEFAULT 'active_ingredient',
  PRIMARY KEY (regulatory_product_id, drug_id, relationship_type)
);
CREATE INDEX IF NOT EXISTS idx_regulatory_product_drug
  ON regulatory_product_drug(drug_id, regulatory_product_id);

CREATE TABLE IF NOT EXISTS evidence_span (
  evidence_span_id TEXT PRIMARY KEY,
  publication_number TEXT NOT NULL REFERENCES patent_document(publication_number),
  source_id TEXT NOT NULL REFERENCES source(source_id),
  artifact_sha256 TEXT NOT NULL CHECK (length(artifact_sha256) = 64),
  section_type TEXT,
  paragraph_id TEXT,
  char_start INTEGER NOT NULL CHECK (char_start >= 0),
  char_end INTEGER NOT NULL CHECK (char_end >= char_start),
  evidence_text TEXT NOT NULL,
  text_sha256 TEXT NOT NULL CHECK (length(text_sha256) = 64),
  evidence_status TEXT NOT NULL,
  extraction_method TEXT NOT NULL,
  extractor_version TEXT,
  review_status TEXT NOT NULL,
  source_url TEXT,
  retrieved_at TEXT NOT NULL,
  license_code TEXT NOT NULL,
  redistribution_class TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS patent_compound_mention (
  mention_id TEXT PRIMARY KEY,
  publication_number TEXT NOT NULL REFERENCES patent_document(publication_number),
  compound_id TEXT NOT NULL REFERENCES compound(compound_id),
  evidence_span_id TEXT REFERENCES evidence_span(evidence_span_id),
  mention_field TEXT,
  relationship_type TEXT NOT NULL,
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  review_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS patent_candidate (
  candidate_id TEXT PRIMARY KEY,
  drug_id TEXT NOT NULL REFERENCES drug_entity(drug_id),
  compound_id TEXT NOT NULL REFERENCES compound(compound_id),
  publication_number TEXT NOT NULL REFERENCES patent_document(publication_number),
  source_release_id TEXT NOT NULL REFERENCES source_release(release_id),
  source_compound_id TEXT NOT NULL,
  source_field_id TEXT,
  source_field_name TEXT,
  match_type TEXT NOT NULL CHECK (match_type IN ('exact_structure', 'same_connectivity')),
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  review_status TEXT NOT NULL CHECK (review_status IN ('accepted', 'rejected', 'needs_review', 'unreviewed')),
  created_at TEXT NOT NULL,
  UNIQUE (drug_id, compound_id, publication_number, source_compound_id, source_field_id)
);

CREATE TABLE IF NOT EXISTS drug_coverage_override (
  drug_id TEXT PRIMARY KEY REFERENCES drug_entity(drug_id),
  public_evidence_unavailable INTEGER NOT NULL DEFAULT 0 CHECK (public_evidence_unavailable IN (0, 1)),
  reason TEXT,
  reviewed_by TEXT NOT NULL,
  reviewed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drug_coverage (
  drug_id TEXT PRIMARY KEY REFERENCES drug_entity(drug_id),
  status TEXT NOT NULL CHECK (status IN (
    'identified', 'patents_found', 'examples_extracted', 'routes_under_review',
    'complete_reviewed_route', 'price_complete', 'cost_comparison_ready',
    'public_evidence_unavailable'
  )),
  identified INTEGER NOT NULL DEFAULT 1 CHECK (identified IN (0, 1)),
  patents_found INTEGER NOT NULL DEFAULT 0 CHECK (patents_found IN (0, 1)),
  examples_extracted INTEGER NOT NULL DEFAULT 0 CHECK (examples_extracted IN (0, 1)),
  routes_under_review INTEGER NOT NULL DEFAULT 0 CHECK (routes_under_review IN (0, 1)),
  complete_reviewed_route INTEGER NOT NULL DEFAULT 0 CHECK (complete_reviewed_route IN (0, 1)),
  price_complete INTEGER NOT NULL DEFAULT 0 CHECK (price_complete IN (0, 1)),
  cost_comparison_ready INTEGER NOT NULL DEFAULT 0 CHECK (cost_comparison_ready IN (0, 1)),
  public_evidence_unavailable INTEGER NOT NULL DEFAULT 0 CHECK (public_evidence_unavailable IN (0, 1)),
  patent_count INTEGER NOT NULL DEFAULT 0 CHECK (patent_count >= 0),
  extracted_example_count INTEGER NOT NULL DEFAULT 0 CHECK (extracted_example_count >= 0),
  reviewed_route_count INTEGER NOT NULL DEFAULT 0 CHECK (reviewed_route_count >= 0),
  priced_route_count INTEGER NOT NULL DEFAULT 0 CHECK (priced_route_count >= 0),
  refreshed_at TEXT NOT NULL,
  details_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS process_route (
  route_id TEXT PRIMARY KEY,
  active_moiety_id TEXT REFERENCES active_moiety(active_moiety_id),
  target_compound_id TEXT REFERENCES compound(compound_id),
  route_fingerprint TEXT,
  review_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS process_step (
  step_id TEXT PRIMARY KEY,
  route_id TEXT NOT NULL REFERENCES process_route(route_id),
  evidence_span_id TEXT NOT NULL REFERENCES evidence_span(evidence_span_id),
  step_order INTEGER CHECK (step_order IS NULL OR step_order >= 0),
  transformation_key TEXT,
  product_compound_id TEXT REFERENCES compound(compound_id),
  operation_summary TEXT,
  evidence_status TEXT NOT NULL,
  review_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quantity_observation (
  quantity_id TEXT PRIMARY KEY,
  step_id TEXT NOT NULL REFERENCES process_step(step_id),
  quantity_kind TEXT NOT NULL,
  original_value REAL NOT NULL CHECK (original_value >= 0),
  original_unit TEXT NOT NULL,
  normalized_value REAL,
  normalized_unit TEXT,
  material_compound_id TEXT REFERENCES compound(compound_id),
  is_range INTEGER NOT NULL DEFAULT 0 CHECK (is_range IN (0, 1)),
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE TABLE IF NOT EXISTS outcome_observation (
  outcome_id TEXT PRIMARY KEY,
  step_id TEXT NOT NULL REFERENCES process_step(step_id),
  yield_percent REAL CHECK (yield_percent IS NULL OR (yield_percent >= 0 AND yield_percent <= 105)),
  purity_percent REAL CHECK (purity_percent IS NULL OR (purity_percent >= 0 AND purity_percent <= 100)),
  outcome_type TEXT,
  original_text TEXT,
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE TABLE IF NOT EXISTS regulatory_event (
  regulatory_event_id TEXT PRIMARY KEY,
  drug_id TEXT NOT NULL REFERENCES drug_entity(drug_id),
  event_type TEXT NOT NULL,
  event_date TEXT,
  application_number TEXT,
  product_number TEXT,
  jurisdiction TEXT NOT NULL,
  source_id TEXT NOT NULL REFERENCES source(source_id),
  source_record_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clinical_event (
  clinical_event_id TEXT PRIMARY KEY,
  drug_id TEXT NOT NULL REFERENCES drug_entity(drug_id),
  study_id TEXT NOT NULL,
  phase TEXT,
  status TEXT,
  event_date TEXT,
  source_id TEXT NOT NULL REFERENCES source(source_id),
  source_record_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS link_candidate (
  candidate_id TEXT PRIMARY KEY,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  object_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  relationship_type TEXT NOT NULL,
  score REAL NOT NULL CHECK (score >= 0 AND score <= 1),
  method TEXT NOT NULL,
  model_version TEXT,
  features_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS curation_decision (
  decision_id TEXT PRIMARY KEY,
  candidate_id TEXT REFERENCES link_candidate(candidate_id),
  object_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('accepted', 'rejected', 'needs_review', 'superseded')),
  reviewer_id TEXT NOT NULL,
  rationale TEXT,
  decided_at TEXT NOT NULL,
  supersedes_decision_id TEXT REFERENCES curation_decision(decision_id)
);

CREATE TABLE IF NOT EXISTS scale_label (
  scale_label_id TEXT PRIMARY KEY,
  step_id TEXT NOT NULL REFERENCES process_step(step_id),
  scale_band TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  basis_kind TEXT,
  basis_value_g REAL CHECK (basis_value_g IS NULL OR basis_value_g >= 0),
  development_stage TEXT NOT NULL DEFAULT 'unknown',
  development_stage_basis TEXT,
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  review_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_version (
  dataset_version_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('working', 'candidate', 'released', 'retired')),
  policy_version TEXT NOT NULL,
  manifest_sha256 TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS dataset_example (
  dataset_version_id TEXT NOT NULL REFERENCES dataset_version(dataset_version_id),
  example_id TEXT NOT NULL,
  step_id TEXT NOT NULL REFERENCES process_step(step_id),
  family_id TEXT,
  active_moiety_id TEXT,
  scale_band TEXT NOT NULL,
  is_synthetic INTEGER NOT NULL CHECK (is_synthetic IN (0, 1)),
  payload_json TEXT NOT NULL,
  PRIMARY KEY (dataset_version_id, example_id)
);

CREATE TABLE IF NOT EXISTS dataset_split (
  dataset_version_id TEXT NOT NULL,
  example_id TEXT NOT NULL,
  split_name TEXT NOT NULL CHECK (split_name IN ('train', 'validation', 'test')),
  split_policy TEXT NOT NULL,
  leakage_group TEXT NOT NULL,
  PRIMARY KEY (dataset_version_id, example_id),
  FOREIGN KEY (dataset_version_id, example_id)
    REFERENCES dataset_example(dataset_version_id, example_id)
);

CREATE TABLE IF NOT EXISTS quality_result (
  quality_result_id TEXT PRIMARY KEY,
  dataset_version_id TEXT,
  check_name TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low')),
  status TEXT NOT NULL CHECK (status IN ('pass', 'fail', 'warn')),
  affected_count INTEGER NOT NULL CHECK (affected_count >= 0),
  details_json TEXT NOT NULL,
  checked_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_patent_family_member_publication
  ON patent_family_member(publication_number);
CREATE INDEX IF NOT EXISTS idx_compound_inchi_key ON compound(inchi_key);
CREATE INDEX IF NOT EXISTS idx_compound_connectivity_key ON compound(connectivity_key);
CREATE INDEX IF NOT EXISTS idx_compound_active_moiety ON compound(active_moiety_id);
CREATE INDEX IF NOT EXISTS idx_drug_alias_normalized ON drug_alias(normalized_alias);
CREATE INDEX IF NOT EXISTS idx_drug_identifier_value ON drug_identifier(namespace, identifier_value);
CREATE INDEX IF NOT EXISTS idx_regulatory_product_application ON regulatory_product(jurisdiction, application_number, product_number);
CREATE INDEX IF NOT EXISTS idx_regulatory_product_status ON regulatory_product(marketing_status, jurisdiction);
CREATE INDEX IF NOT EXISTS idx_ingestion_run_release ON ingestion_run(release_id, status);
CREATE INDEX IF NOT EXISTS idx_patent_candidate_drug ON patent_candidate(drug_id, publication_number);
CREATE INDEX IF NOT EXISTS idx_patent_candidate_compound ON patent_candidate(compound_id, publication_number);
CREATE INDEX IF NOT EXISTS idx_drug_coverage_status ON drug_coverage(status, drug_id);
CREATE INDEX IF NOT EXISTS idx_evidence_publication ON evidence_span(publication_number);
CREATE INDEX IF NOT EXISTS idx_step_evidence ON process_step(evidence_span_id);
CREATE INDEX IF NOT EXISTS idx_quantity_step_kind ON quantity_observation(step_id, quantity_kind);
CREATE INDEX IF NOT EXISTS idx_dataset_family ON dataset_example(dataset_version_id, family_id);
CREATE INDEX IF NOT EXISTS idx_dataset_moiety ON dataset_example(dataset_version_id, active_moiety_id);

-- ---------------------------------------------------------------------------
-- Evidence-bounded synthesis knowledge graph and route economics (MVP v1).
-- These normalized tables are the authoritative graph store. The graph views
-- below expose nodes and edges without duplicating scientific records.

CREATE TABLE IF NOT EXISTS element (
  element_id INTEGER PRIMARY KEY,
  atomic_number INTEGER NOT NULL UNIQUE CHECK (atomic_number > 0),
  symbol TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS functional_group (
  functional_group_id TEXT PRIMARY KEY,
  preferred_name TEXT NOT NULL,
  smarts TEXT NOT NULL,
  detector_version TEXT NOT NULL,
  UNIQUE (smarts, detector_version)
);

CREATE TABLE IF NOT EXISTS compound_property (
  compound_id TEXT PRIMARY KEY REFERENCES compound(compound_id),
  standardized_smiles TEXT,
  molecular_formula TEXT,
  molecular_weight REAL CHECK (molecular_weight IS NULL OR molecular_weight > 0),
  structure_hash TEXT,
  toolkit_name TEXT NOT NULL,
  toolkit_version TEXT NOT NULL,
  computed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS compound_element (
  compound_id TEXT NOT NULL REFERENCES compound(compound_id),
  element_id INTEGER NOT NULL REFERENCES element(element_id),
  atom_count INTEGER NOT NULL CHECK (atom_count > 0),
  PRIMARY KEY (compound_id, element_id)
);

CREATE TABLE IF NOT EXISTS compound_functional_group (
  compound_id TEXT NOT NULL REFERENCES compound(compound_id),
  functional_group_id TEXT NOT NULL REFERENCES functional_group(functional_group_id),
  match_count INTEGER NOT NULL CHECK (match_count > 0),
  detector_version TEXT NOT NULL,
  PRIMARY KEY (compound_id, functional_group_id, detector_version)
);

CREATE TABLE IF NOT EXISTS reaction_instance (
  reaction_id TEXT PRIMARY KEY,
  reaction_name TEXT NOT NULL,
  transformation_key TEXT NOT NULL,
  evidence_span_id TEXT REFERENCES evidence_span(evidence_span_id),
  yield_percent REAL CHECK (yield_percent IS NULL OR (yield_percent > 0 AND yield_percent <= 100)),
  demonstrated_scale_g REAL CHECK (demonstrated_scale_g IS NULL OR demonstrated_scale_g >= 0),
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  review_status TEXT NOT NULL CHECK (review_status IN ('accepted', 'rejected', 'needs_review', 'unreviewed')),
  is_synthetic INTEGER NOT NULL DEFAULT 0 CHECK (is_synthetic IN (0, 1)),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reaction_participant (
  reaction_id TEXT NOT NULL REFERENCES reaction_instance(reaction_id),
  compound_id TEXT NOT NULL REFERENCES compound(compound_id),
  role TEXT NOT NULL CHECK (role IN ('consumed', 'produced', 'catalyst', 'solvent', 'reagent', 'workup')),
  stoichiometry REAL CHECK (stoichiometry IS NULL OR stoichiometry > 0),
  amount_value REAL CHECK (amount_value IS NULL OR amount_value >= 0),
  amount_unit TEXT,
  PRIMARY KEY (reaction_id, compound_id, role)
);

CREATE TABLE IF NOT EXISTS reaction_condition (
  condition_id TEXT PRIMARY KEY,
  reaction_id TEXT NOT NULL REFERENCES reaction_instance(reaction_id),
  condition_type TEXT NOT NULL,
  value_text TEXT NOT NULL,
  numeric_value REAL,
  unit TEXT,
  evidence_span_id TEXT REFERENCES evidence_span(evidence_span_id)
);

CREATE TABLE IF NOT EXISTS material_availability (
  compound_id TEXT PRIMARY KEY REFERENCES compound(compound_id),
  is_starting_material INTEGER NOT NULL CHECK (is_starting_material IN (0, 1)),
  geography TEXT,
  reviewed_at TEXT NOT NULL,
  review_status TEXT NOT NULL CHECK (review_status IN ('accepted', 'rejected', 'needs_review', 'unreviewed'))
);

CREATE TABLE IF NOT EXISTS supplier (
  supplier_id TEXT PRIMARY KEY,
  supplier_name TEXT NOT NULL,
  homepage TEXT,
  geography TEXT,
  review_status TEXT NOT NULL DEFAULT 'unreviewed'
);

CREATE TABLE IF NOT EXISTS material_quote (
  quote_id TEXT PRIMARY KEY,
  compound_id TEXT NOT NULL REFERENCES compound(compound_id),
  supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id),
  source_url TEXT,
  observed_at TEXT NOT NULL,
  currency TEXT NOT NULL CHECK (length(currency) = 3),
  geography TEXT,
  purity_percent REAL CHECK (purity_percent IS NULL OR (purity_percent > 0 AND purity_percent <= 100)),
  pack_size_value REAL NOT NULL CHECK (pack_size_value > 0),
  pack_size_unit TEXT NOT NULL,
  available_quantity_value REAL CHECK (available_quantity_value IS NULL OR available_quantity_value >= 0),
  available_quantity_unit TEXT,
  price REAL NOT NULL CHECK (price >= 0),
  imported_at TEXT NOT NULL,
  raw_record_json TEXT NOT NULL,
  review_status TEXT NOT NULL CHECK (review_status IN ('accepted', 'rejected', 'needs_review', 'unreviewed'))
);

CREATE TABLE IF NOT EXISTS exchange_rate (
  rate_date TEXT NOT NULL,
  base_currency TEXT NOT NULL CHECK (length(base_currency) = 3),
  quote_currency TEXT NOT NULL CHECK (length(quote_currency) = 3),
  rate REAL NOT NULL CHECK (rate > 0),
  source_name TEXT NOT NULL,
  source_url TEXT,
  imported_at TEXT NOT NULL,
  PRIMARY KEY (rate_date, base_currency, quote_currency)
);

CREATE TABLE IF NOT EXISTS hazard_classification (
  hazard_id TEXT PRIMARY KEY,
  compound_id TEXT NOT NULL REFERENCES compound(compound_id),
  hazard_code TEXT NOT NULL,
  severity REAL NOT NULL CHECK (severity >= 0 AND severity <= 1),
  source_name TEXT NOT NULL,
  source_url TEXT,
  reviewed_at TEXT,
  review_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS route_candidate (
  route_candidate_id TEXT PRIMARY KEY,
  target_compound_id TEXT NOT NULL REFERENCES compound(compound_id),
  target_mass_g REAL NOT NULL CHECK (target_mass_g > 0),
  base_currency TEXT NOT NULL CHECK (length(base_currency) = 3),
  generated_at TEXT NOT NULL,
  algorithm_version TEXT NOT NULL,
  request_json TEXT NOT NULL,
  route_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS route_evaluation (
  route_candidate_id TEXT PRIMARY KEY REFERENCES route_candidate(route_candidate_id),
  actual_material_cost REAL,
  actual_cost_coverage REAL NOT NULL CHECK (actual_cost_coverage >= 0 AND actual_cost_coverage <= 1),
  relative_cost_index REAL NOT NULL CHECK (relative_cost_index >= 0 AND relative_cost_index <= 100),
  feasibility_score REAL NOT NULL CHECK (feasibility_score >= 0 AND feasibility_score <= 100),
  rank_score REAL,
  rank_tier TEXT NOT NULL CHECK (rank_tier IN ('cost_complete', 'cost_incomplete')),
  cost_basis_json TEXT NOT NULL,
  evaluated_at TEXT NOT NULL,
  scorer_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extraction_job (
  extraction_job_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_sha256 TEXT NOT NULL CHECK (length(prompt_sha256) = 64),
  input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
  response_sha256 TEXT CHECK (response_sha256 IS NULL OR length(response_sha256) = 64),
  source_url TEXT,
  raw_response_json TEXT,
  token_cost_json TEXT,
  status TEXT NOT NULL CHECK (status IN ('queued', 'completed', 'failed', 'needs_review')),
  review_status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_reaction_participant_compound_role
  ON reaction_participant(compound_id, role);
CREATE INDEX IF NOT EXISTS idx_reaction_evidence ON reaction_instance(evidence_span_id);
CREATE INDEX IF NOT EXISTS idx_quote_compound_date ON material_quote(compound_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_hazard_compound ON hazard_classification(compound_id);
CREATE INDEX IF NOT EXISTS idx_route_target ON route_candidate(target_compound_id, generated_at);

DROP VIEW IF EXISTS kg_edge;
DROP VIEW IF EXISTS kg_node;

CREATE VIEW kg_node AS
SELECT 'compound:' || compound_id AS node_id, 'compound' AS node_type,
       COALESCE(preferred_name, compound_id) AS label, compound_id AS record_id
FROM compound
UNION ALL
SELECT 'reaction:' || reaction_id, 'reaction', reaction_name, reaction_id
FROM reaction_instance
UNION ALL
SELECT 'patent:' || publication_number, 'patent',
       COALESCE(title, publication_number), publication_number
FROM patent_document
UNION ALL
SELECT 'supplier:' || supplier_id, 'supplier', supplier_name, supplier_id
FROM supplier
UNION ALL
SELECT 'element:' || element_id, 'element', symbol, CAST(element_id AS TEXT)
FROM element
UNION ALL
SELECT 'functional_group:' || functional_group_id, 'functional_group',
       preferred_name, functional_group_id
FROM functional_group;

CREATE VIEW kg_edge AS
SELECT 'compound:' || compound_id AS source_id,
       'reaction:' || reaction_id AS target_id,
       role AS edge_type, reaction_id || ':' || compound_id || ':' || role AS record_id
FROM reaction_participant WHERE role <> 'produced'
UNION ALL
SELECT 'reaction:' || reaction_id, 'compound:' || compound_id,
       'produced', reaction_id || ':' || compound_id || ':produced'
FROM reaction_participant WHERE role = 'produced'
UNION ALL
SELECT 'patent:' || e.publication_number, 'reaction:' || r.reaction_id,
       'supports', r.reaction_id || ':evidence'
FROM reaction_instance r JOIN evidence_span e ON e.evidence_span_id = r.evidence_span_id
UNION ALL
SELECT 'supplier:' || q.supplier_id, 'compound:' || q.compound_id,
       'quotes', q.quote_id
FROM material_quote q
UNION ALL
SELECT 'compound:' || ce.compound_id, 'element:' || ce.element_id,
       'contains_element', ce.compound_id || ':element:' || ce.element_id
FROM compound_element ce
UNION ALL
SELECT 'compound:' || cfg.compound_id,
       'functional_group:' || cfg.functional_group_id,
       'has_functional_group',
       cfg.compound_id || ':functional_group:' || cfg.functional_group_id
FROM compound_functional_group cfg;
