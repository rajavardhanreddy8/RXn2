import { useEffect, useMemo, useState } from 'react'
import { fetchProvisionalGraph } from './api'

type Node = { id: string; type: string; label: string; review_status?: string }
type Edge = { id: string; source: string; target: string; type: string; validation_status: string; publication_number?: string }
type Graph = { nodes: Node[]; edges: Edge[]; provisional_reaction_count: number; accepted_chemistry_count: number; truncated?: boolean }

const COLORS: Record<string, string> = {
  compound: '#2b7a57', procedure: '#d98922', provisional_reaction: '#d98922', patent: '#5b75aa', material: '#8f7751', unknown: '#8b9490',
}

export default function ProvisionalGraphPanel() {
  const [graph, setGraph] = useState<Graph | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let live = true
    const load = () => fetchProvisionalGraph(50000)
      .then((value) => { if (live) { setGraph(value as Graph); setError('') } })
      .catch((problem) => { if (live) setError(problem instanceof Error ? problem.message : 'Graph is unavailable.') })
    load()
    const refresh = window.setInterval(load, 30000)
    return () => { live = false; window.clearInterval(refresh) }
  }, [])

  const visible = useMemo(() => {
    if (!graph) return { nodes: [] as Node[], edges: [] as Edge[] }
    const edges = selected ? graph.edges.filter((edge) => edge.source === selected || edge.target === selected) : graph.edges.slice(0, 90)
    const ids = new Set(edges.flatMap((edge) => [edge.source, edge.target]))
    return { edges, nodes: graph.nodes.filter((node) => ids.has(node.id)).slice(0, 48) }
  }, [graph, selected])
  const positions = useMemo(() => new Map(visible.nodes.map((node, index) => {
    const angle = (index / Math.max(visible.nodes.length, 1)) * Math.PI * 2
    const radius = index % 3 === 0 ? 37 : 31 + (index % 5) * 2
    return [node.id, { x: 50 + Math.cos(angle) * radius, y: 50 + Math.sin(angle) * radius }]
  })), [visible.nodes])
  const byId = new Map(visible.nodes.map((node) => [node.id, node]))

  return <section className="provisional-graph-panel" id="provisional-graph">
    <div className="graph-panel-heading"><div><span className="kicker">Whole dataset evidence graph</span><h2>Provisional reaction map</h2><p>All recorded relation candidates are counted and refreshed every 30 seconds. The visual samples the network for readability; click a node to isolate its recorded relations.</p></div><div className="graph-totals"><b>{graph?.nodes.length || 0}</b><span>recorded nodes</span><b>{graph?.edges.length || 0}</b><span>recorded relations</span></div></div>
    {error && <div className="alert error"><b>Graph unavailable</b><span>{error}</span></div>}
    {graph && <>
      <div className="graph-legend"><span><i className="compound" />Resolved compound</span><span><i className="procedure" />Procedure / candidate reaction</span><span><i className="patent" />Patent evidence</span><button onClick={() => setSelected(null)} disabled={!selected}>Show overview</button></div>
      <div className="network-wrap">
        <svg viewBox="0 0 100 100" role="img" aria-label="Provisional evidence relation graph">
          {visible.edges.map((edge) => { const a = positions.get(edge.source); const b = positions.get(edge.target); if (!a || !b) return null; return <line key={edge.id} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={`edge ${edge.validation_status}`} /> })}
          {visible.nodes.map((node) => { const p = positions.get(node.id)!; const color = COLORS[node.type] || COLORS.unknown; return <g key={node.id} className="network-node" onClick={() => setSelected(node.id)} tabIndex={0} role="button"><circle cx={p.x} cy={p.y} r={selected === node.id ? 3.4 : 2.25} fill={color} /><title>{node.label} · {node.type} · needs review</title></g> })}
        </svg>
      </div>
      <div className="graph-selection">{selected ? <><b>{byId.get(selected)?.label || selected}</b><span>{visible.edges.length} direct recorded relations · all need review</span></> : <span>Showing a readable sample of the complete recorded graph. {graph.truncated ? 'The API response is capped at 50,000 records.' : 'All recorded relations are included in the totals.'}</span>}</div>
    </>}
  </section>
}
