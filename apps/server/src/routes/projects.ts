import { Router, Request, Response } from 'express'
import { z } from 'zod'
import { Project } from '../models/Project.js'
import { OptimizationRun } from '../models/OptimizationRun.js'
import { Route } from '../models/Route.js'
import { requireAuth } from '../middleware/auth.js'
import { requireProjectOwnership } from '../middleware/ownership.js'

export const projectsRouter = Router()

// All project endpoints require authentication
projectsRouter.use(requireAuth)

const ProjectCreateSchema = z.object({
  title: z.string().trim().min(1, 'Title cannot be empty'),
  targetCompoundId: z.string().trim().optional().nullable(),
  targetCompoundName: z.string().trim().optional().nullable(),
  targetMassG: z.number().positive('Target mass must be positive'),
  targetMassUnit: z.enum(['g', 'mg', 'kg']).default('g'),
  notes: z.string().optional().default(''),
})

const ProjectUpdateSchema = z.object({
  title: z.string().trim().min(1, 'Title cannot be empty').optional(),
  targetCompoundId: z.string().trim().optional().nullable(),
  targetCompoundName: z.string().trim().optional().nullable(),
  targetMassG: z.number().positive('Target mass must be positive').optional(),
  targetMassUnit: z.enum(['g', 'mg', 'kg']).optional(),
  notes: z.string().optional(),
})

// POST /api/v1/projects
projectsRouter.post('/', async (req: Request, res: Response): Promise<void> => {
  const result = ProjectCreateSchema.safeParse(req.body)
  if (!result.success) {
    res.status(400).json({
      error: 'Validation failed',
      details: result.error.errors.map((e) => e.message),
    })
    return
  }

  const { title, targetCompoundId, targetCompoundName, targetMassG, targetMassUnit, notes } =
    result.data

  try {
    const project = await Project.create({
      ownerId: req.user!.id,
      title,
      targetCompoundId: targetCompoundId || null,
      targetCompoundName: targetCompoundName || null,
      targetMassG,
      targetMassUnit,
      notes: notes || '',
    })

    res.status(201).json({
      project: {
        _id: project._id.toString(),
        ownerId: project.ownerId.toString(),
        title: project.title,
        targetCompoundId: project.targetCompoundId,
        targetCompoundName: project.targetCompoundName,
        targetMassG: project.targetMassG,
        targetMassUnit: project.targetMassUnit,
        notes: project.notes,
        createdAt: project.createdAt,
        updatedAt: project.updatedAt,
      },
    })
  } catch (error) {
    res.status(500).json({ error: 'Failed to create project' })
  }
})

// GET /api/v1/projects
projectsRouter.get('/', async (req: Request, res: Response): Promise<void> => {
  try {
    const projects = await Project.find(req.user!.role === 'admin' ? {} : { ownerId: req.user!.id })
      .sort({ createdAt: -1 })
      .lean()

    // Include route count for each project
    const projectIds = projects.map((p) => p._id)
    const routeCounts = await Route.aggregate([
      { $match: { projectId: { $in: projectIds } } },
      { $group: { _id: '$projectId', count: { $sum: 1 } } },
    ])

    const countMap = new Map(routeCounts.map((r) => [r._id.toString(), r.count]))

    const formatted = projects.map((p) => ({
      _id: p._id.toString(),
      ownerId: p.ownerId.toString(),
      title: p.title,
      targetCompoundId: p.targetCompoundId,
      targetCompoundName: p.targetCompoundName,
      targetMassG: p.targetMassG,
      targetMassUnit: p.targetMassUnit,
      notes: p.notes,
      routeCount: countMap.get(p._id.toString()) || 0,
      createdAt: p.createdAt,
      updatedAt: p.updatedAt,
    }))

    res.json({ projects: formatted, total: formatted.length })
  } catch (error) {
    res.status(500).json({ error: 'Failed to retrieve projects' })
  }
})

// GET /api/v1/projects/:projectId
projectsRouter.get(
  '/:projectId',
  requireProjectOwnership,
  async (req: Request, res: Response): Promise<void> => {
    const p = req.project!
    res.json({
      project: {
        _id: p._id.toString(),
        ownerId: p.ownerId.toString(),
        title: p.title,
        targetCompoundId: p.targetCompoundId,
        targetCompoundName: p.targetCompoundName,
        targetMassG: p.targetMassG,
        targetMassUnit: p.targetMassUnit,
        notes: p.notes,
        createdAt: p.createdAt,
        updatedAt: p.updatedAt,
      },
    })
  }
)

// PATCH /api/v1/projects/:projectId
projectsRouter.patch(
  '/:projectId',
  requireProjectOwnership,
  async (req: Request, res: Response): Promise<void> => {
    const result = ProjectUpdateSchema.safeParse(req.body)
    if (!result.success) {
      res.status(400).json({
        error: 'Validation failed',
        details: result.error.errors.map((e) => e.message),
      })
      return
    }

    // Ignore or reject any client attempt to change ownerId or _id
    const updates = result.data
    const p = req.project!

    if (updates.title !== undefined) p.title = updates.title
    if (updates.targetCompoundId !== undefined) p.targetCompoundId = updates.targetCompoundId || undefined
    if (updates.targetCompoundName !== undefined) p.targetCompoundName = updates.targetCompoundName || undefined
    if (updates.targetMassG !== undefined) p.targetMassG = updates.targetMassG
    if (updates.targetMassUnit !== undefined) p.targetMassUnit = updates.targetMassUnit
    if (updates.notes !== undefined) p.notes = updates.notes

    try {
      await p.save()
      res.json({
        project: {
          _id: p._id.toString(),
          ownerId: p.ownerId.toString(),
          title: p.title,
          targetCompoundId: p.targetCompoundId,
          targetCompoundName: p.targetCompoundName,
          targetMassG: p.targetMassG,
          targetMassUnit: p.targetMassUnit,
          notes: p.notes,
          createdAt: p.createdAt,
          updatedAt: p.updatedAt,
        },
      })
    } catch (error) {
      res.status(500).json({ error: 'Failed to update project' })
    }
  }
)

// DELETE /api/v1/projects/:projectId
projectsRouter.delete(
  '/:projectId',
  requireProjectOwnership,
  async (req: Request, res: Response): Promise<void> => {
    const projectId = req.project!._id

    try {
      // Cascade delete policy: remove the project and all child routes
      await OptimizationRun.deleteMany({ projectId })
      const routeDeleteResult = await Route.deleteMany({ projectId })
      await Project.deleteOne({ _id: projectId })

      res.json({
        message: 'Project and associated routes deleted successfully',
        deletedProjectId: projectId.toString(),
        deletedRoutesCount: routeDeleteResult.deletedCount,
      })
    } catch (error) {
      res.status(500).json({ error: 'Failed to delete project' })
    }
  }
)
