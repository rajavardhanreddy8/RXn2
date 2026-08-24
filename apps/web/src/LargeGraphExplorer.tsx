import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { MultiGraph } from 'graphology'
import forceAtlas2 from 'graphology-layout-forceatlas2'
import Sigma from 'sigma'
import {
  fetchLargeGraphNeighborhood, fetchLargeGraphOverview, fetchLargeGraphStats,
  fetchLargeRouteGraph, fetchMoleculeStructure, findLargeGraphPath, isHostedGraph, searchLargeGraph,
} from './api'
import type { LargeGraphEdge, LargeGraphNeighborhood, LargeGraphNode, LargeGraphOverview, LargeGraphStats, MoleculeStructure } from './api'

const COLORS: Record<string, string> = {
  drug: '#146c4b', moiety: '#23835d', compound: '#34a172', product: '#8a6b43',
  patent_family: '#45659a', patent: '#607fbb', evidence: '#8a8e8c', procedure: '#d98b21',
  reaction: '#ea8c17', route: '#ab6312', element: '#7562a8', functional_group: '#8e78bd',
  condition: '#707a75', quantity: '#7c837f', outcome: '#52645a', material: '#8a7d62',
}
const STATUS_COLORS: Record<string, string> = { validated: '#3a8b64', unresolved: '#c39936', rejected: '#b66555' }
const pretty = (value: string) => value.replaceAll('_', ' ')
const hash = (value: string) => [...value].reduce((total, char) => ((total * 31) + char.charCodeAt(0)) >>> 0, 17)

type DisplayGraph = { nodes: LargeGraphNode[]; edges: LargeGraphEdge[]; selected?: string; truncated?: boolean }

function MoleculePreview({ compoundId }: { compoundId: string }) {
  const [structure, setStructure] = useState<MoleculeStructure | null>(null)
  const [problem, setProblem] = useState('')
  useEffect(() => {
    setStructure(null); setProblem('')
    fetchMoleculeStructure(compoundId).then(setStructure).catch((error: unknown) => {
      setProblem(error instanceof Error ? error.message : 'Structure unavailable')
    })
  }, [compoundId])
  if (problem) return <p className="molecule-unavailable">Molecular structure unavailable: {problem}</p>
  if (!structure) return <p className="molecule-unavailable">Loading atom-and-bond structure…</p>
  const xs = structure.atoms.map((atom) => atom.x); const ys = structure.atoms.map((atom) => atom.y)
  const minX = Math.min(...xs); const maxX = Math.max(...xs); const minY = Math.min(...ys); const maxY = Math.max(...ys)
  const scale = 180 / Math.max(maxX - minX, maxY - minY, 1)
  const point = (atom: { x: number; y: number }) => ({ x: 110 + (atom.x - (minX + maxX) / 2) * scale, y: 110 - (atom.y - (minY + maxY) / 2) * scale })
  const atoms = new Map(structure.atoms.map((atom) => [atom.id, atom]))
  return <div className="molecule-preview"><span className="panel-label">Atom-and-bond structure</span><svg viewBox="0 0 220 220" role="img" aria-label={`Molecular structure of ${structure.preferred_name || compoundId}`}>
    {structure.bonds.map((bond, index) => { const start = point(atoms.get(bond.source)!); const end = point(atoms.get(bond.target)!); return <line key={index} x1={start.x} y1={start.y} x2={end.x} y2={end.y} className={bond.aromatic ? 'aromatic-bond' : 'molecule-bond'} strokeWidth={Math.max(1.5, bond.order * 1.5)} /> })}
    {structure.atoms.map((atom) => { const position = point(atom); return <g key={atom.id}><circle cx={position.x} cy={position.y} r="10" className={`atom atom-${atom.symbol}`} /><text x={position.x} y={position.y + 4} textAnchor="middle">{atom.symbol}</text></g> })}
  </svg><p>{structure.molecular_formula || 'Formula unavailable'}{structure.molecular_weight ? ` · ${structure.molecular_weight.toFixed(2)} g/mol` : ''}</p><code>{structure.smiles}</code></div>
}

export default function LargeGraphExplorer() {
  const container = useRef<HTMLDivElement>(null)
  const [stats, setStats] = useState<LargeGraphStats | null>(null)
  const [overview, setOverview] = useState<LargeGraphOverview | null>(null)
  const [routeDisplay, setRouteDisplay] = useState<DisplayGraph | null>(null)
  const [display, setDisplay] = useState<DisplayGraph | null>(null)
  const [viewMode, setViewMode] = useState<'routes' | 'overview'>('routes')
  const [query, setQuery] = useState('')
  const [nodeType, setNodeType] = useState('')
  const [results, setResults] = useState<LargeGraphNode[]>([])
  const [selected, setSelected] = useState<LargeGraphNode | null>(null)
  const [selectedEdges, setSelectedEdges] = useState<LargeGraphEdge[]>([])
  const [direction, setDirection] = useState('both')
  const [depth, setDepth] = useState(1)
  const [statuses, setStatuses] = useState(['validated', 'unresolved'])
  const [pathStart, setPathStart] = useState('')
  const [pathEnd, setPathEnd] = useState('')
  const [message, setMessage] = useState('Loading graph projection…')
  const [error, setError] = useState('')

  useEffect(() => {
    fetchLargeGraphStats().then(setStats).catch((problem) => setError(problem instanceof Error ? problem.message : 'Large graph is unavailable.'))
  }, [])

  useEffect(() => {
    if (display || viewMode !== 'overview') return
    fetchLargeGraphOverview(nodeType, statuses, direction, depth).then((nextOverview) => {
      setOverview(nextOverview)
      const scope = nodeType ? `${pretty(nodeType)} · ${direction} · ${depth} hop${depth === 1 ? '' : 's'}` : 'all dimensions'
      setMessage(`Global overview · ${scope}`); setError('')
    }).catch((problem) => setError(problem instanceof Error ? problem.message : 'Large graph is unavailable.'))
  }, [nodeType, statuses, direction, depth, display, viewMode])

  useEffect(() => {
    if (display || viewMode !== 'routes') return
    fetchLargeRouteGraph(statuses).then((graph) => {
      setRouteDisplay(graph)
      setMessage(`Whole evidence route network · ${graph.nodes.length.toLocaleString()} materials · ${graph.edges.length.toLocaleString()} transformations`)
      setError('')
    }).catch((problem) => setError(problem instanceof Error ? problem.message : 'Route graph is unavailable.'))
  }, [statuses, display, viewMode])

  const overviewDisplay = useMemo<DisplayGraph | null>(() => {
    if (!overview) return null
    return {
      nodes: overview.nodes.map((node) => ({ node_id: `type:${node.id}`, node_type: node.id, label: `${node.label} · ${node.count.toLocaleString()}`, review_status: 'summary' })),
      edges: overview.edges.map((edge, index) => ({
        edge_id: `overview:${index}`, source_node_id: `type:${edge.source}`, target_node_id: `type:${edge.target}`,
        predicate: `${pretty(edge.predicate)} · ${edge.count.toLocaleString()}`, validation_status: edge.validation_status,
        review_status: 'summary',
      })),
    }
  }, [overview])
  const activeDisplay = display || (viewMode === 'routes' ? routeDisplay : overviewDisplay)

  useEffect(() => {
    if (!container.current || !activeDisplay) return
    const graph = new MultiGraph()
    const typeOrder = [...new Set(activeDisplay.nodes.map((node) => node.node_type))]
    for (const node of activeDisplay.nodes) {
      const typeIndex = Math.max(typeOrder.indexOf(node.node_type), 0)
      const angle = (typeIndex / Math.max(typeOrder.length, 1)) * Math.PI * 2 + ((hash(node.node_id) % 100) / 700)
      const radius = node.node_id === activeDisplay.selected ? 0 : 8 + (hash(node.node_id + ':r') % 100) / 8
      graph.addNode(node.node_id, {
        label: node.label, x: Math.cos(angle) * radius, y: Math.sin(angle) * radius,
        size: node.node_id === activeDisplay.selected ? 13 : node.node_id.startsWith('type:') ? 10 : 4,
        color: COLORS[node.node_type] || '#7f8883', nodeType: node.node_type,
      })
    }
    for (const edge of activeDisplay.edges) {
      if (!graph.hasNode(edge.source_node_id) || !graph.hasNode(edge.target_node_id)) continue
      graph.addEdgeWithKey(edge.edge_id, edge.source_node_id, edge.target_node_id, {
        color: STATUS_COLORS[edge.validation_status] || '#9aa29e', size: 0.6, label: edge.predicate,
      })
    }
    if (!activeDisplay.nodes.some((node) => node.node_id.startsWith('type:'))) {
      for (const node of graph.nodes()) {
        const degree = graph.degree(node)
        graph.setNodeAttribute(node, 'size', node === activeDisplay.selected ? 14 : Math.min(13, 2.5 + Math.sqrt(degree) * 1.45))
      }
    }
    if (viewMode === 'routes' && !activeDisplay.nodes.some((node) => node.node_id.startsWith('type:')) && graph.order > 1) {
      forceAtlas2.assign(graph, {
        iterations: graph.order <= 500 ? 100 : 30,
        settings: { ...forceAtlas2.inferSettings(graph), gravity: 0.08, scalingRatio: 12, slowDown: 2 },
      })
    }
    const renderer = new Sigma(graph, container.current, {
      renderEdgeLabels: false, labelDensity: 0.08, labelGridCellSize: 120,
      defaultNodeColor: '#7f8883', defaultEdgeColor: '#aeb5b1',
    })
    renderer.on('clickNode', ({ node }) => {
      if (node.startsWith('type:')) { setNodeType(node.slice(5)); setQuery(''); setDisplay(null); return }
      void openNode(node)
    })
    return () => renderer.kill()
  }, [activeDisplay])

  async function submitSearch(event: FormEvent) {
    event.preventDefault(); if (!query.trim()) return
    try { setResults((await searchLargeGraph(query, nodeType)).items); setError('') }
    catch (problem) { setError(problem instanceof Error ? problem.message : 'Search failed.') }
  }

  async function openNode(nodeId: string) {
    if (viewMode === 'routes' && routeDisplay) {
      const node = routeDisplay.nodes.find((item) => item.node_id === nodeId)
      setSelected(node || results.find((item) => item.node_id === nodeId) || null)
      const direct = routeDisplay.edges.filter((edge) => edge.source_node_id === nodeId || edge.target_node_id === nodeId)
      setSelectedEdges(direct); setResults([])
      setMessage(direct.length ? `${direct.length} direct route transformations` : 'No consumed/produced route connection is currently resolved for this compound')
      return
    }
    try {
      const graph: LargeGraphNeighborhood = await fetchLargeGraphNeighborhood(nodeId, depth, direction, statuses)
      setDisplay({ ...graph, selected: nodeId }); setSelected(graph.nodes.find((node) => node.node_id === nodeId) || null)
      setSelectedEdges(graph.edges.filter((edge) => edge.source_node_id === nodeId || edge.target_node_id === nodeId))
      setMessage(`${graph.nodes.length.toLocaleString()} nodes · ${graph.edges.length.toLocaleString()} edges${graph.truncated ? ' · bounded' : ''}`)
      setResults([]); setError('')
    } catch (problem) { setError(problem instanceof Error ? problem.message : 'Expansion failed.') }
  }

  async function runPath() {
    if (!pathStart || !pathEnd) return
    try {
      const path = await findLargeGraphPath(pathStart, pathEnd, statuses)
      if (!path.found) { setMessage(path.reason || 'No path found'); return }
      const nodes = (await Promise.all(path.nodes.map((node) => fetchLargeGraphNeighborhood(node, 1, 'both', statuses)))).flatMap((item) => item.nodes)
      setDisplay({ nodes: [...new Map(nodes.map((node) => [node.node_id, node])).values()], edges: path.edges, selected: pathStart })
      setMessage(`Path found · ${path.edges.length} edges`)
    } catch (problem) { setError(problem instanceof Error ? problem.message : 'Path search failed.') }
  }

  function toggleStatus(status: string) {
    setStatuses((current) => current.includes(status) ? (current.length > 1 ? current.filter((item) => item !== status) : current) : [...current, status])
  }

  return <section className="large-graph-explorer" id="large-graph">
    <div className="large-graph-heading"><div><span className="kicker">RXN2 multidimensional graph</span><h2>{stats?.node_count.toLocaleString() || '—'} nodes · {stats?.edge_count.toLocaleString() || '—'} edges</h2><p>Route map shows only evidence-backed molecule transformations. Dataset overview shows catalogue, patent and chemical dimensions.</p></div><div className="graph-view-switch"><button className={viewMode === 'routes' ? 'active' : ''} onClick={() => { setViewMode('routes'); setDisplay(null); setSelected(null) }}>Route map</button><button className={viewMode === 'overview' ? 'active' : ''} onClick={() => { setViewMode('overview'); setDisplay(null); setSelected(null) }}>Dataset overview</button></div></div>
    {error && <div className="alert error"><b>Graph error</b><span>{error}</span></div>}
    <div className="large-graph-toolbar">
      <form onSubmit={submitSearch}><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Drug, compound, InChIKey or patent" /><button>Search</button></form>
      <select disabled={viewMode === 'routes'} value={nodeType} onChange={(event) => { setNodeType(event.target.value); setDisplay(null); setSelected(null) }} aria-label="Node type"><option value="">All dimensions</option>{stats?.nodes_by_type.map((item) => <option key={item.node_type} value={item.node_type}>{pretty(item.node_type)} ({item.count.toLocaleString()})</option>)}</select>
      <select disabled={viewMode === 'routes'} value={direction} onChange={(event) => { setDirection(event.target.value); setDisplay(null); setSelected(null) }} aria-label="Direction"><option value="both">Both directions</option><option value="incoming">Upstream</option><option value="outgoing">Downstream</option></select>
      <select disabled={viewMode === 'routes'} value={depth} onChange={(event) => { setDepth(Number(event.target.value)); setDisplay(null); setSelected(null) }} aria-label="Depth"><option value={1}>1 hop</option><option value={2}>2 hops</option><option value={3}>3 hops</option></select>
    </div>
    <div className="large-graph-statuses">{['validated','unresolved','rejected'].map((status) => <label key={status}><input type="checkbox" checked={statuses.includes(status)} onChange={() => { toggleStatus(status); setDisplay(null); setSelected(null) }} /> {status}</label>)}</div>
    {results.length > 0 && <div className="graph-search-results">{results.map((node) => <button key={node.node_id} onClick={() => void openNode(node.node_id)}><b>{node.label}</b><small>{pretty(node.node_type)} · {node.node_id}</small></button>)}</div>}
    <div className="large-graph-stage"><div ref={container} className="sigma-host" /><aside>
      <span className="panel-label">Selection</span>
      {selected ? <><h3>{selected.label}</h3><p>{pretty(selected.node_type)} · {selected.review_status}</p>{selected.node_id.startsWith('compound:') && <MoleculePreview compoundId={selected.node_id.slice('compound:'.length)} />}<dl><dt>Node ID</dt><dd>{selected.node_id}</dd><dt>Direct relations</dt><dd>{selectedEdges.length}</dd></dl><div className="graph-path-actions"><button onClick={() => setPathStart(selected.node_id)}>Set path start</button><button onClick={() => setPathEnd(selected.node_id)}>Set path end</button></div>{!isHostedGraph && <><a href={`/api/graph/export?node_id=${encodeURIComponent(selected.node_id)}&depth=${depth}&format=graphml`}>Export GraphML</a><a href={`/api/graph/export?node_id=${encodeURIComponent(selected.node_id)}&depth=${depth}&format=jsonl`}>Export JSONL</a></>}{selectedEdges.slice(0, 8).map((edge) => <div className="evidence-edge" key={edge.edge_id}><b>{pretty(edge.predicate)}</b><span>{edge.validation_status}</span><small>{edge.evidence_span_id || edge.source_table}</small></div>)}</> : <p>Select a curated compound from search to see its real atom-and-bond structure, then inspect connected route evidence.</p>}
    </aside></div>
    <div className="graph-path-bar"><input value={pathStart} onChange={(event) => setPathStart(event.target.value)} placeholder="Path start node ID" /><span>→</span><input value={pathEnd} onChange={(event) => setPathEnd(event.target.value)} placeholder="Path end node ID" /><button onClick={() => void runPath()}>Find path</button></div>
    <div className="large-graph-message">{message} · provisional evidence remains review-gated</div>
  </section>
}
