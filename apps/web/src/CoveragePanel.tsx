import { FormEvent, useEffect, useState } from 'react'
import { fetchCoverage } from './api'
import type { CoverageResponse, CoverageStatus } from './types'

const labels: Record<CoverageStatus, string> = {
  identified: 'Identified',
  patents_found: 'Patents found',
  examples_extracted: 'Examples extracted',
  routes_under_review: 'Routes under review',
  complete_reviewed_route: 'Reviewed route',
  price_complete: 'Price complete',
  cost_comparison_ready: 'Cost comparison ready',
  public_evidence_unavailable: 'Public evidence unavailable',
}

const statuses = Object.keys(labels) as CoverageStatus[]

export default function CoveragePanel() {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<CoverageStatus | ''>('')
  const [data, setData] = useState<CoverageResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  async function load(search = query, filter = status) {
    setLoading(true)
    setError('')
    try {
      setData(await fetchCoverage(search, filter))
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Coverage data could not be loaded.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load('', '') }, [])

  function submit(event: FormEvent) {
    event.preventDefault()
    void load()
  }

  return (
    <section className="coverage-section" id="coverage">
      <div className="coverage-heading">
        <div><span className="kicker">Global drug catalogue</span><h2>Coverage is a state, not a promise.</h2></div>
        <p>Each publicly identified small-molecule drug advances only when its patent, example, route, and price evidence exists.</p>
      </div>

      <div className="coverage-stats">
        <div><strong>{data?.total ?? '—'}</strong><span>matching drugs</span></div>
        <div><strong>{data?.status_counts.patents_found ?? '—'}</strong><span>patents found</span></div>
        <div><strong>{data?.status_counts.complete_reviewed_route ?? '—'}</strong><span>reviewed routes</span></div>
        <div><strong>{data?.status_counts.cost_comparison_ready ?? '—'}</strong><span>cost-ready</span></div>
      </div>

      <form className="coverage-filters" onSubmit={submit}>
        <input aria-label="Search drug catalogue" placeholder="Search drug or brand name" value={query} onChange={(event) => setQuery(event.target.value)} />
        <select aria-label="Coverage status" value={status} onChange={(event) => setStatus(event.target.value as CoverageStatus | '')}>
          <option value="">All coverage states</option>
          {statuses.map((value) => <option key={value} value={value}>{labels[value]}</option>)}
        </select>
        <button className="primary" disabled={loading}>{loading ? 'Loading…' : 'Apply'}</button>
      </form>

      {error && <div className="alert error"><b>Coverage error</b><span>{error}</span></div>}
      {!error && data && <div className="coverage-table" role="table" aria-label="Per-drug public evidence coverage">
        <div className="coverage-row coverage-header" role="row">
          <span>Drug</span><span>Status</span><span>Patents</span><span>Examples</span><span>Routes</span><span>Priced</span>
        </div>
        {data.items.map((drug) => <div className="coverage-row" role="row" key={drug.drug_id}>
          <span><b>{drug.preferred_name}</b><small>{drug.compound_count} structure form{drug.compound_count === 1 ? '' : 's'}</small></span>
          <span><i className={`status-dot ${drug.status}`} />{labels[drug.status]}</span>
          <span>{drug.patent_count}</span>
          <span>{drug.extracted_example_count}</span>
          <span>{drug.reviewed_route_count}</span>
          <span>{drug.priced_route_count}</span>
        </div>)}
        {!data.items.length && <div className="coverage-empty">No drugs match this coverage filter.</div>}
      </div>}
      <p className="coverage-note">Coverage records report public evidence only. Missing data remains an explicit gap.</p>
    </section>
  )
}
