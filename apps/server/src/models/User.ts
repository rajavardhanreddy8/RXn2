import mongoose, { Document, Schema, Types } from 'mongoose'

export interface IUser extends Document {
  _id: Types.ObjectId
  email: string
  passwordHash: string
  role: 'student' | 'admin'
  tokenVersion: number
  name?: string
  createdAt: Date
  updatedAt: Date
}

const UserSchema = new Schema<IUser>(
  {
    tokenVersion: { type: Number, default: 0 },
    email: {
      type: String,
      required: true,
      unique: true,
      trim: true,
      lowercase: true,
      index: true,
    },
    passwordHash: {
      type: String,
      required: true,
    },
    role: {
      type: String,
      enum: ['student', 'admin'],
      default: 'student',
    },
    name: {
      type: String,
      trim: true,
    },
  },
  {
    timestamps: true,
  }
)

export const User = mongoose.model<IUser>('User', UserSchema)
