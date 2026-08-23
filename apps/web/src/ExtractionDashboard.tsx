import { useCallback, useEffect, useState } from 'react'
import { fetchAutomationStatus, fetchProvisionalGraph } from './api'
import type { AutomationStatus } from './types'

type ProvisionalStatus = {
  nodes?: unknown[]
  edges?: unknown[]
  provisional_reaction_count?: number
  validation_counts?: Record<string, number>
}

export default function ExtractionDashboard() {
  const [automation, setAutomation] = useState<AutomationStatus | null>(null)
  const [graph, setGraph] = useState<ProvisionalStatus | null>(null)
  const [updated, setUpdated] = useState<Date | null>(null)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const [a, g] = await Promise.all([fetchAutomationStatus(), fetchProvisionalGraph()])
      setAutomation(a)
      setGraph(g)
      setUpdated(new Date())
      setError('')
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Dashboard data is unavailable.')
    }
  }, [])

  useEffect(() => {
    refresh()
    const timer = window.setInterval(refresh, 10000)
    return () => window.clearInterval(timer)
  }, [refresh])

  const counts = automation?.status_counts || {}
  const queued = counts.queued || 0
  const running = counts.running || 0
  const completed = counts.succeeded || 0
  const failed = (counts.failed || 0) + (counts.blocked || 0)
  const total = queued + running + completed + failed
  const progress = total ? Math.round((completed / total) * 100) : 0
  const validation = graph?.validation_counts || {}

  return (
    <section className="extraction-dashboard" id="extraction-dashboard">
      <div className="dashboard-heading">
        <div><span className="kicker">Live extraction control room</span><h2>Overnight relation extraction</h2><p>Auto-refreshes every 10 seconds. Checkpoints remain resumable and all chemistry stays review-gated.</p></div>
        <span className={`run-state ${running ? 'active' : ''}`}><i />{running ? 'Running' : queued ? 'Queued' : 'Idle'}</span>
      </div>
      {error && <div className="alert error"><b>Status unavailable</b><span>{error}</span></div>}
      <div className="dashboard-progress"><div><span>Overall progress</span><strong>{progress}%</strong></div><div className="progress-track"><i style={{ width: `${progress}%` }} /></div><small>{updated ? `Updated ${updated.toLocaleTimeString()}` : 'Waiting for first update'}</small></div>
      <div className="dashboard-stats">
        <div><span>Completed</span><strong>{completed.toLocaleString()}</strong></div>
        <div><span>Running</span><strong>{running.toLocaleString()}</strong></div>
        <div><span>Queued</span><strong>{queued.toLocaleString()}</strong></div>
        <div><span>Failed / blocked</span><strong>{failed.toLocaleString()}</strong></div>
        <div><span>Provisional reactions</span><strong>{(graph?.provisional_reaction_count || 0).toLocaleString()}</strong></div>
        <div><span>Needs review</span><strong>{(validation.needs_review || 0).toLocaleString()}</strong></div>
      </div>
      <div className="dashboard-note"><b>Safety state:</b> automatic acceptance is {automation?.automatic_acceptance ? 'enabled' : 'disabled'}; provisional edges cannot become accepted chemistry automatically.</div>
    </section>
  )
}
