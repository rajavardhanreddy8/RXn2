import { describe, it, before, after, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import request from 'supertest'
import { MongoMemoryServer } from 'mongodb-memory-server'
import mongoose from 'mongoose'
import { app } from '../src/index.js'
import { User } from '../src/models/User.js'
import { Project } from '../src/models/Project.js'
import { Route } from '../src/models/Route.js'
import { OptimizationRun } from '../src/models/OptimizationRun.js'

describe('Phase 4 — Security, Validation, and Scientific Honesty Verification', () => {
  let mongoServer: MongoMemoryServer
  let userAToken: string
  let userBToken: string
  let userAId: string
  let userBId: string

  before(async () => {
    process.env.NODE_ENV = 'test'
    mongoServer = await MongoMemoryServer.create()
    const uri = mongoServer.getUri()
    await mongoose.connect(uri)
  })

  after(async () => {
    await mongoose.disconnect()
    await mongoServer.stop()
  })

  beforeEach(async () => {
    await OptimizationRun.deleteMany({})
    await Route.deleteMany({})
    await Project.deleteMany({})
    await User.deleteMany({})

    // Create User A
    const regA = await request(app).post('/api/v1/auth/register').send({
      email: 'user_a@example.com',
      password: 'password123',
      name: 'User A',
    })
    userAToken = regA.body.token
    userAId = regA.body.user.id

    // Create User B
    const regB = await request(app).post('/api/v1/auth/register').send({
      email: 'user_b@example.com',
      password: 'password123',
      name: 'User B',
    })
    userBToken = regB.body.token
    userBId = regB.body.user.id
  })

  describe('1. Security Verification', () => {
    it('rejects login with incorrect password with 401', async () => {
      const res = await request(app).post('/api/v1/auth/login').send({
        email: 'user_a@example.com',
        password: 'wrong_password',
      })
      assert.equal(res.status, 401)
      assert.match(res.body.error, /invalid email or password/i)
    })

    it('denies access to protected endpoints when cookie/token is missing', async () => {
      const res = await request(app).get('/api/v1/projects')
      assert.equal(res.status, 401)
      assert.match(res.body.error, /authentication required/i)
    })

    it('denies User B from reading User A’s project (returns 404 without data leak)', async () => {
      const projRes = await request(app)
        .post('/api/v1/projects')
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          title: 'User A Secret Project',
          targetCompoundName: 'Confidential API',
          targetMassG: 500,
        })
      const projectId = projRes.body.project._id

      const res = await request(app)
        .get(`/api/v1/projects/${projectId}`)
        .set('Authorization', `Bearer ${userBToken}`)
      assert.equal(res.status, 404)
      assert.equal(res.body.error, 'Project not found')
    })

    it('denies User B from modifying User A’s route', async () => {
      const proj = await Project.create({
        ownerId: userAId,
        title: 'Project A',
        targetMassG: 100,
        targetMassUnit: 'g',
      })
      const route = await Route.create({
        projectId: proj._id,
        ownerId: userAId,
        origin: 'manual',
        revision: 1,
        steps: [{ position: 1, name: 'Step 1' }],
      })

      const res = await request(app)
        .patch(`/api/v1/projects/${proj._id}/routes/${route._id}`)
        .set('Authorization', `Bearer ${userBToken}`)
        .send({ origin: 'generated' })
      assert.equal(res.status, 404)
    })

    it('denies User B from deleting User A’s route', async () => {
      const proj = await Project.create({
        ownerId: userAId,
        title: 'Project A',
        targetMassG: 100,
        targetMassUnit: 'g',
      })
      const route = await Route.create({
        projectId: proj._id,
        ownerId: userAId,
        origin: 'manual',
        revision: 1,
        steps: [{ position: 1, name: 'Step 1' }],
      })

      const res = await request(app)
        .delete(`/api/v1/projects/${proj._id}/routes/${route._id}`)
        .set('Authorization', `Bearer ${userBToken}`)
      assert.equal(res.status, 404)

      const stillExists = await Route.findById(route._id)
      assert.ok(stillExists)
    })

    it('ignores client-supplied ownerId and strictly binds to authenticated session', async () => {
      const spoofedOwnerId = new mongoose.Types.ObjectId().toString()
      const res = await request(app)
        .post('/api/v1/projects')
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          title: 'Spoofed Owner Project',
          ownerId: spoofedOwnerId,
          targetMassG: 250,
        })
      assert.equal(res.status, 201)
      assert.equal(res.body.project.ownerId, userAId)
      assert.notEqual(res.body.project.ownerId, spoofedOwnerId)
    })

    it('ignores client-supplied role during registration and forces student role', async () => {
      const res = await request(app)
        .post('/api/v1/auth/register')
        .send({
          email: 'attacker@example.com',
          password: 'password123',
          role: 'admin',
        })
      assert.equal(res.status, 201)
      assert.equal(res.body.user.role, 'student')

      const savedUser = await User.findOne({ email: 'attacker@example.com' })
      assert.equal(savedUser?.role, 'student')
    })
  })

  describe('2. Validation & Boundary Verification', () => {
    it('rejects invalid / non-positive target mass with 400', async () => {
      const res = await request(app)
        .post('/api/v1/projects')
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          title: 'Negative Mass Test',
          targetMassG: -50,
        })
      assert.equal(res.status, 400)
      assert.match(res.body.details[0], /positive/i)
    })

    it('rejects invalid yield percentage (<0 or >100) with 400', async () => {
      const proj = await Project.create({
        ownerId: userAId,
        title: 'Project A',
        targetMassG: 100,
        targetMassUnit: 'g',
      })

      const res = await request(app)
        .post(`/api/v1/projects/${proj._id}/routes`)
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          origin: 'manual',
          steps: [{ position: 1, name: 'Step 1', yieldPercent: 125 }],
        })
      assert.equal(res.status, 400)
      assert.match(res.body.details[0], /yield/i)
    })

    it('rejects negative step duration with 400', async () => {
      const proj = await Project.create({
        ownerId: userAId,
        title: 'Project A',
        targetMassG: 100,
        targetMassUnit: 'g',
      })

      const res = await request(app)
        .post(`/api/v1/projects/${proj._id}/routes`)
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          origin: 'manual',
          steps: [{ position: 1, name: 'Step 1', durationHours: -2 }],
        })
      assert.equal(res.status, 400)
      assert.match(res.body.details[0], /duration/i)
    })

    it('handles malformed project ID string safely with 404 without crashing', async () => {
      const res = await request(app)
        .get('/api/v1/projects/invalid-malformed-id-123')
        .set('Authorization', `Bearer ${userAToken}`)
      assert.equal(res.status, 404)
      assert.equal(res.body.error, 'Project not found')
    })

    it('handles malformed route ID string safely with 404 without crashing', async () => {
      const proj = await Project.create({
        ownerId: userAId,
        title: 'Project A',
        targetMassG: 100,
        targetMassUnit: 'g',
      })

      const res = await request(app)
        .get(`/api/v1/projects/${proj._id}/routes/malformed-route-id-999`)
        .set('Authorization', `Bearer ${userAToken}`)
      assert.equal(res.status, 404)
      assert.equal(res.body.error, 'Route not found')
    })
  })

  describe('3. Scientific Honesty & Immutability Verification', () => {
    it('preserves missing yield as null/partial without substituting 0', async () => {
      const proj = await Project.create({
        ownerId: userAId,
        title: 'Project A',
        targetMassG: 100,
        targetMassUnit: 'g',
      })
      const route1 = await Route.create({
        projectId: proj._id,
        ownerId: userAId,
        origin: 'manual',
        revision: 1,
        steps: [
          { position: 1, name: 'Step 1', yieldPercent: 80 },
          { position: 2, name: 'Step 2', yieldPercent: null }, // missing
        ],
      })
      const route2 = await Route.create({
        projectId: proj._id,
        ownerId: userAId,
        origin: 'manual',
        revision: 1,
        steps: [
          { position: 1, name: 'Step 1', yieldPercent: 90 },
          { position: 2, name: 'Step 2', yieldPercent: 85 },
        ],
      })

      const res = await request(app)
        .post(`/api/v1/projects/${proj._id}/comparisons`)
        .set('Authorization', `Bearer ${userAToken}`)
        .send({ routeIds: [route1._id.toString(), route2._id.toString()] })

      assert.equal(res.status, 201)
      const r1Metrics = res.body.comparison.routeSnapshots[0].metrics.yield
      assert.equal(r1Metrics.value, null)
      assert.equal(r1Metrics.status, 'partial')
      assert.ok(r1Metrics.warnings.length > 0)

      const r2Metrics = res.body.comparison.routeSnapshots[1].metrics.yield
      assert.equal(r2Metrics.value, 76.5)
      assert.equal(r2Metrics.status, 'complete')
    })

    it('guarantees snapshot immutability when route is modified post-comparison', async () => {
      const proj = await Project.create({
        ownerId: userAId,
        title: 'Project A',
        targetMassG: 100,
        targetMassUnit: 'g',
      })
      const route1 = await Route.create({
        projectId: proj._id,
        ownerId: userAId,
        origin: 'manual',
        revision: 1,
        steps: [{ position: 1, name: 'Step 1', yieldPercent: 50, durationHours: 4 }],
      })
      const route2 = await Route.create({
        projectId: proj._id,
        ownerId: userAId,
        origin: 'manual',
        revision: 1,
        steps: [{ position: 1, name: 'Step 1', yieldPercent: 60, durationHours: 6 }],
      })

      // 1. Create Comparison
      const compRes = await request(app)
        .post(`/api/v1/projects/${proj._id}/comparisons`)
        .set('Authorization', `Bearer ${userAToken}`)
        .send({ routeIds: [route1._id.toString(), route2._id.toString()] })
      const compId = compRes.body.comparison._id

      // Verify initial snapshot metrics
      assert.equal(compRes.body.comparison.routeSnapshots[0].metrics.yield.value, 50)
      assert.equal(compRes.body.comparison.routeSnapshots[0].revision, 1)

      // 2. Modify Route 1 in workspace
      await request(app)
        .patch(`/api/v1/projects/${proj._id}/routes/${route1._id}`)
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          steps: [
            { position: 1, name: 'Step 1 Modified', yieldPercent: 99, durationHours: 1 },
          ],
        })

      // 3. Fetch Historical Comparison
      const histRes = await request(app)
        .get(`/api/v1/projects/${proj._id}/comparisons/${compId}`)
        .set('Authorization', `Bearer ${userAToken}`)

      assert.equal(histRes.status, 200)
      const historicalRoute1 = histRes.body.comparison.routeSnapshots[0]
      // Snapshot MUST retain the original state (50% yield, revision 1, step 1 name)
      assert.equal(historicalRoute1.revision, 1)
      assert.equal(historicalRoute1.steps[0].yieldPercent, 50)
      assert.equal(historicalRoute1.steps[0].name, 'Step 1')
      assert.equal(historicalRoute1.metrics.yield.value, 50)
    })
  })
})
