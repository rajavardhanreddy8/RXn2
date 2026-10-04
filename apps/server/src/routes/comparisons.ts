import { Router, Request, Response } from 'express'
import mongoose from 'mongoose'
import { z } from 'zod'
import { Route, IRouteStep } from '../models/Route.js'
import { OptimizationRun, IRouteSnapshot } from '../models/OptimizationRun.js'
import { requireAuth, requireAdmin } from '../middleware/auth.js'
import { requireProjectOwnership } from '../middleware/ownership.js'
import { buildRouteSnapshot } from '../services/comparisonService.js'
import { fastApiClient, FastAPIServiceError } from '../services/fastapiClient.js'

export const comparisonsRouter = Router({ mergeParams: true })

comparisonsRouter.use(requireAuth)
comparisonsRouter.use(requireAdmin)
comparisonsRouter.use(requireProjectOwnership)

const CompareRequestSchema = z.object({
  routeIds: z.array(z.string()).refine(ids => new Set(ids).size === ids.length, 'Choose distinct routes').and(z.array(z.string()).min(2, 'At least 2 routes are required for comparison').max(10, 'A maximum of 10 routes can be compared at once')),

  baseCurrency: z.string().regex(/^[A-Z]{3}$/).default('USD'),
})

// POST /api/v1/projects/:projectId/comparisons
comparisonsRouter.post('/', async (req: Request, res: Response): Promise<void> => {
  const startedAt = new Date()
  const project = req.project!
  const projectId = project._id

  const result = CompareRequestSchema.safeParse(req.body)
  if (!result.success) {
    res.status(400).json({
      error: 'Validation failed',
      details: result.error.errors.map((e) => e.message),
    })
    return
  }

  const { routeIds, baseCurrency } = result.data

  try {
    const routeSnapshots: IRouteSnapshot[] = []
    for (const routeId of routeIds) {
      if (mongoose.Types.ObjectId.isValid(routeId)) {
        // Look up manual route from MongoDB
        const dbRoute = await Route.findOne({
          _id: routeId,
          projectId,
          ownerId: req.user!.id,
        }).lean()

        if (dbRoute) {
          const snapshot = buildRouteSnapshot(
            dbRoute._id.toString(),
            'manual',
            dbRoute.revision,
            dbRoute.steps,
            baseCurrency
          )
          routeSnapshots.push(snapshot)
        }
      } else {
        // Fetch the authoritative result; never trust client-supplied chemistry or prices.
        const genRoute = await fastApiClient.getRoute(routeId)
        const targetId = project.targetCompoundId || (await fastApiClient.resolveTarget(project.targetCompoundName || '')).target?.compound_id
        const massG = project.targetMassG * (project.targetMassUnit === 'kg' ? 1000 : project.targetMassUnit === 'mg' ? 0.001 : 1)
        if (genRoute.target_compound_id !== targetId || genRoute.target_mass_g !== massG || genRoute.base_currency !== baseCurrency) {
          res.status(400).json({ error: 'Generated route target, batch mass, and currency must match this project' })
          return
        }
        const convertedSteps: IRouteStep[] = genRoute.steps.map((s, idx) => ({
          position: idx + 1,
          name: s.reaction_name || s.transformation_key || `Step ${idx + 1}`,
          reactants: s.inputs.map((i) => i.compound_id),
          inputLabels: s.inputs.map((i) => i.preferred_name || i.compound_id),
          productLabel: s.product_compound_id,
          yieldPercent: s.yield_percent ?? null,
          durationHours: null,
          durationUnit: 'h',
          solvent: '',
          conditions: '',
          hazardNotes: '',
          evidenceRef: s.evidence?.publication_number || s.evidence?.label || '',
          dataSource: s.is_synthetic ? 'synthetic_fixture' : 'patent_evidence',
        }))

        const snapshot = buildRouteSnapshot(
          genRoute.route_id,
          'generated',
          1,
          convertedSteps,
          baseCurrency,
          genRoute.evaluation as unknown as Record<string, unknown>
        )
        routeSnapshots.push(snapshot)
      }
    }

    if (routeSnapshots.length !== routeIds.length) {
      // Record failed run if comparison was attempted
      await OptimizationRun.create({
        projectId,
        createdBy: req.user!.id,
        routeSnapshots: [],
        target: {
          compoundId: project.targetCompoundId,
          compoundName: project.targetCompoundName,
          targetMassG: project.targetMassG,
          targetMassUnit: project.targetMassUnit,
        },
        constraints: { baseCurrency },
        methodVersion: 'synthai-comparison-v2',
        dataVersion: 'local-evidence-unversioned',
        startedAt,
        endedAt: new Date(),
        status: 'failed',
        error: 'Could not resolve at least 2 valid routes for comparison',
      })

      res.status(400).json({
        error: 'Comparison requires at least 2 resolvable routes',
      })
      return
    }

    const endedAt = new Date()

    // Save immutable optimization run snapshot
    const run = await OptimizationRun.create({
      projectId,
      createdBy: req.user!.id,
      routeSnapshots,
      target: {
        compoundId: project.targetCompoundId,
        compoundName: project.targetCompoundName,
        targetMassG: project.targetMassG,
        targetMassUnit: project.targetMassUnit,
      },
      constraints: {
        baseCurrency,
      },
      methodVersion: 'synthai-comparison-v2',
      dataVersion: 'local-evidence-unversioned',
      startedAt,
      endedAt,
      status: 'completed',
    })

    res.status(201).json({
      comparison: {
        _id: run._id.toString(),
        projectId: run.projectId.toString(),
        createdBy: run.createdBy.toString(),
        routeSnapshots: run.routeSnapshots,
        target: run.target,
        constraints: run.constraints,
        methodVersion: run.methodVersion,
        dataVersion: run.dataVersion,
        startedAt: run.startedAt,
        endedAt: run.endedAt,
        status: run.status,
        createdAt: run.createdAt,
      },
    })
  } catch (error) {
    // Record failed run on unexpected exception
    await OptimizationRun.create({
      projectId,
      createdBy: req.user!.id,
      routeSnapshots: [],
      target: {
        compoundId: project.targetCompoundId,
        compoundName: project.targetCompoundName,
        targetMassG: project.targetMassG,
        targetMassUnit: project.targetMassUnit,
      },
      constraints: { baseCurrency },
      methodVersion: 'synthai-comparison-v2',
      dataVersion: 'local-evidence-unversioned',
      startedAt,
      endedAt: new Date(),
      status: 'failed',
      error: error instanceof Error ? error.message : String(error),
    }).catch(() => {})

    res.status(error instanceof FastAPIServiceError ? error.statusCode : 500).json({ error: error instanceof FastAPIServiceError ? error.message : 'Failed to complete route comparison run' })
  }
})

// GET /api/v1/projects/:projectId/comparisons
comparisonsRouter.get('/', async (req: Request, res: Response): Promise<void> => {
  const projectId = req.project!._id

  try {
    const runs = await OptimizationRun.find({ projectId })
      .sort({ createdAt: -1 })
      .lean()

    const formatted = runs.map((run) => ({
      _id: run._id.toString(),
      projectId: run.projectId.toString(),
      createdBy: run.createdBy.toString(),
      routeSnapshotsCount: run.routeSnapshots?.length || 0,
      target: run.target,
      methodVersion: run.methodVersion,
      status: run.status,
      error: run.error,
      createdAt: run.createdAt,
    }))

    res.json({ comparisons: formatted, total: formatted.length })
  } catch (error) {
    res.status(500).json({ error: 'Failed to retrieve comparison history' })
  }
})

// GET /api/v1/projects/:projectId/comparisons/:comparisonId
comparisonsRouter.get('/:comparisonId', async (req: Request, res: Response): Promise<void> => {
  const { comparisonId } = req.params
  const projectId = req.project!._id

  if (!comparisonId || !mongoose.Types.ObjectId.isValid(comparisonId)) {
    res.status(404).json({ error: 'Comparison run not found' })
    return
  }

  try {
    const run = await OptimizationRun.findOne({
      _id: comparisonId,
      projectId,
    }).lean()

    if (!run) {
      res.status(404).json({ error: 'Comparison run not found' })
      return
    }

    res.json({
      comparison: {
        _id: run._id.toString(),
        projectId: run.projectId.toString(),
        createdBy: run.createdBy.toString(),
        routeSnapshots: run.routeSnapshots,
        target: run.target,
        constraints: run.constraints,
        methodVersion: run.methodVersion,
        dataVersion: run.dataVersion,
        startedAt: run.startedAt,
        endedAt: run.endedAt,
        status: run.status,
        error: run.error,
        createdAt: run.createdAt,
      },
    })
  } catch (error) {
    res.status(500).json({ error: 'Failed to retrieve comparison snapshot' })
  }
})
