import './config.js'
import express, { Express, Request, Response, NextFunction } from 'express'
import cors from 'cors'
import cookieParser from 'cookie-parser'
import rateLimit from 'express-rate-limit'
import { connectMongo } from './db.js'
import { authRouter } from './routes/auth.js'
import { projectsRouter } from './routes/projects.js'
import { routesRouter } from './routes/routes.js'
import { chemistryRouter } from './routes/chemistry.js'
import { comparisonsRouter } from './routes/comparisons.js'


export const app: Express = express()
const PORT = process.env.PORT || 4000

// Middleware
app.use(
  cors({
    origin: [
      'http://localhost:5173',
      'http://127.0.0.1:5173',
      process.env.CLIENT_ORIGIN || '',
    ].filter(Boolean),
    credentials: true,
  })
)
app.use(express.json())
app.use(cookieParser())

// Rate limiting for authentication endpoints
const loginLimiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 minutes
  max: 20, // max 20 login attempts per window
  message: { error: 'Too many login attempts. Please try again later.' },
  standardHeaders: true,
  legacyHeaders: false,
})

app.use('/api/v1/auth/login', loginLimiter)

// Health check endpoint
app.get('/api/v1/health', (_req: Request, res: Response) => {
  res.json({
    status: 'ok',
    service: 'synthai-express-server',
    version: '0.1.0',
    timestamp: new Date().toISOString(),
  })
})

// Mount versioned routers
app.use('/api/v1/auth', authRouter)
app.use('/api/v1', chemistryRouter)
app.use('/api/v1/projects', projectsRouter)
app.use('/api/v1/projects/:projectId/routes', routesRouter)
app.use('/api/v1/projects/:projectId/comparisons', comparisonsRouter)

// 404 handler
app.use((_req: Request, res: Response) => {
  res.status(404).json({ error: 'Endpoint not found' })
})

// Global error handler
app.use((err: Error, _req: Request, res: Response, _next: NextFunction) => {
  console.error('Unhandled server error:', err)
  res.status(500).json({ error: 'Internal server error' })
})

// Start server only when executed directly and not under tests
const isTestEnv = process.env.NODE_ENV === 'test' || process.env.npm_lifecycle_event === 'test' || Boolean(process.env.NODE_TEST_CONTEXT)

if (!isTestEnv) {
  if (!process.env.JWT_SECRET || process.env.JWT_SECRET.length < 32) {
    throw new Error('Set JWT_SECRET to a random value of at least 32 characters in the root .env')
  }
  connectMongo()
    .then(() => {
      app.listen(PORT, () => {
        console.log(`SynthAI Express server running on port ${PORT}`)
      })
    })
    .catch((err) => {
      console.error('Failed to start server:', err)
      process.exit(1)
    })
}
