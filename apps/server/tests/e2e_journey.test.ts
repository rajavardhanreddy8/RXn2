import { describe, it, before, after } from 'node:test'
import assert from 'node:assert/strict'
import request from 'supertest'
import { MongoMemoryServer } from 'mongodb-memory-server'
import mongoose from 'mongoose'
import { app } from '../src/index.js'
import { User } from '../src/models/User.js'
import { Project } from '../src/models/Project.js'
import { Route } from '../src/models/Route.js'
import { OptimizationRun } from '../src/models/OptimizationRun.js'

describe('End-to-End User Journey (Phase 4)', () => {
  let mongoServer: MongoMemoryServer
  let authCookie: string
  let authToken: string
  let userId: string
  let projectId: string
  let route1Id: string
  let route2Id: string
  let comparisonRunId: string

  before(async () => {
    process.env.NODE_ENV = 'test'
    mongoServer = await MongoMemoryServer.create()
    const uri = mongoServer.getUri()
    await mongoose.connect(uri)
    await User.deleteMany({})
    await Project.deleteMany({})
    await Route.deleteMany({})
    await OptimizationRun.deleteMany({})
  })

  after(async () => {
    await mongoose.disconnect()
    await mongoServer.stop()
  })

  it('Step 1: Register and Login user', async () => {
    const regRes = await request(app)
      .post('/api/v1/auth/register')
      .send({
        email: 'chemist_demo@synthai.org',
        password: 'Password123!',
        name: 'Demo Chemist',
      })

    assert.equal(regRes.status, 201)
    assert.equal(regRes.body.user.email, 'chemist_demo@synthai.org')
    assert.equal(regRes.body.user.name, 'Demo Chemist')
    assert.equal(regRes.body.user.role, 'student')
    assert.ok(regRes.body.token)

    authToken = regRes.body.token
    userId = regRes.body.user.id
    authCookie = regRes.headers['set-cookie'][0]

    // Verify session via /me
    const meRes = await request(app)
      .get('/api/v1/auth/me')
      .set('Cookie', authCookie)
    assert.equal(meRes.status, 200)
    assert.equal(meRes.body.user.id, userId)
  })

  it('Step 2: Create a new Synthesis Project', async () => {
    const projRes = await request(app)
      .post('/api/v1/projects')
      .set('Cookie', authCookie)
      .send({
        title: 'Apixaban Scaleup Study',
        targetCompoundName: 'Apixaban',
        targetCompoundId: 'BENCH-APIXABAN',
        targetMassG: 500,
        targetMassUnit: 'g',
        notes: 'Scaleup route evaluation and comparative economic study.',
      })

    assert.equal(projRes.status, 201)
    assert.equal(projRes.body.project.title, 'Apixaban Scaleup Study')
    assert.equal(projRes.body.project.ownerId, userId)
    assert.equal(projRes.body.project.targetMassG, 500)
    projectId = projRes.body.project._id
  })

  it('Step 3: Create two Route Drafts with reaction steps', async () => {
    // Route 1: 2-step sequence
    const r1Res = await request(app)
      .post(`/api/v1/projects/${projectId}/routes`)
      .set('Cookie', authCookie)
      .send({
        origin: 'manual',
        steps: [
          {
            position: 1,
            name: 'Pyridine Coupling',
            productLabel: 'Intermediate A',
            yieldPercent: 85,
            durationHours: 4,
            durationUnit: 'h',
            solvent: 'DCM',
            hazardNotes: 'Mild exotherm',
            evidenceRef: 'US-2024-001234',
          },
          {
            position: 2,
            name: 'Lactam Cyclization',
            productLabel: 'Apixaban Crude',
            yieldPercent: 80,
            durationHours: 6,
            durationUnit: 'h',
            solvent: 'DMF',
            hazardNotes: 'Toxic solvent handle under hood',
            evidenceRef: 'Patent EP-312984',
          },
        ],
      })

    assert.equal(r1Res.status, 201)
    assert.equal(r1Res.body.route.steps.length, 2)
    assert.equal(r1Res.body.route.revision, 1)
    route1Id = r1Res.body.route._id

    // Route 2: 1-step sequence
    const r2Res = await request(app)
      .post(`/api/v1/projects/${projectId}/routes`)
      .set('Cookie', authCookie)
      .send({
        origin: 'manual',
        steps: [
          {
            position: 1,
            name: 'One-Pot Condensation',
            productLabel: 'Apixaban Crude',
            yieldPercent: 68,
            durationHours: 12,
            durationUnit: 'h',
            solvent: 'THF',
            evidenceRef: 'WO-2023-998811',
          },
        ],
      })

    assert.equal(r2Res.status, 201)
    assert.equal(r2Res.body.route.steps.length, 1)
    route2Id = r2Res.body.route._id

    // Verify listing routes
    const listRes = await request(app)
      .get(`/api/v1/projects/${projectId}/routes`)
      .set('Cookie', authCookie)
    assert.equal(listRes.status, 200)
    assert.equal(listRes.body.total, 2)
  })

  it('Step 4: Run Multi-Route Comparison (2 Routes) and Save Snapshot', async () => {
    const compRes = await request(app)
      .post(`/api/v1/projects/${projectId}/comparisons`)
      .set('Cookie', authCookie)
      .send({
        routeIds: [route1Id, route2Id],
        baseCurrency: 'USD',
      })

    assert.equal(compRes.status, 201)
    assert.equal(compRes.body.comparison.status, 'completed')
    assert.equal(compRes.body.comparison.routeSnapshots.length, 2)

    // Verify Route 1 normalized metrics
    const snap1 = compRes.body.comparison.routeSnapshots[0]
    assert.equal(snap1.metrics.yield.value, 68.0) // 85% * 80% = 68%
    assert.equal(snap1.metrics.yield.status, 'complete')
    assert.equal(snap1.metrics.duration.value, 10.0) // 4h + 6h = 10h
    assert.equal(snap1.metrics.duration.status, 'complete')
    assert.equal(snap1.metrics.cost.status, 'unknown') // manual route without quote pricing
    assert.equal(snap1.metrics.safety.status, 'partial') // step 1 has hazard notes, step 2 does not

    // Verify Route 2 normalized metrics
    const snap2 = compRes.body.comparison.routeSnapshots[1]
    assert.equal(snap2.metrics.yield.value, 68.0)
    assert.equal(snap2.metrics.duration.value, 12.0)

    comparisonRunId = compRes.body.comparison._id
  })

  it('Step 5: Inspect Comparison History & Open Saved Comparison Snapshot', async () => {
    // List runs
    const historyRes = await request(app)
      .get(`/api/v1/projects/${projectId}/comparisons`)
      .set('Cookie', authCookie)

    assert.equal(historyRes.status, 200)
    assert.equal(historyRes.body.total, 1)
    assert.equal(historyRes.body.comparisons[0]._id, comparisonRunId)
    assert.equal(historyRes.body.comparisons[0].status, 'completed')

    // Fetch specific run
    const singleRes = await request(app)
      .get(`/api/v1/projects/${projectId}/comparisons/${comparisonRunId}`)
      .set('Cookie', authCookie)

    assert.equal(singleRes.status, 200)
    assert.equal(singleRes.body.comparison._id, comparisonRunId)
    assert.equal(singleRes.body.comparison.routeSnapshots.length, 2)
  })

  it('Step 6: Edit Route and verify old Snapshot remains Immutable', async () => {
    // Edit Route 1 in workspace
    await request(app)
      .patch(`/api/v1/projects/${projectId}/routes/${route1Id}`)
      .set('Cookie', authCookie)
      .send({
        steps: [
          { position: 1, name: 'Modified Step 1', yieldPercent: 99, durationHours: 1 },
        ],
      })

    // Reopen historical snapshot
    const singleRes = await request(app)
      .get(`/api/v1/projects/${projectId}/comparisons/${comparisonRunId}`)
      .set('Cookie', authCookie)

    assert.equal(singleRes.status, 200)
    // Historical snapshot MUST remain 2 steps with 68% yield
    assert.equal(singleRes.body.comparison.routeSnapshots[0].steps.length, 2)
    assert.equal(singleRes.body.comparison.routeSnapshots[0].metrics.yield.value, 68.0)
    assert.equal(singleRes.body.comparison.routeSnapshots[0].revision, 1)
  })

  it('Step 7: Logout User and verify session termination', async () => {
    const logoutRes = await request(app)
      .post('/api/v1/auth/logout')
      .set('Cookie', authCookie)

    assert.equal(logoutRes.status, 200)

    // Verify /me fails after logout
    const clearedCookie = logoutRes.headers['set-cookie']
      ? logoutRes.headers['set-cookie'][0]
      : ''
    const meRes = await request(app)
      .get('/api/v1/auth/me')
      .set('Cookie', clearedCookie)

    assert.equal(meRes.status, 401)
  })
})
