import { useEffect, useState } from 'react'
import { fetchAutomationStatus } from './api'
import type { AutomationStatus } from './types'

export default function AutomationPanel() {
  const [status, setStatus] = useState<AutomationStatus | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    fetchAutomationStatus().then(setStatus).catch((problem) => {
      setError(problem instanceof Error ? problem.message : 'Automation status is unavailable.')
    })
  }, [])

  const blocked = status?.exceptions.filter((job) => job.status === 'blocked').length || 0
  const failed = status?.exceptions.filter((job) => job.status === 'failed').length || 0

  return (
    <section className="automation-panel" id="automation">
      <div>
        <span className="kicker">Unattended processing</span>
        <h2>Windows + Drive + Colab</h2>
        <p>Verified Drive outputs are imported locally. Text PDFs are processed automatically; scanned PDFs wait in the Colab OCR queue. Chemistry always remains review-gated.</p>
      </div>
      {error && <div className="alert error"><b>Status unavailable</b><span>{error}</span></div>}
      {status && <div className="automation-stats">
        <div><span>Completed jobs</span><strong>{status.status_counts.succeeded || 0}</strong></div>
        <div><span>Blocked inputs</span><strong>{blocked}</strong></div>
        <div><span>Failed jobs</span><strong>{failed}</strong></div>
        <div><span>Automatic approvals</span><strong>{status.automatic_acceptance ? 'On' : 'Off'}</strong></div>
      </div>}
      {status?.exceptions.length ? <details>
        <summary>Exceptions requiring attention ({status.exceptions.length})</summary>
        <ul>{status.exceptions.map((job) => <li key={job.pipeline_job_id}><b>{job.job_type}</b>: {job.error_text || job.status}</li>)}</ul>
      </details> : null}
    </section>
  )
}
