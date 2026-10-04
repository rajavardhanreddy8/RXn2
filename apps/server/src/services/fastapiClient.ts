export interface FastAPIError extends Error {
  statusCode: number
  detail?: string
}

export class FastAPIServiceError extends Error implements FastAPIError {
  statusCode: number
  detail?: string

  constructor(message: string, statusCode: number = 502, detail?: string) {
    super(message)
    this.name = 'FastAPIServiceError'
    this.statusCode = statusCode
    this.detail = detail
  }
}

export interface TargetResolveResult {
  resolved: boolean
  target?: {
    compound_id: string
    preferred_name: string
    smiles?: string
    inchi_key?: string | null
    standardized_smiles?: string
    molecular_formula?: string
    molecular_weight?: number
  }
  reviewed_producing_reactions?: number
  coverage?: string
  message?: string
}

export interface FastAPIRouteStep {
  reaction_id: string
  reaction_name: string
  transformation_key: string
  product_compound_id: string
  yield_percent: number | null
  demonstrated_scale_g: number | null
  confidence: number
  is_synthetic: boolean
  evidence: {
    publication_number: string | null
    source_url: string | null
    evidence_status: string | null
    label: string
  }
  inputs: Array<{
    compound_id: string
    role: string
    stoichiometry: number | null
    preferred_name: string
    molecular_weight?: number
  }>
}

export interface FastAPIRouteEvaluation {
  actual_material_cost: number | null
  actual_cost_label: string
  actual_cost_coverage: number
  relative_cost_index: number
  feasibility_score: number
  rank_tier: string
  rank_score: number | null
  currency: string
  quote_lines: Array<{
    compound_id: string
    quote_id: string
    supplier_id: string
    required_mass_g: number
    pack_size_g: number
    packs: number
    package_cost: number
    continuous_cost: number
    currency: string
    observed_at: string
    source_url: string | null
  }>
  unpriced_materials: Array<{ compound_id: string; required_mass_g: number }>
  leaf_requirements_g: Record<string, number>
  warnings: string[]
  scorer_version: string
}

export interface FastAPIRoute {
  route_id: string
  target_compound_id: string
  step_count: number
  steps: FastAPIRouteStep[]
  algorithm_version: string
  evaluation: FastAPIRouteEvaluation
  rank?: number
}

export interface RouteGenerateResult {
  target: {
    compound_id: string
    preferred_name: string
    smiles?: string
    molecular_weight?: number
  }
  target_mass_g?: number
  base_currency?: string
  routes: FastAPIRoute[]
  coverage_gap: boolean
  message?: string
  disclaimer?: string
}

export class FastAPIClient {
  private baseUrl: string
  private timeoutMs: number

  constructor(
    baseUrl: string = process.env.FASTAPI_URL || 'http://127.0.0.1:8000',
    timeoutMs: number = parseInt(process.env.FASTAPI_TIMEOUT_MS || '5000', 10)
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, '')
    this.timeoutMs = timeoutMs
  }

  private async fetchWithTimeout(endpoint: string, options: RequestInit = {}): Promise<Response> {
    const controller = new AbortController()
    const id = setTimeout(() => controller.abort(), this.timeoutMs)

    try {
      const response = await fetch(`${this.baseUrl}${endpoint}`, {
        ...options,
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
          ...options.headers,
        },
      })
      return response
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        throw new FastAPIServiceError(
          `FastAPI service request timed out after ${this.timeoutMs}ms`,
          504
        )
      }
      throw new FastAPIServiceError(
        'FastAPI chemistry service is currently unavailable',
        502,
        err instanceof Error ? err.message : String(err)
      )
    } finally {
      clearTimeout(id)
    }
  }

  async resolveTarget(query: string, queryType: string = 'auto'): Promise<TargetResolveResult> {
    const res = await this.fetchWithTimeout('/api/targets/resolve', {
      method: 'POST',
      body: JSON.stringify({ query, query_type: queryType }),
    })

    const body = await res.json().catch(() => ({}))

    if (!res.ok) {
      throw new FastAPIServiceError(
        body.detail || 'Target resolution failed',
        res.status,
        body.detail
      )
    }

    return body as TargetResolveResult
  }

  async generateRoutes(
    compoundId?: string | null,
    query?: string | null,
    targetMassG: number = 1000,
    baseCurrency: string = 'USD',
    constraints: {
      max_steps?: number
      max_routes?: number
      excluded_compound_ids?: string[]
      excluded_hazard_codes?: string[]
    } = {}
  ): Promise<RouteGenerateResult> {
    const payload: Record<string, unknown> = {
      target_mass_g: targetMassG,
      base_currency: baseCurrency,
      constraints: {
        max_steps: constraints.max_steps || 6,
        max_routes: constraints.max_routes || 10,
        excluded_compound_ids: constraints.excluded_compound_ids || [],
        excluded_hazard_codes: constraints.excluded_hazard_codes || [],
      },
    }

    if (compoundId) {
      payload.compound_id = compoundId
    } else if (query) {
      payload.query = query
    } else {
      throw new FastAPIServiceError('Either compound_id or query is required', 400)
    }

    const res = await this.fetchWithTimeout('/api/routes/generate', {
      method: 'POST',
      body: JSON.stringify(payload),
    })

    const body = await res.json().catch(() => ({}))

    if (!res.ok) {
      if (res.status === 404) {
        // Target not found in reviewed graph
        return {
          target: { compound_id: compoundId || query || '', preferred_name: query || compoundId || '' },
          routes: [],
          coverage_gap: true,
          message: body.detail || 'Target is not present in the reviewed local graph.',
        }
      }
      throw new FastAPIServiceError(
        body.detail || 'Route generation failed',
        res.status,
        body.detail
      )
    }

    return body as RouteGenerateResult
  }

  async getRoute(routeId: string): Promise<FastAPIRoute & { target_mass_g: number; base_currency: string }> {
    const response = await this.fetchWithTimeout(`/api/routes/${encodeURIComponent(routeId)}`)
    if (!response.ok) throw new FastAPIServiceError('Generated route unavailable', response.status)
    return response.json()
  }

  async compareRoutes(routeIds: string[]): Promise<{ routes: FastAPIRoute[]; comparable: boolean }> {
    if (!routeIds || routeIds.length < 2 || routeIds.length > 10) {
      throw new FastAPIServiceError('Route comparison requires between 2 and 10 route IDs', 400)
    }

    const res = await this.fetchWithTimeout('/api/routes/compare', {
      method: 'POST',
      body: JSON.stringify({ route_ids: routeIds }),
    })

    const body = await res.json().catch(() => ({}))

    if (!res.ok) {
      throw new FastAPIServiceError(
        body.detail || 'FastAPI route comparison failed',
        res.status,
        body.detail
      )
    }

    return body as { routes: FastAPIRoute[]; comparable: boolean }
  }
}

export const fastApiClient = new FastAPIClient()
