import { Router, Request, Response } from 'express'
import mongoose from 'mongoose'
import { z } from 'zod'
import { Route, IRouteStep } from '../models/Route.js'
import { requireAuth } from '../middleware/auth.js'
import { requireProjectOwnership } from '../middleware/ownership.js'

export const routesRouter = Router({ mergeParams: true })

// Require authentication and project ownership for all route endpoints
routesRouter.use(requireAuth)
routesRouter.use(requireProjectOwnership)

const StepSchema = z.object({
  position: z.number().int('Step position must be an integer').min(1, 'Step position must be >= 1'),
  name: z.string().optional().default(''),
  reactants: z.array(z.string()).optional().default([]),
  inputLabels: z.array(z.string()).optional().default([]),
  productLabel: z.string().optional().default(''),
  yieldPercent: z
    .number()
    .min(0, 'Yield percentage cannot be negative')
    .max(100, 'Yield percentage cannot exceed 100')
    .nullable()
    .optional()
    .default(null),
  durationHours: z
    .number()
    .min(0, 'Duration cannot be negative')
    .nullable()
    .optional()
    .default(null),
  durationUnit: z.enum(['h', 'min', 'd']).default('h'),
  solvent: z.string().optional().default(''),
  conditions: z.string().optional().default(''),
  hazardNotes: z.string().optional().default(''),
  evidenceRef: z.string().optional().default(''),
  dataSource: z.string().optional().default('manual_entry'),
}).transform(step => ({ ...step, dataSource: 'manual_entry' }))

const RouteCreateSchema = z.object({
  origin: z.literal('manual').default('manual'),
  steps: z.array(StepSchema).optional().default([]),
})

const RouteUpdateSchema = z.object({
  origin: z.literal('manual').optional(),
  steps: z.array(StepSchema).optional(),
})

// POST /api/v1/projects/:projectId/routes
routesRouter.post('/', async (req: Request, res: Response): Promise<void> => {
  const result = RouteCreateSchema.safeParse(req.body)
  if (!result.success) {
    res.status(400).json({
      error: 'Validation failed',
      details: result.error.errors.map((e) => e.message),
    })
    return
  }

  const { origin, steps } = result.data
  const projectId = req.project!._id

  // Sort steps by position
  const sortedSteps = [...steps].sort((a, b) => a.position - b.position)

  try {
    const route = await Route.create({
      projectId,
      ownerId: req.project!.ownerId,
      origin,
      revision: 1,
      steps: sortedSteps,
    })

    res.status(201).json({
      route: {
        _id: route._id.toString(),
        projectId: route.projectId.toString(),
        origin: route.origin,
        revision: route.revision,
        steps: route.steps,
        createdAt: route.createdAt,
        updatedAt: route.updatedAt,
      },
    })
  } catch (error) {
    res.status(500).json({ error: 'Failed to create route' })
  }
})

// GET /api/v1/projects/:projectId/routes
routesRouter.get('/', async (req: Request, res: Response): Promise<void> => {
  const projectId = req.project!._id

  try {
    const routes = await Route.find({
      projectId,
      ownerId: req.project!.ownerId,
    })
      .sort({ createdAt: 1 })
      .lean()

    const formatted = routes.map((r) => ({
      _id: r._id.toString(),
      projectId: r.projectId.toString(),
      origin: r.origin,
      revision: r.revision,
      steps: r.steps,
      createdAt: r.createdAt,
      updatedAt: r.updatedAt,
    }))

    res.json({ routes: formatted, total: formatted.length })
  } catch (error) {
    res.status(500).json({ error: 'Failed to retrieve routes' })
  }
})

// GET /api/v1/projects/:projectId/routes/:routeId
routesRouter.get('/:routeId', async (req: Request, res: Response): Promise<void> => {
  const { routeId } = req.params
  const projectId = req.project!._id

  if (!routeId || !mongoose.Types.ObjectId.isValid(routeId)) {
    res.status(404).json({ error: 'Route not found' })
    return
  }

  try {
    const route = await Route.findOne({
      _id: routeId,
      projectId,
      ownerId: req.project!.ownerId,
    }).lean()

    if (!route) {
      res.status(404).json({ error: 'Route not found' })
      return
    }

    res.json({
      route: {
        _id: route._id.toString(),
        projectId: route.projectId.toString(),
        origin: route.origin,
        revision: route.revision,
        steps: route.steps,
        createdAt: route.createdAt,
        updatedAt: route.updatedAt,
      },
    })
  } catch (error) {
    res.status(500).json({ error: 'Failed to retrieve route' })
  }
})

// PATCH /api/v1/projects/:projectId/routes/:routeId
routesRouter.patch('/:routeId', async (req: Request, res: Response): Promise<void> => {
  const { routeId } = req.params
  const projectId = req.project!._id

  if (!routeId || !mongoose.Types.ObjectId.isValid(routeId)) {
    res.status(404).json({ error: 'Route not found' })
    return
  }

  const result = RouteUpdateSchema.safeParse(req.body)
  if (!result.success) {
    res.status(400).json({
      error: 'Validation failed',
      details: result.error.errors.map((e) => e.message),
    })
    return
  }

  try {
    const route = await Route.findOne({
      _id: routeId,
      projectId,
      ownerId: req.project!.ownerId,
    })

    if (!route) {
      res.status(404).json({ error: 'Route not found' })
      return
    }

    const { origin, steps } = result.data

    if (origin !== undefined) {
      route.origin = origin
    }

    if (steps !== undefined) {
      // Sort updated steps by position
      route.steps = [...steps].sort((a, b) => a.position - b.position) as IRouteStep[]
    }

    // Increment revision count
    route.revision += 1

    await route.save()

    res.json({
      route: {
        _id: route._id.toString(),
        projectId: route.projectId.toString(),
        origin: route.origin,
        revision: route.revision,
        steps: route.steps,
        createdAt: route.createdAt,
        updatedAt: route.updatedAt,
      },
    })
  } catch (error) {
    res.status(500).json({ error: 'Failed to update route' })
  }
})

// DELETE /api/v1/projects/:projectId/routes/:routeId
routesRouter.delete('/:routeId', async (req: Request, res: Response): Promise<void> => {
  const { routeId } = req.params
  const projectId = req.project!._id

  if (!routeId || !mongoose.Types.ObjectId.isValid(routeId)) {
    res.status(404).json({ error: 'Route not found' })
    return
  }

  try {
    const deleted = await Route.findOneAndDelete({
      _id: routeId,
      projectId,
      ownerId: req.project!.ownerId,
    })

    if (!deleted) {
      res.status(404).json({ error: 'Route not found' })
      return
    }

    res.json({
      message: 'Route deleted successfully',
      deletedRouteId: routeId,
    })
  } catch (error) {
    res.status(500).json({ error: 'Failed to delete route' })
  }
})
