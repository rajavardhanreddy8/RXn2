import { Request, Response, NextFunction } from 'express'
import mongoose from 'mongoose'
import { Project, IProject } from '../models/Project.js'

declare global {
  namespace Express {
    interface Request {
      project?: IProject
    }
  }
}

export async function requireProjectOwnership(
  req: Request,
  res: Response,
  next: NextFunction
): Promise<void> {
  const { projectId } = req.params

  if (!projectId || !mongoose.Types.ObjectId.isValid(projectId)) {
    res.status(404).json({ error: 'Project not found' })
    return
  }

  if (!req.user?.id) {
    res.status(401).json({ error: 'Authentication required' })
    return
  }

  try {
    const project = await Project.findOne({
      _id: projectId,
      ...(req.user.role === 'admin' && req.method === 'GET' ? {} : { ownerId: req.user.id }),
    })

    if (!project) {
      // Return 404 to avoid leaking whether unauthorized records exist
      res.status(404).json({ error: 'Project not found' })
      return
    }

    req.project = project
    next()
  } catch (error) {
    res.status(500).json({ error: 'Failed to verify project ownership' })
  }
}
