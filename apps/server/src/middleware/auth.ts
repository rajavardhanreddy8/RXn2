import { Request, Response, NextFunction } from 'express'
import jwt from 'jsonwebtoken'
import { User } from '../models/User.js'

export interface AuthUser {
  id: string
  email: string
  role: 'student' | 'admin'
  tokenVersion?: number
}

declare global {
  namespace Express {
    interface Request {
      user?: AuthUser
    }
  }
}

const JWT_SECRET = process.env.JWT_SECRET || (process.env.NODE_ENV === 'test' || process.env.npm_lifecycle_event === 'test' || process.env.NODE_TEST_CONTEXT ? 'test-only-secret-not-for-serving-users' : '')

export function generateToken(user: { _id: string | Object; email: string; role: string; tokenVersion?: number }): string {
  return jwt.sign(
    {
      id: user._id.toString(),
      email: user.email,
      role: user.role,
      tokenVersion: user.tokenVersion || 0,
    },
    JWT_SECRET,
    { expiresIn: '7d' }
  )
}

export async function requireAuth(req: Request, res: Response, next: NextFunction): Promise<void> {
  const token =
    req.cookies?.synthai_session ||
    req.cookies?.token ||
    (req.headers.authorization?.startsWith('Bearer ')
      ? req.headers.authorization.slice(7)
      : null)

  if (!token) {
    res.status(401).json({ error: 'Authentication required' })
    return
  }

  try {
    const payload = jwt.verify(token, JWT_SECRET) as AuthUser
    const user = await User.findById(payload.id)
    if (!user || (payload.tokenVersion || 0) !== user.tokenVersion) {
      res.status(401).json({ error: 'Session has ended; sign in again' })
      return
    }
    req.user = {
      id: payload.id,
      email: payload.email,
      role: user.role,
    }
    next()
  } catch (error) {
    res.status(401).json({ error: 'Invalid or expired session token' })
  }
}

export function requireAdmin(req: Request, res: Response, next: NextFunction): void {
  if (req.user?.role !== 'admin') {
    res.status(403).json({ error: 'Administrator access required' })
    return
  }
  next()
}

export function optionalAuth(req: Request, _res: Response, next: NextFunction): void {
  const token =
    req.cookies?.synthai_session ||
    req.cookies?.token ||
    (req.headers.authorization?.startsWith('Bearer ')
      ? req.headers.authorization.slice(7)
      : null)

  if (token) {
    try {
      const payload = jwt.verify(token, JWT_SECRET) as AuthUser
      req.user = {
        id: payload.id,
        email: payload.email,
        role: payload.role,
      }
    } catch {
      // Ignore invalid optional tokens
    }
  }
  next()
}
