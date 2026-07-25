import { FormEvent, useMemo, useState } from 'react'
import { fetchGraph, generateRoutes, resolveTarget } from './api'
import GraphView from './GraphView'
import RouteCard from './RouteCard'
import CoveragePanel from './CoveragePanel'
import type { GenerateResponse, Graph } from './types'

const benchmarks = ['Acetaminophen', 'Ibuprofen', 'Metformin', 'Sildenafil', 'Apixaban']

export default function App() {
  const [query, setQuery] = useState('Demo benzamide target')
  const [batchMass, setBatchMass] = useState(1000)
  const [maxSteps, setMaxSteps] = useState(6)
  const [result, setResult] = useState<GenerateResponse | null>(null)
  const [graph, setGraph] = useState<Graph | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [view, setView] = useState<'routes' | 'graph'>('routes')

  const savings = useMemo(() => {
    const costs = result?.routes.map((route) => route.evaluation.actual_material_cost).filter((value): value is number => value !== null) || []
    if (costs.length < 2) return null
    return Math.max(...costs) - Math.min(...costs)
  }, [result])

  async function submit(event: FormEvent) {
    event.preventDefault()
    setLoading(true)
    setError('')
    setResult(null)
    setGraph(null)
    setSelected([])
    try {
      const resolution = await resolveTarget(query)
      if (!resolution.resolved || !resolution.target) {
        setResult({ target: { compound_id: query, preferred_name: query }, routes: [], coverage_gap: true, message: resolution.message })
        return
      }
      const [routes, subgraph] = await Promise.all([
        generateRoutes(resolution.target.compound_id, batchMass, maxSteps),
        fetchGraph(resolution.target.compound_id),
      ])
      setResult(routes)
      setGraph(subgraph)
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'The local service could not complete the request.')
    } finally {
      setLoading(false)
    }
  }

  function exportJson() {
    if (!result) return
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `scaleup-${result.target.compound_id}.json`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="app-shell">
      <header>
        <a className="brand" href="#top" aria-label="ScaleUp Graph home">
          <span className="brand-mark">S</span>
          <span><b>ScaleUp</b><small>GRAPH</small></span>
        </a>
        <nav><a className="active" href="#explorer">Route explorer</a><a href="#coverage">Coverage</a><a href="#principles">Methods</a></nav>
        <span className="local-status"><i /> Local evidence store</span>
      </header>

      <main id="top">
        <section className="hero">
          <div>
            <span className="kicker">Evidence-bounded process intelligence</span>
            <h1>Find a lower-cost synthesis route.<br /><em>Know why it ranks.</em></h1>
            <p>Search only reviewed reaction instances, trace every step to its source, and compare package-aware raw-material economics without a required external API.</p>
          </div>
          <div className="hero-stat"><span>Ranking policy</span><strong>50<span>%</span></strong><p>cost</p><strong>50<span>%</span></strong><p>feasibility</p></div>
        </section>

        <section className="workspace" id="explorer">
          <aside className="controls">
            <span className="panel-label">Route brief</span>
            <form onSubmit={submit}>
              <label>Target name or SMILES
                <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="e.g. Apixaban or canonical SMILES" required />
              </label>
              <div className="field-row">
                <label>Target batch
                  <div className="unit-input"><input type="number" min="0.001" max="10000000" step="any" value={batchMass} onChange={(event) => setBatchMass(Number(event.target.value))} /><span>g</span></div>
                </label>
                <label>Maximum steps
                  <select value={maxSteps} onChange={(event) => setMaxSteps(Number(event.target.value))}>
                    {[3, 4, 5, 6, 8, 10, 12].map((value) => <option key={value}>{value}</option>)}
                  </select>
                </label>
              </div>
              <button className="primary" disabled={loading}>{loading ? <><i className="spinner" /> Searching reviewed graph…</> : 'Generate evidence-bounded routes'}</button>
            </form>
            <div className="guardrail"><span>✓</span><p><b>No invented chemistry</b>Routes use accepted reaction instances and reviewed starting-material links only.</p></div>
            <div className="benchmark-list" id="benchmarks">
              <span className="panel-label">Benchmark targets</span>
              {benchmarks.map((name) => <button key={name} onClick={() => setQuery(name)}>{name}<span>→</span></button>)}
            </div>
          </aside>

          <div className="results" aria-live="polite">
            <div className="results-toolbar">
              <div className="view-tabs">
                <button className={view === 'routes' ? 'active' : ''} onClick={() => setView('routes')}>Ranked routes</button>
                <button className={view === 'graph' ? 'active' : ''} onClick={() => setView('graph')}>Knowledge graph</button>
              </div>
              <button className="export" onClick={exportJson} disabled={!result}>Export JSON ↓</button>
            </div>

            {error && <div className="alert error"><b>Service error</b><span>{error}</span></div>}
            {!result && !loading && <div className="empty-state"><div className="orbit"><span>S</span></div><h2>Start with a target brief</h2><p>The local engine will resolve the compound, traverse reviewed transformations, calculate material demand, and rank complete routes.</p></div>}
            {loading && <div className="empty-state"><div className="orbit loading"><span>⌁</span></div><h2>Traversing the graph</h2><p>Checking evidence, material availability, yields, scale precedent, and local quote coverage.</p></div>}
            {result?.coverage_gap && <div className="coverage-gap"><span className="gap-icon">!</span><div><span className="kicker">Honest coverage gap</span><h2>No complete reviewed route found</h2><p>{result.message}</p><p className="next-action">Next data action: acquire a lawful patent snapshot, extract candidates, validate structures and quantities, then accept edges through human review.</p></div></div>}
            {result && !result.coverage_gap && view === 'routes' && <>
              <div className="summary-strip">
                <div><span>Target</span><strong>{result.target.preferred_name}</strong></div>
                <div><span>Complete routes</span><strong>{result.routes.length}</strong></div>
                <div><span>Batch basis</span><strong>{result.target_mass_g?.toLocaleString()} g</strong></div>
                <div><span>Fixture spread</span><strong>{savings === null ? '—' : `$${savings.toFixed(0)}`}</strong></div>
              </div>
              <div className="fixture-warning"><b>Demonstration data</b> These bundled routes and prices are synthetic UI/test fixtures—not patent evidence or a cost-reduction claim.</div>
              {selected.length >= 2 && <div className="compare-bar"><span>{selected.length} routes selected</span><span>Comparison is normalized to the same target, batch and currency date.</span></div>}
              <div className="route-list">
                {result.routes.map((route) => <RouteCard key={route.route_id} route={route} selected={selected.includes(route.route_id)} onSelect={(checked) => setSelected((current) => checked ? [...current, route.route_id] : current.filter((id) => id !== route.route_id))} />)}
              </div>
            </>}
            {result && !result.coverage_gap && view === 'graph' && <GraphView graph={graph} />}
          </div>
        </section>

        <CoveragePanel />

        <section className="methods" id="principles">
          <span className="kicker">What the score means</span>
          <h2>Transparent enough to challenge.</h2>
          <div className="method-grid">
            <article><span>01</span><h3>Material economics</h3><p>Propagates stoichiometry, molecular weight and reviewed yield backward, then applies local quote packs and dated FX.</p></article>
            <article><span>02</span><h3>Scale feasibility</h3><p>Balances cumulative yield, step count, hazards, solvent evidence, demonstrated scale and evidence completeness.</p></article>
            <article><span>03</span><h3>Missing stays missing</h3><p>No hidden imputation. Insufficient quote or stoichiometry coverage moves a route to the cost-incomplete tier.</p></article>
          </div>
        </section>
      </main>
      <footer><span>ScaleUp Graph · MVP 0.2</span><span>Decision support only—not manufacturing instructions.</span></footer>
    </div>
  )
}
