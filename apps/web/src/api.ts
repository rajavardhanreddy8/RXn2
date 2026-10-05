import type {
  AutomationStatus,
  CoverageResponse,
  CoverageStatus,
  GenerateResponse,
  Graph,
  ReviewQueueResponse,
  User,
  Project,
  SavedRoute,
  SavedStep,
  MassUnit,
  OptimizationRun,
} from './types'

const hostedGraphEndpoint = import.meta.env.VITE_RXN2_HOSTED_API?.replace(/\/$/, '')
const hostedProjectionEndpoint = import.meta.env.VITE_RXN2_FULL_PROJECTION_API?.replace(/\/$/, '')
const hostedWorkspaceEndpoint = import.meta.env.VITE_EXPRESS_API_URL?.replace(/\/$/, '')
const hostedPythonEndpoint = import.meta.env.VITE_PYTHON_API_URL?.replace(/\/$/, '')

export const isHostedGraph = Boolean(hostedGraphEndpoint)

function hostedRequestUrl(localUrl: string) {
  if (localUrl.startsWith('/api/v1') && hostedWorkspaceEndpoint) {
    return `${hostedWorkspaceEndpoint}${localUrl}`
  }
  if (localUrl.startsWith('/api/') && hostedPythonEndpoint) {
    return `${hostedPythonEndpoint}${localUrl}`
  }
  if (!hostedGraphEndpoint) return localUrl
  const parsed = new URL(localUrl, window.location.origin)
  const parameters = new URLSearchParams(parsed.search)
  const operation = (() => {
    if (parsed.pathname === '/api/graph/stats') return 'stats'
    if (parsed.pathname === '/api/graph/overview') return 'overview'
    if (parsed.pathname === '/api/graph/routes') return 'routes'
    if (parsed.pathname === '/api/graph/projection') return 'projection'
    if (parsed.pathname === '/api/graph/search') return 'search'
    if (parsed.pathname.startsWith('/api/chemistry/structure/')) {
      parameters.set('compound_id', decodeURIComponent(parsed.pathname.split('/').at(-1) || ''))
      return 'structure'
    }
    if (parsed.pathname.startsWith('/api/graph/neighborhood/')) {
      parameters.set('node_id', decodeURIComponent(parsed.pathname.split('/').at(-1) || ''))
      return 'neighborhood'
    }
    if (parsed.pathname === '/api/graph/path') return 'path'
    return ''
  })()
  if (!operation) return localUrl
  parameters.set('op', operation)
  return `${hostedGraphEndpoint}?${parameters}`
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(hostedRequestUrl(url), {
    credentials: 'include',
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  const contentType = response.headers.get('content-type') || ''
  const body = contentType.includes('application/json') ? await response.json() : await response.text()
  if (!response.ok) {
    if (typeof body === 'string') {
      if (url.startsWith('/api/v1') && !hostedWorkspaceEndpoint && window.location.hostname.endsWith('chatgpt.site')) {
        throw new Error('Workspace sign-in is not configured for this Site yet.')
      }
      throw new Error('The requested service is unavailable.')
    }
    const message =
      typeof body.error === 'string'
        ? body.error
        : typeof body.detail === 'string'
        ? body.detail
        : Array.isArray(body.details)
        ? body.details.join(', ')
        : JSON.stringify(body.error || body.detail || body)
    throw new Error(message)
  }
  if (typeof body === 'string') {
    throw new Error('The service returned an unexpected response.')
  }
  return body as T
}

export async function resolveTarget(query: string) {
  return request<{
    resolved: boolean
    target?: { compound_id: string; preferred_name: string }
    message?: string
    reviewed_producing_reactions?: number
  }>('/api/v1/targets/resolve', {
    method: 'POST',
    body: JSON.stringify({ query, query_type: 'auto' }),
  })
}

export async function generateRoutes(compoundId: string, targetMassG: number, maxSteps: number) {
  return request<GenerateResponse>('/api/v1/routes/generate', {
    method: 'POST',
    body: JSON.stringify({
      compound_id: compoundId,
      target_mass_g: targetMassG,
      base_currency: 'USD',
      constraints: { max_steps: maxSteps, max_routes: 10 },
    }),
  })
}

export async function fetchGraph(compoundId: string) {
  return fetchGraphNode(`compound:${compoundId}`)
}

export async function fetchGraphNode(nodeId: string) {
  return request<Graph>(`/api/graph/neighbors/${encodeURIComponent(nodeId)}?direction=both`)
}

export async function fetchCoverage(query = '', status: CoverageStatus | '' = '') {
  const parameters = new URLSearchParams({ limit: '100' })
  if (query.trim()) parameters.set('query', query.trim())
  if (status) parameters.set('status', status)
  return request<CoverageResponse>(`/api/catalogue/coverage?${parameters}`)
}

export async function fetchAutomationStatus() {
  return request<AutomationStatus>('/api/automation/status')
}

export async function fetchProvisionalGraph(limit = 1) {
  return request<{ nodes?: unknown[]; edges?: unknown[]; provisional_reaction_count?: number; validation_counts?: Record<string, number> }>(`/api/graph/provisional?limit=${limit}`)
}

export async function fetchLargeGraphStats() {
  return request<LargeGraphStats>('/api/graph/stats')
}

export async function fetchLargeGraphOverview(nodeType = '', statuses: string[] = ['validated', 'unresolved', 'rejected'], direction = 'both', depth = 1) {
  const parameters = new URLSearchParams({ validation_statuses: statuses.join(','), direction, depth: String(depth) })
  if (nodeType) parameters.set('node_type', nodeType)
  return request<LargeGraphOverview>(`/api/graph/overview?${parameters}`)
}

export async function searchLargeGraph(query: string, nodeType = '') {
  const parameters = new URLSearchParams({ query, limit: '30' })
  if (nodeType) parameters.set('node_type', nodeType)
  return request<{ items: LargeGraphNode[] }>(`/api/graph/search?${parameters}`)
}

export async function fetchLargeGraphNeighborhood(nodeId: string, depth: number, direction: string, statuses: string[]) {
  const parameters = new URLSearchParams({
    depth: String(depth), node_limit: '2000', edge_limit: '5000', direction,
    validation_statuses: statuses.join(','),
  })
  return request<LargeGraphNeighborhood>(`/api/graph/neighborhood/${encodeURIComponent(nodeId)}?${parameters}`)
}

export async function fetchLargeRouteGraph(statuses: string[], processLayer = 'core') {
  const parameters = new URLSearchParams({ validation_statuses: statuses.join(','), process_layer: processLayer })
  return request<LargeGraphNeighborhood>(`/api/graph/routes?${parameters}`)
}

export type ProjectionPage<T> = {
  kind: 'nodes' | 'edges'
  offset: number
  limit: number
  total: number
  items: T[]
}

export async function fetchFullProjectionPage<T extends LargeGraphNode | LargeGraphEdge>(
  kind: 'nodes' | 'edges', offset: number, statuses: string[], limit = 1000,
) {
  const parameters = new URLSearchParams({
    kind, offset: String(offset), limit: String(limit), validation_statuses: statuses.join(','),
  })
  if (hostedProjectionEndpoint) {
    const response = await fetch(`${hostedProjectionEndpoint}?${parameters}`)
    const body = await response.json()
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Full graph request failed')
    return body as ProjectionPage<T>
  }
  return request<ProjectionPage<T>>(`/api/graph/projection?${parameters}`)
}

export async function findLargeGraphPath(source: string, target: string, statuses: string[]) {
  const parameters = new URLSearchParams({ source, target, max_depth: '6', validation_statuses: statuses.join(',') })
  return request<{ found: boolean; nodes: string[]; edges: LargeGraphEdge[]; reason?: string }>(`/api/graph/path?${parameters}`)
}

export async function fetchMoleculeStructure(compoundId: string) {
  return request<MoleculeStructure>(`/api/chemistry/structure/${encodeURIComponent(compoundId)}`)
}

export type LargeGraphNode = {
  node_id: string
  node_type: string
  label: string
  review_status: string
  source_table?: string
  record_id?: string
  properties_json?: string
}

export type LargeGraphEdge = {
  edge_id: string
  source_node_id: string
  target_node_id: string
  predicate: string
  validation_status: string
  review_status: string
  confidence?: number | null
  evidence_span_id?: string | null
  source_table?: string
  source_record_id?: string
  properties_json?: string
}

export type LargeGraphStats = {
  node_count: number
  edge_count: number
  nodes_by_type: Array<{ node_type: string; count: number }>
  edges_by_type: Array<{ predicate: string; validation_status: string; review_status: string; count: number }>
}

export type LargeGraphOverview = {
  nodes: Array<{ id: string; label: string; count: number }>
  edges: Array<{ source: string; target: string; predicate: string; validation_status: string; count: number }>
}

export type MoleculeStructure = {
  compound_id: string
  preferred_name?: string | null
  molecular_formula?: string | null
  molecular_weight?: number | null
  inchi_key?: string | null
  smiles: string
  atoms: Array<{ id: number; symbol: string; atomic_number: number; x: number; y: number; aromatic: boolean; formal_charge: number; implicit_hydrogens: number }>
  bonds: Array<{ source: number; target: number; order: number; aromatic: boolean }>
}

export type LargeGraphNeighborhood = {
  selected_node: string
  nodes: LargeGraphNode[]
  edges: LargeGraphEdge[]
  truncated: boolean
}

export async function fetchReviewQueue() {
  return request<ReviewQueueResponse>('/api/review-queue')
}

// ----------------------------------------------------
// Phase 1 Auth API Functions
// ----------------------------------------------------

export async function registerUser(email: string, password: string, name?: string) {
  return request<{ user: User; token: string }>('/api/v1/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, password, name }),
  })
}

export async function loginUser(email: string, password: string) {
  return request<{ user: User; token: string }>('/api/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

export async function logoutUser() {
  return request<{ message: string }>('/api/v1/auth/logout', {
    method: 'POST',
  })
}

export async function fetchCurrentUser() {
  return request<{ user: User }>('/api/v1/auth/me')
}

// ----------------------------------------------------
// Phase 2 Project Workspace API Functions
// ----------------------------------------------------

export async function fetchProjects() {
  return request<{ projects: Project[]; total: number }>('/api/v1/projects')
}

export async function fetchProject(projectId: string) {
  return request<{ project: Project }>(`/api/v1/projects/${projectId}`)
}

export async function createProject(data: {
  title: string
  targetCompoundId?: string | null
  targetCompoundName?: string | null
  targetMassG: number
  targetMassUnit?: MassUnit
  notes?: string
}) {
  return request<{ project: Project }>('/api/v1/projects', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updateProject(
  projectId: string,
  data: Partial<{
    title: string
    targetCompoundId?: string | null
    targetCompoundName?: string | null
    targetMassG: number
    targetMassUnit: MassUnit
    notes: string
  }>
) {
  return request<{ project: Project }>(`/api/v1/projects/${projectId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

export async function deleteProject(projectId: string) {
  return request<{ message: string; deletedProjectId: string; deletedRoutesCount: number }>(
    `/api/v1/projects/${projectId}`,
    { method: 'DELETE' }
  )
}

// ----------------------------------------------------
// Phase 2 Route Management API Functions
// ----------------------------------------------------

export async function fetchProjectRoutes(projectId: string) {
  return request<{ routes: SavedRoute[]; total: number }>(`/api/v1/projects/${projectId}/routes`)
}

export async function fetchProjectRoute(projectId: string, routeId: string) {
  return request<{ route: SavedRoute }>(`/api/v1/projects/${projectId}/routes/${routeId}`)
}

export async function createProjectRoute(
  projectId: string,
  data: {
    origin?: 'manual' | 'generated'
    steps: SavedStep[]
  }
) {
  return request<{ route: SavedRoute }>(`/api/v1/projects/${projectId}/routes`, {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updateProjectRoute(
  projectId: string,
  routeId: string,
  data: {
    origin?: 'manual' | 'generated'
    steps?: SavedStep[]
  }
) {
  return request<{ route: SavedRoute }>(`/api/v1/projects/${projectId}/routes/${routeId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

export async function deleteProjectRoute(projectId: string, routeId: string) {
  return request<{ message: string; deletedRouteId: string }>(
    `/api/v1/projects/${projectId}/routes/${routeId}`,
    { method: 'DELETE' }
  )
}

// ----------------------------------------------------
// Phase 3 Route Comparison API Functions
// ----------------------------------------------------

export async function createComparison(
  projectId: string,
  data: {
    routeIds: string[]
    generatedRoutes?: unknown[]
    baseCurrency?: string
  }
) {
  return request<{ comparison: OptimizationRun }>(`/api/v1/projects/${projectId}/comparisons`, {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function fetchProjectComparisons(projectId: string) {
  return request<{
    comparisons: Array<{
      _id: string
      projectId: string
      createdBy: string
      routeSnapshotsCount: number
      target: { compoundName?: string; targetMassG: number }
      methodVersion: string
      status: 'completed' | 'failed'
      error?: string | null
      createdAt: string
    }>
    total: number
  }>(`/api/v1/projects/${projectId}/comparisons`)
}

export async function fetchProjectComparison(projectId: string, comparisonId: string) {
  return request<{ comparison: OptimizationRun }>(
    `/api/v1/projects/${projectId}/comparisons/${comparisonId}`
  )
}
