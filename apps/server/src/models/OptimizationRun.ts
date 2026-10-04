import mongoose, { Document, Schema, Types } from 'mongoose'
import { IRouteStep, RouteOrigin, RouteStepSchema } from './Route.js'
import { MassUnit } from './Project.js'

export type MetricStatus = 'complete' | 'partial' | 'unknown'
export type MetricOrigin = 'entered' | 'reported' | 'calculated' | 'predicted'
export type RunStatus = 'completed' | 'failed'

export interface IRouteMetric {
  value: number | string | null
  unit: string
  status: MetricStatus
  origin: MetricOrigin
  evidence?: string | null
  warnings: string[]
}

export interface IComparisonRouteMetrics {
  yield: IRouteMetric
  duration: IRouteMetric
  cost: IRouteMetric
  safety: IRouteMetric
  green: IRouteMetric
  stepCount: IRouteMetric
}

export interface IRouteSnapshot {
  routeId: string
  origin: RouteOrigin
  revision: number
  steps: IRouteStep[]
  metrics?: IComparisonRouteMetrics
  rawEvaluation?: Record<string, unknown> | null
}

export interface IOptimizationRun extends Document {
  _id: Types.ObjectId
  projectId: Types.ObjectId
  createdBy: Types.ObjectId
  routeSnapshots: IRouteSnapshot[]
  target: {
    compoundId?: string | null
    compoundName?: string | null
    targetMassG: number
    targetMassUnit: MassUnit
  }
  constraints: {
    maxSteps?: number
    baseCurrency?: string
    excludedCompounds?: string[]
    excludedHazards?: string[]
  }
  methodVersion: string
  dataVersion: string
  startedAt: Date
  endedAt: Date
  status: RunStatus
  error?: string | null
  createdAt: Date
  updatedAt: Date
}

const RouteMetricSchema = new Schema<IRouteMetric>(
  {
    value: { type: Schema.Types.Mixed, default: null },
    unit: { type: String, default: '' },
    status: {
      type: String,
      enum: ['complete', 'partial', 'unknown'],
      required: true,
    },
    origin: {
      type: String,
      enum: ['entered', 'reported', 'calculated', 'predicted'],
      required: true,
    },
    evidence: { type: String, default: null },
    warnings: { type: [String], default: [] },
  },
  { _id: false }
)

const ComparisonRouteMetricsSchema = new Schema<IComparisonRouteMetrics>(
  {
    yield: { type: RouteMetricSchema, required: true },
    duration: { type: RouteMetricSchema, required: true },
    cost: { type: RouteMetricSchema, required: true },
    safety: { type: RouteMetricSchema, required: true },
    green: { type: RouteMetricSchema, required: true },
    stepCount: { type: RouteMetricSchema, required: true },
  },
  { _id: false }
)

const RouteSnapshotSchema = new Schema<IRouteSnapshot>(
  {
    routeId: { type: String, required: true },
    origin: { type: String, enum: ['manual', 'generated'], required: true },
    revision: { type: Number, default: 1 },
    steps: { type: [RouteStepSchema], default: [] },
    metrics: { type: ComparisonRouteMetricsSchema, default: null },
    rawEvaluation: { type: Schema.Types.Mixed, default: null },
  },
  { _id: false }
)

const OptimizationRunSchema = new Schema<IOptimizationRun>(
  {
    projectId: {
      type: Schema.Types.ObjectId,
      ref: 'Project',
      required: true,
      index: true,
    },
    createdBy: {
      type: Schema.Types.ObjectId,
      ref: 'User',
      required: true,
      index: true,
    },
    routeSnapshots: {
      type: [RouteSnapshotSchema],
      required: true,
    },
    target: {
      compoundId: { type: String, default: null },
      compoundName: { type: String, default: null },
      targetMassG: { type: Number, required: true },
      targetMassUnit: { type: String, default: 'g' },
    },
    constraints: {
      maxSteps: { type: Number, default: 6 },
      baseCurrency: { type: String, default: 'USD' },
      excludedCompounds: { type: [String], default: [] },
      excludedHazards: { type: [String], default: [] },
    },
    methodVersion: {
      type: String,
      default: 'synthai-comparison-v1',
    },
    dataVersion: {
      type: String,
      default: 'rxn2-evidence-v1',
    },
    startedAt: {
      type: Date,
      default: Date.now,
    },
    endedAt: {
      type: Date,
      default: Date.now,
    },
    status: {
      type: String,
      enum: ['completed', 'failed'],
      default: 'completed',
      required: true,
    },
    error: {
      type: String,
      default: null,
    },
  },
  {
    timestamps: true,
  }
)

OptimizationRunSchema.index({ projectId: 1, createdAt: -1 })
OptimizationRunSchema.index({ createdBy: 1, createdAt: -1 })

export const OptimizationRun = mongoose.model<IOptimizationRun>(
  'OptimizationRun',
  OptimizationRunSchema
)
