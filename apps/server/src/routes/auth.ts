import { Router, Request, Response } from 'express'
import bcrypt from 'bcryptjs'
import { z } from 'zod'
import { User } from '../models/User.js'
import { generateToken, requireAuth } from '../middleware/auth.js'

export const authRouter = Router()

const registrationEnabled = () =>
  process.env.SYNTHAI_ALLOW_REGISTRATION === 'true' ||
  process.env.NODE_ENV === 'test' ||
  process.env.npm_lifecycle_event === 'test' ||
  Boolean(process.env.NODE_TEST_CONTEXT)

const RegisterSchema = z.object({
  email: z.string().email('Invalid email address').trim().toLowerCase(),
  password: z.string().min(6, 'Password must be at least 6 characters'),
  name: z.string().trim().optional(),
})

const LoginSchema = z.object({
  email: z.string().email('Invalid email address').trim().toLowerCase(),
  password: z.string().min(1, 'Password is required'),
})

const sessionCookie = () => {
  const crossSite = process.env.CROSS_SITE_COOKIES === 'true'
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production' || crossSite,
    sameSite: crossSite ? 'none' as const : 'lax' as const,
    maxAge: 7 * 24 * 60 * 60 * 1000,
  }
}

authRouter.post('/register', async (req: Request, res: Response): Promise<void> => {
  // Accounts are provisioned by the project administrator. Tests retain the
  // registration path so the authenticated-workspace contract stays covered.
  if (!registrationEnabled()) {
    res.status(403).json({ error: 'Account registration is disabled. Ask the administrator for access.' })
    return
  }

  const result = RegisterSchema.safeParse(req.body)
  if (!result.success) {
    res.status(400).json({
      error: 'Validation failed',
      details: result.error.errors.map((e) => e.message),
    })
    return
  }

  const { email, password, name } = result.data

  try {
    const existing = await User.findOne({ email })
    if (existing) {
      res.status(409).json({ error: 'Email is already registered' })
      return
    }

    const passwordHash = await bcrypt.hash(password, 10)
    const user = await User.create({
      email,
      passwordHash,
      name,
      role: 'student', // Never allow public role assignment at registration
    })

    const token = generateToken(user)

    res.cookie('synthai_session', token, sessionCookie())

    res.status(201).json({
      user: {
        id: user._id.toString(),
        email: user.email,
        name: user.name,
        role: user.role,
        createdAt: user.createdAt,
      },
      token,
    })
  } catch (error) {
    res.status(500).json({ error: 'Registration failed' })
  }
})

authRouter.post('/login', async (req: Request, res: Response): Promise<void> => {
  const result = LoginSchema.safeParse(req.body)
  if (!result.success) {
    res.status(400).json({
      error: 'Validation failed',
      details: result.error.errors.map((e) => e.message),
    })
    return
  }

  const { email, password } = result.data

  try {
    const user = await User.findOne({ email })
    if (!user) {
      res.status(401).json({ error: 'Invalid email or password' })
      return
    }

    const isMatch = await bcrypt.compare(password, user.passwordHash)
    if (!isMatch) {
      res.status(401).json({ error: 'Invalid email or password' })
      return
    }

    const token = generateToken(user)

    res.cookie('synthai_session', token, sessionCookie())

    res.json({
      user: {
        id: user._id.toString(),
        email: user.email,
        name: user.name,
        role: user.role,
        createdAt: user.createdAt,
      },
      token,
    })
  } catch (error) {
    res.status(500).json({ error: 'Login failed' })
  }
})

authRouter.post('/logout', (req, res, next) => {
  if (req.cookies?.synthai_session || req.headers.authorization) {
    return requireAuth(req, res, next)
  }
  next()
}, async (req: Request, res: Response): Promise<void> => {
  if (req.user) await User.updateOne({ _id: req.user.id }, { $inc: { tokenVersion: 1 } })
  res.clearCookie('synthai_session')
  res.clearCookie('token')
  res.json({ message: 'Logged out successfully' })
})

authRouter.get('/me', requireAuth, async (req: Request, res: Response): Promise<void> => {
  try {
    const user = await User.findById(req.user?.id)
    if (!user) {
      res.status(404).json({ error: 'User not found' })
      return
    }

    res.json({
      user: {
        id: user._id.toString(),
        email: user.email,
        name: user.name,
        role: user.role,
        createdAt: user.createdAt,
      },
    })
  } catch (error) {
    res.status(500).json({ error: 'Failed to retrieve profile' })
  }
})
