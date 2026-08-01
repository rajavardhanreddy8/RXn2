import { useEffect, useState } from 'react'
import { fetchGraphNode } from './api'
import type { Graph } from './types'

export default function GraphView({ graph }: { graph: Graph | null }) {
  const [current, setCurrent] = useState(graph)
  const [loading, setLoading] = useState(false)
  useEffect(() => setCurrent(graph), [graph])
  if (!current) return <div className="graph-empty">Generate routes to inspect the evidence graph.</div>
  const graphData = current

  const selectedId = graphData.selected_node || graphData.nodes[0]?.id
  const selected = graphData.nodes.find((node) => node.id === selectedId)
  const incoming = graphData.edges.filter((edge) => edge.target === selectedId)
  const outgoing = graphData.edges.filter((edge) => edge.source === selectedId)
  const byId = new Map(graphData.nodes.map((node) => [node.id, node]))

  async function open(nodeId: string) {
    setLoading(true)
    try { setCurrent(await fetchGraphNode(nodeId)) } finally { setLoading(false) }
  }

  function neighbors(edges: typeof graphData.edges, incomingSide: boolean) {
    return edges.map((edge) => {
      const node = byId.get(incomingSide ? edge.source : edge.target)
      if (!node) return null
      return (
        <button className="graph-node" key={`${edge.source}-${edge.target}-${edge.type}`} onClick={() => open(node.id)} disabled={loading}>
          <span className="node-symbol">{node.type.slice(0, 1).toUpperCase()}</span>
          <span><b>{node.label}</b><small>{edge.type.replaceAll('_', ' ')}</small></span>
        </button>
      )
    })
  }

  return (
    <div className="graph" aria-label="Bidirectional knowledge graph">
      <div className="graph-column">
        <span className="eyebrow">Incoming / upstream</span>
        {neighbors(incoming, true)}
        {!incoming.length && <span className="graph-note">No recorded incoming edges</span>}
      </div>
      <div className="graph-focus" aria-live="polite">
        <span>→</span>
        <div className="graph-node graph-selected">
          <span className="node-symbol">{selected?.type.slice(0, 1).toUpperCase()}</span>
          <span><b>{selected?.label || selectedId}</b><small>{selected?.type}</small></span>
        </div>
        <span>→</span>
      </div>
      <div className="graph-column">
        <span className="eyebrow">Outgoing / downstream</span>
        {neighbors(outgoing, false)}
        {!outgoing.length && <span className="graph-note">No recorded outgoing edges</span>}
      </div>
      {graphData.truncated && <p className="graph-note">Showing the first 200 relationships.</p>}
      {graphData.disclaimer && <p className="graph-disclaimer">{graphData.disclaimer}</p>}
    </div>
  )
}
