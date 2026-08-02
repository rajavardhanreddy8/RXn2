import { useEffect, useState } from 'react'
import { fetchReviewQueue } from './api'
import type { ReviewQueueResponse } from './types'

export default function ReviewQueuePanel() {
  const [data, setData] = useState<ReviewQueueResponse | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    fetchReviewQueue().then(setData).catch((problem: unknown) => {
      setError(problem instanceof Error ? problem.message : 'Priority queue could not be loaded.')
    })
  }, [])

  return (
    <section className="coverage-section" id="review-queue">
      <div className="coverage-heading">
        <div><span className="kicker">Next evidence work</span><h2>One patent family per drug.</h2></div>
        <p>Ranked acquisition candidates only. They are not accepted routes and still require chemistry review.</p>
      </div>
      {error && <div className="alert error"><b>Queue error</b><span>{error}</span></div>}
      {data?.message && <div className="alert error"><b>Queue unavailable</b><span>{data.message}</span></div>}
      {data?.items.length ? <div className="coverage-table" role="table" aria-label="Priority patent review queue">
        <div className="coverage-row coverage-header" role="row"><span>Rank</span><span>Drug</span><span>Publication</span><span>Candidate title</span><span>Why selected</span><span>Next action</span></div>
        {data.items.map((item) => <div className="coverage-row" role="row" key={`${item.drug_id}:${item.family_id}`}>
          <span>{item.rank}</span><span><b>{item.drug_name}</b><small>{item.country_code} · {item.publication_date || 'date unavailable'}</small></span><span>{item.publication_number}</span><span>{item.title}</span><span>{item.selection_rationale}</span><span>{item.next_action}</span>
        </div>)}
      </div> : !error && !data?.message && <div className="coverage-empty">Loading priority candidates…</div>}
      <p className="coverage-note">No candidate in this queue is a verified manufacturing route.</p>
    </section>
  )
}