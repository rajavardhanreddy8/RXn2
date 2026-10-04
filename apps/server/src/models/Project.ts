import mongoose, { Document, Schema, Types } from 'mongoose'

export type MassUnit = 'g' | 'mg' | 'kg'

export interface IProject extends Document {
  _id: Types.ObjectId
  ownerId: Types.ObjectId
  title: string
  targetCompoundId?: string
  targetCompoundName?: string
  targetMassG: number
  targetMassUnit: MassUnit
  notes: string
  createdAt: Date
  updatedAt: Date
}

const ProjectSchema = new Schema<IProject>(
  {
    ownerId: {
      type: Schema.Types.ObjectId,
      ref: 'User',
      required: true,
      index: true,
    },
    title: {
      type: String,
      required: true,
      trim: true,
      minlength: [1, 'Title cannot be empty'],
    },
    targetCompoundId: {
      type: String,
      trim: true,
      default: null,
    },
    targetCompoundName: {
      type: String,
      trim: true,
      default: null,
    },
    targetMassG: {
      type: Number,
      required: true,
      min: [0.000001, 'Target mass must be positive'],
    },
    targetMassUnit: {
      type: String,
      enum: ['g', 'mg', 'kg'],
      default: 'g',
      required: true,
    },
    notes: {
      type: String,
      default: '',
    },
  },
  {
    timestamps: true,
  }
)

ProjectSchema.index({ ownerId: 1, createdAt: -1 })

export const Project = mongoose.model<IProject>('Project', ProjectSchema)
