import type { Graph } from './types'

export default function GraphView({ graph }: { graph: Graph | null }) {
  if (!graph) return <div className="graph-empty">Generate routes to inspect the evidence graph.</div>
  const compounds = graph.nodes.filter((node) => node.type === 'compound')
  const reactions = graph.nodes.filter((node) => node.type === 'reaction')
  return (
    <div className="graph" aria-label="Knowledge graph">
      <div className="graph-column">
        <span className="eyebrow">Materials</span>
        {compounds.map((node) => (
          <div className="graph-node compound-node" key={node.id} title={node.id}>
            <span className="node-symbol">C</span><span>{node.label}</span>
          </div>
        ))}
      </div>
      <div className="graph-lines" aria-hidden="true">
        {graph.edges.map((edge, index) => <span key={`${edge.source}-${edge.target}-${index}`}>→</span>)}
      </div>
      <div className="graph-column">
        <span className="eyebrow">Reviewed transformations</span>
        {reactions.map((node) => (
          <div className="graph-node reaction-node" key={node.id} title={node.id}>
            <span className="node-symbol">R</span><span>{node.label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

