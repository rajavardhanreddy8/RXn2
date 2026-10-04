import mongoose, { Document, Schema, Types } from 'mongoose'

export type RouteOrigin = 'manual' | 'generated'
export type DurationUnit = 'h' | 'min' | 'd'

export interface IRouteStep {
  position: number
  name: string
  reactants: string[]
  inputLabels: string[]
  productLabel: string
  yieldPercent: number | null
  durationHours: number | null
  durationUnit: DurationUnit
  solvent: string
  conditions: string
  hazardNotes: string
  evidenceRef: string
  dataSource: string
}

export interface IRoute extends Document {
  _id: Types.ObjectId
  projectId: Types.ObjectId
  ownerId: Types.ObjectId
  origin: RouteOrigin
  revision: number
  steps: IRouteStep[]
  createdAt: Date
  updatedAt: Date
}

export const RouteStepSchema = new Schema<IRouteStep>(
  {
    position: {
      type: Number,
      required: true,
      min: [1, 'Step position must be a positive integer'],
    },
    name: {
      type: String,
      default: '',
      trim: true,
    },
    reactants: {
      type: [String],
      default: [],
    },
    inputLabels: {
      type: [String],
      default: [],
    },
    productLabel: {
      type: String,
      default: '',
      trim: true,
    },
    yieldPercent: {
      type: Number,
      default: null,
      validate: {
        validator: function (value: number | null) {
          if (value === null || value === undefined) return true
          return value >= 0 && value <= 100
        },
        message: 'Yield percentage must be between 0 and 100',
      },
    },
    durationHours: {
      type: Number,
      default: null,
      validate: {
        validator: function (value: number | null) {
          if (value === null || value === undefined) return true
          return value >= 0
        },
        message: 'Duration cannot be negative',
      },
    },
    durationUnit: {
      type: String,
      enum: ['h', 'min', 'd'],
      default: 'h',
    },
    solvent: {
      type: String,
      default: '',
      trim: true,
    },
    conditions: {
      type: String,
      default: '',
      trim: true,
    },
    hazardNotes: {
      type: String,
      default: '',
      trim: true,
    },
    evidenceRef: {
      type: String,
      default: '',
      trim: true,
    },
    dataSource: {
      type: String,
      default: 'manual_entry',
      trim: true,
    },
  },
  { _id: false }
)

const RouteSchema = new Schema<IRoute>(
  {
    projectId: {
      type: Schema.Types.ObjectId,
      ref: 'Project',
      required: true,
      index: true,
    },
    ownerId: {
      type: Schema.Types.ObjectId,
      ref: 'User',
      required: true,
      index: true,
    },
    origin: {
      type: String,
      enum: ['manual', 'generated'],
      default: 'manual',
      required: true,
    },
    revision: {
      type: Number,
      default: 1,
      min: 1,
    },
    steps: {
      type: [RouteStepSchema],
      default: [],
    },
  },
  {
    timestamps: true,
  }
)

RouteSchema.index({ projectId: 1, revision: -1 })
RouteSchema.index({ ownerId: 1, createdAt: -1 })

export const Route = mongoose.model<IRoute>('Route', RouteSchema)
