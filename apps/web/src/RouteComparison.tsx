import { useState, useEffect } from 'react'
import {
  createComparison,
  fetchProjectComparisons,
  fetchProjectComparison,
  generateRoutes,
  resolveTarget,
} from './api'
import type {
  Project,
  SavedRoute,
  OptimizationRun,
  RouteMetric,
  Route,
} from './types'

export type ComparisonSummary = {
  _id: string
  projectId: string
  createdBy: string
  routeSnapshotsCount: number
  target: { compoundName?: string; targetMassG: number }
  methodVersion: string
  status: 'completed' | 'failed'
  error?: string | null
  createdAt: string
}

interface RouteComparisonProps {
  project: Project
  routes: SavedRoute[]
  onOpenCreateRoute: () => void
}

export default function RouteComparison({
  project,
  routes,
  onOpenCreateRoute,
}: RouteComparisonProps) {
  const [activeTab, setActiveTab] = useState<'compare' | 'history'>('compare')

  // Selection state
  const [selectedRouteIds, setSelectedRouteIds] = useState<string[]>([])
  const [comparing, setComparing] = useState(false)
  const [comparisonError, setComparisonError] = useState('')
  const [currentRun, setCurrentRun] = useState<OptimizationRun | null>(null)

  // Fast-API Generated candidate routes state
  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState('')
  const [coverageGap, setCoverageGap] = useState<{ gap: boolean; message: string } | null>(null)
  const [generatedCandidates, setGeneratedCandidates] = useState<Route[]>([])

  // History state
  const [historyRuns, setHistoryRuns] = useState<ComparisonSummary[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [viewingSnapshotRun, setViewingSnapshotRun] = useState<OptimizationRun | null>(null)

  // Load comparison history when project changes or tab switches
  useEffect(() => {
    if (project._id) {
      loadHistory()
    }
  }, [project._id, activeTab])

  // Auto-select first 2 routes if available and nothing selected
  useEffect(() => {
    if (routes.length >= 2 && selectedRouteIds.length === 0) {
      setSelectedRouteIds(routes.slice(0, 2).map((r) => r._id))
    }
  }, [routes])

  async function loadHistory() {
    setHistoryLoading(true)
    setHistoryError('')
    try {
      const res = await fetchProjectComparisons(project._id)
      setHistoryRuns(res.comparisons)
    } catch (err) {
      setHistoryError(err instanceof Error ? err.message : 'Failed to load comparison history')
    } finally {
      setHistoryLoading(false)
    }
  }

  function toggleRouteSelection(routeId: string) {
    setSelectedRouteIds((prev) => {
      if (prev.includes(routeId)) {
        return prev.filter((id) => id !== routeId)
      } else {
        if (prev.length >= 10) {
          alert('You can select a maximum of 10 routes for comparison.')
          return prev
        }
        return [...prev, routeId]
      }
    })
  }

  async function handleRunComparison() {
    if (selectedRouteIds.length < 2) {
      setComparisonError('Please select at least 2 routes to perform a side-by-side comparison.')
      return
    }
    if (selectedRouteIds.length > 10) {
      setComparisonError('Maximum 10 routes can be compared at one time.')
      return
    }

    setComparing(true)
    setComparisonError('')
    setCurrentRun(null)
    setViewingSnapshotRun(null)

    try {
      const res = await createComparison(project._id, {
        routeIds: selectedRouteIds,
        baseCurrency: 'USD',
      })
      setCurrentRun(res.comparison)
      // refresh history list in background
      loadHistory()
    } catch (err) {
      setComparisonError(
        err instanceof Error
          ? err.message
          : 'Comparison failed. The service could not complete the multi-route analysis.'
      )
    } finally {
      setComparing(false)
    }
  }

  async function handleFetchGeneratedCandidates() {
    setGenerating(true)
    setGenerateError('')
    setCoverageGap(null)
    setGeneratedCandidates([])

    const targetQuery = project.targetCompoundId || project.targetCompoundName
    if (!targetQuery) {
      setGenerateError('Project has no Target Compound ID or Name specified.')
      setGenerating(false)
      return
    }

    try {
      // 1. Resolve Target
      const resolution = await resolveTarget(targetQuery)
      if (!resolution.resolved || !resolution.target) {
        setCoverageGap({
          gap: true,
          message: resolution.message || `No reviewed graph entry found for target "${targetQuery}".`,
        })
        return
      }

      // 2. Generate Routes from FastAPI Bridge
      const genRes = await generateRoutes(
        resolution.target.compound_id,
        project.targetMassG * (project.targetMassUnit === 'kg' ? 1000 : project.targetMassUnit === 'mg' ? 0.001 : 1),
        8
      )

      if (genRes.coverage_gap || !genRes.routes || genRes.routes.length === 0) {
        setCoverageGap({
          gap: true,
          message:
            genRes.message ||
            'Coverage gap: No complete reviewed route instances found in current evidence database.',
        })
      } else {
        setGeneratedCandidates(genRes.routes)
      }
    } catch (err) {
      setGenerateError(
        err instanceof Error
          ? err.message
          : 'Failed to contact FastAPI service for candidate route generation.'
      )
    } finally {
      setGenerating(false)
    }
  }

  async function openHistoricalRun(runId: string) {
    try {
      const res = await fetchProjectComparison(project._id, runId)
      setViewingSnapshotRun(res.comparison)
      setActiveTab('compare')
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to fetch historical run')
    }
  }

  // Render Metric Status Pill
  function renderMetricBadge(status: 'complete' | 'partial' | 'unknown', origin?: string) {
    const statusClass = `status-pill status-${status}`
    return (
      <div className="metric-badge-container">
        <span className={statusClass}>{status.toUpperCase()}</span>
        {origin && <span className="origin-pill">{origin}</span>}
      </div>
    )
  }

  // Render Single Metric Cell
  function renderMetricCell(
    metric: RouteMetric | undefined,
    formatter: (v: number) => string
  ) {
    if (!metric) {
      return (
        <div className="metric-cell">
          <div className="metric-value-row">
            <span className="metric-value empty-val">—</span>
            {renderMetricBadge('unknown')}
          </div>
        </div>
      )
    }

    const isNum = typeof metric.value === 'number'
    const isAvailable = metric.value !== null && metric.value !== undefined

    return (
      <div className="metric-cell">
        <div className="metric-value-row">
          <span className={`metric-value ${!isAvailable ? 'empty-val' : ''}`}>
            {isAvailable ? (isNum ? formatter(metric.value as number) : String(metric.value)) : '—'}
          </span>
          {renderMetricBadge(metric.status, metric.origin)}
        </div>
        {metric.warnings && metric.warnings.length > 0 && (
          <ul className="metric-warnings-list">
            {metric.warnings.map((w: string, i: number) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        )}
      </div>
    )
  }

  const runToDisplay = viewingSnapshotRun || currentRun

  return (
    <div className="route-comparison-module">
      {/* Top Header & Tabs */}
      <div className="comparison-header">
        <div>
          <h3>Route Comparison & Evaluation Engine</h3>
          <p>
            Multi-route normalized comparison (2–10 routes) evaluating cumulative yield, total duration,
            material economics, and scale safety.
          </p>
        </div>
        <div className="view-tabs">
          <button
            className={activeTab === 'compare' ? 'active' : ''}
            onClick={() => {
              setActiveTab('compare')
              setViewingSnapshotRun(null)
            }}
          >
            Live Comparison
          </button>
          <button
            className={activeTab === 'history' ? 'active' : ''}
            onClick={() => setActiveTab('history')}
          >
            Run History ({historyRuns.length})
          </button>
        </div>
      </div>

      {activeTab === 'compare' && (
        <div className="compare-workspace-layout">
          {/* Controls / Route Selector Card */}
          <section className="comparison-selector-card">
            <div className="selector-card-header">
              <h4>1. Select Routes to Compare (2 to 10 routes)</h4>
              <span className="selection-count-badge">
                {selectedRouteIds.length} / 10 Selected
              </span>
            </div>

            {routes.length === 0 ? (
              <div className="no-routes-alert">
                <p>No saved routes found in this project.</p>
                <button className="secondary small-btn" onClick={onOpenCreateRoute}>
                  + Draft Manual Route First
                </button>
              </div>
            ) : (
              <div className="route-selection-list">
                {routes.map((r) => {
                  const isChecked = selectedRouteIds.includes(r._id)
                  return (
                    <label
                      key={r._id}
                      className={`route-select-item ${isChecked ? 'selected' : ''}`}
                    >
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={() => toggleRouteSelection(r._id)}
                      />
                      <div className="route-select-info">
                        <div className="route-select-title">
                          <strong>Route ID: {r._id.slice(-6)}</strong>
                          <span className={`origin-badge origin-${r.origin}`}>
                            {r.origin.toUpperCase()}
                          </span>
                          <span className="revision-badge">Rev #{r.revision}</span>
                        </div>
                        <small>
                          {r.steps.length} Steps · Updated {new Date(r.updatedAt).toLocaleDateString()}
                        </small>
                      </div>
                    </label>
                  )
                })}
              </div>
            )}

            <div className="selector-actions">
              <button
                className="primary"
                disabled={comparing || selectedRouteIds.length < 2 || selectedRouteIds.length > 10}
                onClick={handleRunComparison}
              >
                {comparing ? (
                  <>
                    <span className="spinner" /> Evaluating Normalized Metrics…
                  </>
                ) : (
                  `Run Multi-Route Comparison (${selectedRouteIds.length} Routes)`
                )}
              </button>

              <button
                type="button"
                className="secondary small-btn generate-bridge-btn"
                disabled={generating}
                onClick={handleFetchGeneratedCandidates}
              >
                {generating ? (
                  <>
                    <span className="spinner" /> Querying FastAPI Bridge…
                  </>
                ) : (
                  '⚡ Query FastAPI for Candidate Routes'
                )}
              </button>
            </div>

            {comparisonError && (
              <div className="alert error" style={{ marginTop: '14px' }}>
                <b>Comparison Error:</b> {comparisonError}
                <div style={{ marginTop: '6px', fontSize: '12px' }}>
                  <i>Note: Project and route drafts remain safe and unchanged.</i>
                </div>
              </div>
            )}

            {generateError && (
              <div className="alert error" style={{ marginTop: '14px' }}>
                <b>FastAPI Service Notice:</b> {generateError}
              </div>
            )}

            {/* Coverage Gap Alert Box */}
            {coverageGap?.gap && (
              <div className="coverage-gap-banner">
                <div className="gap-icon">⚠️</div>
                <div className="gap-content">
                  <span className="kicker">Honest Coverage Gap</span>
                  <h4>No complete reviewed route found in graph</h4>
                  <p>{coverageGap.message}</p>
                  <p className="gap-action-text">
                    Under strict evidence bounds, unverified or partial reaction links are never fabricated.
                    You can create a manual route draft to sequence your own literature procedure.
                  </p>
                  <button className="primary small-btn" onClick={onOpenCreateRoute}>
                    + Draft Manual Route Fallback
                  </button>
                </div>
              </div>
            )}

            {/* Generated Candidates Notification */}
            {generatedCandidates.length > 0 && (
              <div className="generated-candidates-box">
                <h5>⚡ FastAPI Generated Candidates ({generatedCandidates.length})</h5>
                <p>
                  Found {generatedCandidates.length} candidate route(s) from reviewed graph. You can inspect them
                  in the Route Explorer and select them here for a saved comparison. Synthetic fixture results remain demonstration data.
                </p>
                <div className="candidates-mini-list">
                  {generatedCandidates.map((c) => (
                    <label key={c.route_id} className="candidate-mini-card">
                      <input type="checkbox" checked={selectedRouteIds.includes(c.route_id)} onChange={() => toggleRouteSelection(c.route_id)} aria-label={`Select generated route ${c.route_id}`} />
                      <strong>Route {c.route_id.slice(-6)}</strong>
                      <span>{c.steps.length} steps</span>
                      {c.evaluation?.actual_material_cost != null && (
                        <span>${c.evaluation.actual_material_cost.toFixed(2)}</span>
                      )}
                    </label>
                  ))}
                </div>
              </div>
            )}
          </section>

          {/* Comparison Results Section */}
          <section className="comparison-results-panel">
            {runToDisplay ? (
              <div className="comparison-run-display">
                <div className="run-meta-banner">
                  <div>
                    <span className="kicker">
                      {viewingSnapshotRun ? 'Historical Snapshot View' : 'Live Comparison Snapshot'}
                    </span>
                    <h4>
                      Run #{runToDisplay._id.slice(-6)} · Status:{' '}
                      <span className={`status-tag status-${runToDisplay.status}`}>
                        {runToDisplay.status.toUpperCase()}
                      </span>
                    </h4>
                    <small>
                      Executed: {new Date(runToDisplay.startedAt).toLocaleString()} · Method v
                      {runToDisplay.methodVersion} · Data v{runToDisplay.dataVersion}
                    </small>
                  </div>
                  {viewingSnapshotRun && (
                    <button
                      className="secondary small-btn"
                      onClick={() => setViewingSnapshotRun(null)}
                    >
                      ← Back to Live Editor
                    </button>
                  )}
                </div>

                <div className="snapshot-notice">
                  <b>🔒 Immutable Snapshot:</b> This comparison record is locked. Future edits to project routes
                  will not alter this historical evaluation.
                </div>

                <div className="fixture-warning" style={{ margin: '0 0 10px' }}>
                  <b>Demonstration notice:</b> Synthetic demonstration data — not validated chemistry. Decision support only, not manufacturing instructions.
                </div>

                {/* Side-by-Side Comparison Grid / Cards */}
                <div className="routes-comparison-grid">
                  {runToDisplay.routeSnapshots.map((snapshot, idx) => (
                    <div key={snapshot.routeId || idx} className="route-comparison-card">
                      <div className="card-head">
                        <div>
                          <span className={`origin-badge origin-${snapshot.origin}`}>
                            {snapshot.origin.toUpperCase()}
                          </span>
                          <span className="revision-badge">Rev #{snapshot.revision}</span>
                        </div>
                        <h4>Route #{snapshot.routeId.slice(-6)}</h4>
                        <small>{snapshot.steps.length} Reaction Steps</small>
                      </div>

                      <div className="metrics-block">
                        {/* Overall Yield */}
                        <div className="metric-row">
                          <span className="metric-title">Cumulative Yield</span>
                          {renderMetricCell(snapshot.metrics?.yield, (v) => `${v.toFixed(1)} %`)}
                        </div>

                        {/* Duration */}
                        <div className="metric-row">
                          <span className="metric-title">Total Duration</span>
                          {renderMetricCell(snapshot.metrics?.duration, (v) => `${v.toFixed(1)} h`)}
                        </div>

                        {/* Material Economics / Cost */}
                        <div className="metric-row">
                          <span className="metric-title">Material Cost</span>
                          {renderMetricCell(
                            snapshot.metrics?.cost,
                            (v) => `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                          )}
                        </div>

                        {/* Scale Safety / Feasibility */}
                        <div className="metric-row">
                          <span className="metric-title">Known Hazard Flags</span>
                          {renderMetricCell(
                            snapshot.metrics?.safety,
                            (v) => `Feasibility: ${(v * 100).toFixed(0)}%`
                          )}
                        </div>
                        <div className="metric-row">
                          <span className="metric-title">Solvent Diversity Screening</span>
                          {renderMetricCell(snapshot.metrics?.green, v => `${v} distinct solvents`)}
                        </div>
                      </div>

                      {/* Evidence & Step Summary */}
                      <div className="snapshot-steps-accordion">
                        <strong>Step Sequence & Evidence ({snapshot.steps.length} Steps):</strong>
                        <ol className="snapshot-steps-list">
                          {snapshot.steps.map((s) => (
                            <li key={s.position} className="snapshot-step-item">
                              <div className="step-item-title">
                                <b>{s.name || `Step ${s.position}`}</b>
                                <span>
                                  {s.yieldPercent !== null ? `${s.yieldPercent}%` : 'No yield'} ·{' '}
                                  {s.durationHours !== null ? `${s.durationHours} ${s.durationUnit}` : 'No duration'}
                                </span>
                              </div>
                              {s.hazardNotes && (
                                <div className="step-hazard-flag">
                                  ⚠️ <b>Hazard:</b> {s.hazardNotes}
                                </div>
                              )}
                              {s.evidenceRef && (
                                <div className="step-evidence-citation">
                                  📜 <b>Evidence:</b> {s.evidenceRef}
                                </div>
                              )}
                            </li>
                          ))}
                        </ol>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div className="empty-comparison-placeholder">
                <div className="orbit"><span>⚖️</span></div>
                <h3>Multi-Route Comparison Ready</h3>
                <p>
                  Select 2 to 10 routes from the left panel and click <b>Run Multi-Route Comparison</b> to generate
                  side-by-side metric normalization and an immutable audit snapshot.
                </p>
              </div>
            )}
          </section>
        </div>
      )}

      {/* History Tab */}
      {activeTab === 'history' && (
        <section className="comparison-history-section">
          <div className="history-header">
            <h4>Saved Optimization & Comparison Runs ({historyRuns.length})</h4>
            <button className="small-action" onClick={loadHistory}>
              ↻ Refresh Runs
            </button>
          </div>

          {historyLoading && (
            <div className="loading-state-small">
              <span className="spinner" /> Loading comparison run snapshots…
            </div>
          )}

          {historyError && (
            <div className="alert error">
              <span>{historyError}</span>
            </div>
          )}

          {!historyLoading && historyRuns.length === 0 && (
            <div className="empty-history-box">
              <p>No comparisons have been recorded for this project yet.</p>
              <button className="primary small-btn" onClick={() => setActiveTab('compare')}>
                Run First Comparison
              </button>
            </div>
          )}

          <div className="history-runs-table-container">
            {historyRuns.length > 0 && (
              <table className="history-table">
                <thead>
                  <tr>
                    <th>Run ID</th>
                    <th>Date & Time</th>
                    <th>Status</th>
                    <th>Routes Compared</th>
                    <th>Target Compound</th>
                    <th>Method Version</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {historyRuns.map((run) => (
                    <tr key={run._id}>
                      <td>
                        <code>{run._id.slice(-8)}</code>
                      </td>
                      <td>{new Date(run.createdAt || Date.now()).toLocaleString()}</td>
                      <td>
                        <span className={`status-tag status-${run.status}`}>
                          {run.status.toUpperCase()}
                        </span>
                      </td>
                      <td>{run.routeSnapshotsCount || 0} routes</td>
                      <td>{run.target?.compoundName || '—'}</td>
                      <td>v{run.methodVersion}</td>
                      <td>
                        <button
                          className="secondary small-btn"
                          onClick={() => openHistoricalRun(run._id)}
                        >
                          View Snapshot →
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      )}
    </div>
  )
}
