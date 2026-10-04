import { Router, Request, Response } from 'express'
import { z } from 'zod'
import { fastApiClient, FastAPIServiceError } from '../services/fastapiClient.js'
import { requireAuth } from '../middleware/auth.js'

export const chemistryRouter = Router()

// Target resolution can be used by authenticated users
chemistryRouter.use(requireAuth)

const ResolveTargetSchema = z.object({
  query: z.string().trim().min(1, 'Target query is required'),
  query_type: z.enum(['auto', 'name', 'smiles', 'inchi_key']).default('auto'),
})

const GenerateRoutesSchema = z.object({
  compound_id: z.string().trim().optional().nullable(),
  query: z.string().trim().optional().nullable(),
  target_mass_g: z.number().positive('Target mass must be positive').default(1000),
  base_currency: z.string().default('USD'),
  constraints: z
    .object({
      max_steps: z.number().int().min(1).max(12).default(6),
      max_routes: z.number().int().min(1).max(10).default(10),
      excluded_compound_ids: z.array(z.string()).default([]),
      excluded_hazard_codes: z.array(z.string()).default([]),
    })
    .default({}),
})

// POST /api/v1/targets/resolve
chemistryRouter.post('/targets/resolve', async (req: Request, res: Response): Promise<void> => {
  const result = ResolveTargetSchema.safeParse(req.body)
  if (!result.success) {
    res.status(400).json({
      error: 'Validation failed',
      details: result.error.errors.map((e) => e.message),
    })
    return
  }

  try {
    const data = await fastApiClient.resolveTarget(result.data.query, result.data.query_type)
    res.json(data)
  } catch (error: any) {
    const statusCode = error.statusCode || (error instanceof FastAPIServiceError ? error.statusCode : 502)
    res.status(statusCode).json({
      error: error.message || 'Failed to communicate with chemistry service',
      detail: error.detail,
    })
  }
})

// POST /api/v1/routes/generate
chemistryRouter.post('/routes/generate', async (req: Request, res: Response): Promise<void> => {
  const result = GenerateRoutesSchema.safeParse(req.body)
  if (!result.success) {
    res.status(400).json({
      error: 'Validation failed',
      details: result.error.errors.map((e) => e.message),
    })
    return
  }

  const { compound_id, query, target_mass_g, base_currency, constraints } = result.data

  if (!compound_id && !query) {
    res.status(400).json({ error: 'Either compound_id or query is required' })
    return
  }

  try {
    const data = await fastApiClient.generateRoutes(
      compound_id,
      query,
      target_mass_g,
      base_currency,
      constraints
    )
    res.json(data)
  } catch (error: any) {
    const statusCode = error.statusCode || (error instanceof FastAPIServiceError ? error.statusCode : 502)
    res.status(statusCode).json({
      error: error.message || 'Failed to communicate with chemistry service',
      detail: error.detail,
    })
  }
})
