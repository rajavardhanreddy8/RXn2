import type { AutomationStatus, CoverageResponse, CoverageStatus, GenerateResponse, Graph } from './types'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  const body = await response.json()
  if (!response.ok) {
    const message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail || body)
    throw new Error(message)
  }
  return body as T
}

export async function resolveTarget(query: string) {
  return request<{
    resolved: boolean
    target?: { compound_id: string; preferred_name: string }
    message?: string
    reviewed_producing_reactions?: number
  }>('/api/targets/resolve', {
    method: 'POST',
    body: JSON.stringify({ query, query_type: 'auto' }),
  })
}

export async function generateRoutes(compoundId: string, targetMassG: number, maxSteps: number) {
  return request<GenerateResponse>('/api/routes/generate', {
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
