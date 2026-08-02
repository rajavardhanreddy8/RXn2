export type Evidence = {
  publication_number: string | null
  source_url: string | null
  evidence_status: string | null
  label: string
}

export type RouteStep = {
  reaction_id: string
  reaction_name: string
  transformation_key: string
  product_compound_id: string
  yield_percent: number | null
  demonstrated_scale_g: number | null
  confidence: number
  is_synthetic: boolean
  evidence: Evidence
  inputs: Array<{
    compound_id: string
    preferred_name: string
    stoichiometry: number | null
  }>
}

export type Evaluation = {
  actual_material_cost: number | null
  actual_cost_label: string
  actual_cost_coverage: number
  relative_cost_index: number
  feasibility_score: number
  rank_tier: 'cost_complete' | 'cost_incomplete'
  rank_score: number
  currency: string
  quote_lines: Array<{
    compound_id: string
    supplier_id: string
    required_mass_g: number
    packs: number
    package_cost: number
  }>
  unpriced_materials: Array<{ compound_id: string; required_mass_g: number }>
  warnings: string[]
}

export type Route = {
  route_id: string
  rank: number
  step_count: number
  steps: RouteStep[]
  evaluation: Evaluation
}

export type GenerateResponse = {
  target: { compound_id: string; preferred_name: string }
  target_mass_g?: number
  base_currency?: string
  routes: Route[]
  coverage_gap: boolean
  message?: string
  disclaimer?: string
}

export type Graph = {
  selected_node?: string
  direction?: 'incoming' | 'outgoing' | 'both'
  truncated?: boolean
  disclaimer?: string
  nodes: Array<{ id: string; type: string; label: string; record_id?: string }>
  edges: Array<{ source: string; target: string; type: string; traversed_from?: 'incoming' | 'outgoing' }>
}

export type CoverageStatus =
  | 'identified'
  | 'patents_found'
  | 'examples_extracted'
  | 'routes_under_review'
  | 'complete_reviewed_route'
  | 'price_complete'
  | 'cost_comparison_ready'
  | 'public_evidence_unavailable'

export type DrugCoverage = {
  drug_id: string
  preferred_name: string
  modality: string
  status: CoverageStatus
  identified: boolean
  patents_found: boolean
  examples_extracted: boolean
  routes_under_review: boolean
  complete_reviewed_route: boolean
  price_complete: boolean
  cost_comparison_ready: boolean
  public_evidence_unavailable: boolean
  patent_count: number
  extracted_example_count: number
  reviewed_route_count: number
  priced_route_count: number
  compound_count: number
  product_count: number
  marketing_statuses: string[]
  refreshed_at: string
}

export type CoverageResponse = {
  total: number
  limit: number
  offset: number
  status_counts: Record<CoverageStatus, number>
  items: DrugCoverage[]
}

export type AutomationJob = {
  pipeline_job_id: string
  job_type: string
  input_identity: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'blocked' | 'skipped'
  attempt_count: number
  started_at: string | null
  completed_at: string | null
  error_text: string | null
}

export type AutomationStatus = {
  mode: string
  scheduler: string
  automatic_acceptance: boolean
  status_counts: Record<string, number>
  exceptions: AutomationJob[]
  recent_jobs: AutomationJob[]
}

export type ReviewQueueItem = {
  rank: number
  drug_id: string
  drug_name: string
  family_id: string
  publication_number: string
  title: string
  country_code: string
  publication_date: string | null
  selection_rationale: string
  next_action: string
}

export type ReviewQueueResponse = {
  total: number
  items: ReviewQueueItem[]
  automatic_acceptance: boolean
  message?: string
}