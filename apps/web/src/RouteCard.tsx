import type { Route } from './types'

const money = (value: number, currency: string) => new Intl.NumberFormat('en-US', {
  style: 'currency', currency, maximumFractionDigits: 0,
}).format(value)

export default function RouteCard({ route, selected, onSelect }: {
  route: Route
  selected: boolean
  onSelect: (checked: boolean) => void
}) {
  const evaluation = route.evaluation
  return (
    <article className={`route-card ${selected ? 'selected' : ''}`} data-testid="route-card">
      <div className="route-head">
        <div>
          <span className="rank">#{route.rank} ranked route</span>
          <h3>{route.steps.map((step) => step.transformation_key).join(' · ')}</h3>
        </div>
        <label className="compare-check">
          <input type="checkbox" checked={selected} onChange={(event) => onSelect(event.target.checked)} />
          Compare
        </label>
      </div>
      <div className="metrics">
        <div><span>Material cost</span><strong>{evaluation.actual_material_cost === null ? 'Incomplete' : money(evaluation.actual_material_cost, evaluation.currency)}</strong></div>
        <div><span>Cost coverage</span><strong>{Math.round(evaluation.actual_cost_coverage * 100)}%</strong></div>
        <div><span>Relative cost</span><strong>{evaluation.relative_cost_index.toFixed(1)} <small>/ 100</small></strong></div>
        <div><span>Feasibility</span><strong>{evaluation.feasibility_score.toFixed(1)} <small>/ 100</small></strong></div>
      </div>
      <div className="route-steps">
        {route.steps.map((step, index) => (
          <details key={step.reaction_id} open>
            <summary>
              <span className="step-number">{index + 1}</span>
              <span><b>{step.reaction_name}</b><small>{step.inputs.map((input) => input.preferred_name).join(' + ')} → {step.product_compound_id}</small></span>
              <span className="yield">{step.yield_percent ?? '—'}% yield</span>
            </summary>
            <div className="evidence-row">
              <span className={step.is_synthetic ? 'fixture-badge' : 'evidence-badge'}>{step.evidence.label}</span>
              <span>Scale precedent: {step.demonstrated_scale_g ? `${step.demonstrated_scale_g.toLocaleString()} g` : 'missing'}</span>
              <span>Confidence: {Math.round(step.confidence * 100)}%</span>
            </div>
          </details>
        ))}
      </div>
      <div className="cost-lines">
        {evaluation.quote_lines.map((line) => (
          <span key={line.compound_id}>{line.compound_id}: {line.required_mass_g.toFixed(1)} g · {line.packs} pack(s) · {money(line.package_cost, evaluation.currency)}</span>
        ))}
      </div>
      <p className="fineprint">{evaluation.actual_cost_label}</p>
    </article>
  )
}

