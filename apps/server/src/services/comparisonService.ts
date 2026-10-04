import { IRouteStep, RouteOrigin } from '../models/Route.js'
import {
  IComparisonRouteMetrics,
  IRouteMetric,
  IRouteSnapshot,
} from '../models/OptimizationRun.js'

export function normalizeRouteMetrics(
  routeId: string,
  origin: RouteOrigin,
  steps: IRouteStep[],
  baseCurrency: string = 'USD',
  rawEvaluation?: Record<string, unknown> | null
): IComparisonRouteMetrics {
  // 1. Step Count Metric
  const stepCountMetric: IRouteMetric = {
    value: steps.length,
    unit: 'steps',
    status: 'complete',
    origin: origin === 'generated' ? 'calculated' : 'entered',
    warnings: [],
  }

  // 2. Yield Metric
  let yieldMetric: IRouteMetric
  if (steps.length === 0) {
    yieldMetric = {
      value: null,
      unit: '%',
      status: 'unknown',
      origin: 'entered',
      warnings: ['No reaction steps present in route.'],
    }
  } else {
    const missingYieldSteps = steps.filter(
      (s) => s.yieldPercent === null || s.yieldPercent === undefined
    )
    const validYields = steps
      .map((s) => s.yieldPercent)
      .filter((y): y is number => y !== null && y !== undefined)

    if (missingYieldSteps.length === 0) {
      // Complete yield info across ALL steps -> calculate cumulative yield
      const fractionalProduct = validYields.reduce((acc, y) => acc * (y / 100), 1)
      const overallYield = Math.round(fractionalProduct * 10000) / 100 // e.g. 76.44%

      yieldMetric = {
        value: overallYield,
        unit: '%',
        status: 'complete',
        origin: steps.length === 1 ? (origin === 'manual' ? 'entered' : 'reported') : 'calculated',
        warnings: steps.length > 1 ? ['Product of step yields assumes a linear sequential route.'] : [],
      }
    } else if (validYields.length > 0) {
      // Partial yields exist
      const stepPositions = missingYieldSteps.map((s) => s.position).join(', ')
      yieldMetric = {
        value: null,
        unit: '%',
        status: 'partial',
        origin: 'entered',
        warnings: [
          `Cumulative yield unknown: missing yield data for step(s) ${stepPositions}. (Missing values are never substituted with 0).`,
        ],
      }
    } else {
      // Zero yields entered
      yieldMetric = {
        value: null,
        unit: '%',
        status: 'unknown',
        origin: 'entered',
        warnings: ['No step yield data entered.'],
      }
    }
  }

  // 3. Duration Metric
  if (origin === 'generated' && steps.length > 1) {
    yieldMetric = { value: null, unit: '%', status: 'unknown', origin: 'calculated', warnings: ['Overall yield not calculated: generated routes may contain convergent branches. Inspect the reported step yields.'] }
  }
  let durationMetric: IRouteMetric
  if (steps.length === 0) {
    durationMetric = {
      value: null,
      unit: 'h',
      status: 'unknown',
      origin: 'entered',
      warnings: ['No steps present.'],
    }
  } else {
    const missingDurationSteps = steps.filter(
      (s) => s.durationHours === null || s.durationHours === undefined
    )
    const stepsWithDuration = steps.filter(
      (s) => s.durationHours !== null && s.durationHours !== undefined
    )

    if (missingDurationSteps.length === 0) {
      // All steps have duration -> convert to hours and sum
      const totalHours = steps.reduce((acc, s) => {
        const val = s.durationHours || 0
        if (s.durationUnit === 'min') return acc + val / 60
        if (s.durationUnit === 'd') return acc + val * 24
        return acc + val
      }, 0)

      durationMetric = {
        value: Math.round(totalHours * 100) / 100,
        unit: 'h',
        status: 'complete',
        origin: steps.length === 1 ? (origin === 'manual' ? 'entered' : 'reported') : 'calculated',
        warnings: ['Reaction time only; workup, isolation, and drying may be missing.'],
      }
    } else if (stepsWithDuration.length > 0) {
      const stepPositions = missingDurationSteps.map((s) => s.position).join(', ')
      durationMetric = {
        value: null,
        unit: 'h',
        status: 'partial',
        origin: 'entered',
        warnings: [
          `Total duration unknown: missing duration for step(s) ${stepPositions}.`,
        ],
      }
    } else {
      durationMetric = {
        value: null,
        unit: 'h',
        status: 'unknown',
        origin: 'entered',
        warnings: ['No step duration data entered.'],
      }
    }
  }

  // 4. Cost Metric
  let costMetric: IRouteMetric
  if (rawEvaluation) {
    const rawCost = rawEvaluation.actual_material_cost as number | null
    const coverage = (rawEvaluation.actual_cost_coverage as number) || 0
    const currency = (rawEvaluation.currency as string) || baseCurrency
    const rawWarnings = (rawEvaluation.warnings as string[]) || []

    if (rawCost !== null && rawCost !== undefined && coverage === 1 && rawWarnings.length === 0 && ((rawEvaluation.unpriced_materials as unknown[]) || []).length === 0) {
      costMetric = {
        value: Math.round(rawCost * 100) / 100,
        unit: currency,
        status: 'complete',
        origin: 'calculated',
        evidence: `Pricing coverage: ${Math.round(coverage * 100)}% from the prototype material-cost engine`,
        warnings: rawWarnings,
      }
    } else {
      costMetric = {
        value: rawCost !== null && rawCost !== undefined ? Math.round(rawCost * 100) / 100 : null,
        unit: currency,
        status: coverage > 0 ? 'partial' : 'unknown',
        origin: 'calculated',
        evidence: `Pricing coverage: ${Math.round(coverage * 100)}%`,
        warnings: [
          'Partial material estimate: unresolved quantities, prices, or warnings prevent complete-cost status.',
          ...rawWarnings,
        ],
      }
    }
  } else {
    costMetric = {
      value: null,
      unit: baseCurrency,
      status: 'unknown',
      origin: 'entered',
      warnings: [
        'Manual route draft: commercial quotes have not been linked or evaluated.',
      ],
    }
  }

  // 5. Safety & Hazard Metric
  let safetyMetric: IRouteMetric
  const hazardNotes = steps
    .map((s) => s.hazardNotes)
    .filter((h): h is string => Boolean(h && h.trim()))

  if (hazardNotes.length > 0) {
    safetyMetric = {
      value: `${hazardNotes.length} hazard flag(s)`,
      unit: 'flags',
      status: 'partial',
      origin: 'entered',
      warnings: [
        ...hazardNotes,
        'Safety profile is user-entered; not verified against regulatory toxicological databases.',
      ],
    }
  } else {
    safetyMetric = {
      value: 'Unreviewed safety profile',
      unit: '',
      status: 'unknown',
      origin: 'entered',
      warnings: [
        'No hazard information entered. A route cannot be assumed safe when safety data is missing.',
      ],
    }
  }

  return {
    green: {
      value: steps.some(s => s.solvent.trim()) ? new Set(steps.map(s => s.solvent.trim()).filter(Boolean)).size : null,
      unit: 'distinct solvents',
      status: steps.some(s => s.solvent.trim()) ? 'partial' : 'unknown',
      origin: 'calculated',
      warnings: ['Solvent diversity screening only; solvent amounts and workup are absent. This is not PMI, E-factor, or a sustainability score.'],
    },
    stepCount: stepCountMetric,
    yield: yieldMetric,
    duration: durationMetric,
    cost: costMetric,
    safety: safetyMetric,
  }
}

export function buildRouteSnapshot(
  routeId: string,
  origin: RouteOrigin,
  revision: number,
  steps: IRouteStep[],
  baseCurrency: string = 'USD',
  rawEvaluation?: Record<string, unknown> | null
): IRouteSnapshot {
  const metrics = normalizeRouteMetrics(routeId, origin, steps, baseCurrency, rawEvaluation)
  return {
    routeId,
    origin,
    revision,
    steps: JSON.parse(JSON.stringify(steps)),
    metrics,
    rawEvaluation: rawEvaluation ? JSON.parse(JSON.stringify(rawEvaluation)) : null,
  }
}
