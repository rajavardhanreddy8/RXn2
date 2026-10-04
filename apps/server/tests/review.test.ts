import { before, after, it } from 'node:test'
import assert from 'node:assert/strict'
import request from 'supertest'
import mongoose from 'mongoose'
import { MongoMemoryServer } from 'mongodb-memory-server'
import { app } from '../src/index.js'
import { User } from '../src/models/User.js'
import { Project } from '../src/models/Project.js'
import { Route } from '../src/models/Route.js'
import { OptimizationRun } from '../src/models/OptimizationRun.js'
import { normalizeRouteMetrics } from '../src/services/comparisonService.js'
import { generateToken } from '../src/middleware/auth.js'
import { fastApiClient, FastAPIServiceError } from '../src/services/fastapiClient.js'

let mongo: MongoMemoryServer
before(async () => {
  mongo = await MongoMemoryServer.create()
  await mongoose.connect(mongo.getUri())
})
after(async () => { await mongoose.disconnect(); await mongo.stop() })

it('partial cost, prototype feasibility, and absent solvent data do not become complete assessments', () => {
  const metrics = normalizeRouteMetrics('generated', 'generated', [], 'USD', {
    actual_material_cost: 40, actual_cost_coverage: 0.85, feasibility_score: 99, warnings: [],
  })
  assert.equal(metrics.cost.status, 'partial')
  assert.equal(metrics.safety.status, 'unknown')
  assert.equal(metrics.green.status, 'unknown')
})

it('admin can read another owner project but cannot edit it, and logout invalidates the old token', async () => {
  const owner = await User.create({ email: 'owner@review.example', passwordHash: 'unused', role: 'student' })
  const admin = await User.create({ email: 'admin@review.example', passwordHash: 'unused', role: 'admin' })
  const project = await Project.create({ ownerId: owner._id, title: 'Private project', targetMassG: 100 })
  const token = generateToken(admin)
  assert.equal((await request(app).get(`/api/v1/projects/${project._id}`).auth(token, { type: 'bearer' })).status, 200)
  assert.equal((await request(app).patch(`/api/v1/projects/${project._id}`).auth(token, { type: 'bearer' }).send({ title: 'Changed' })).status, 404)
  assert.equal((await request(app).post('/api/v1/auth/logout').auth(token, { type: 'bearer' })).status, 200)
  assert.equal((await request(app).get('/api/v1/auth/me').auth(token, { type: 'bearer' })).status, 401)
})

it('project deletion removes its comparison history', async () => {
  const owner = await User.create({ email: 'delete@review.example', passwordHash: 'unused', role: 'student' })
  const project = await Project.create({ ownerId: owner._id, title: 'Disposable', targetMassG: 100 })
  await OptimizationRun.create({ projectId: project._id, createdBy: owner._id, routeSnapshots: [], target: { targetMassG: 100 }, status: 'failed' })
  assert.equal((await request(app).delete(`/api/v1/projects/${project._id}`).auth(generateToken(owner), { type: 'bearer' })).status, 200)
  assert.equal(await OptimizationRun.countDocuments({ projectId: project._id }), 0)
})

it('a Python outage records a failed comparison and preserves the draft', async t => {
  const owner = await User.create({ email: 'outage@review.example', passwordHash: 'unused', role: 'student' })
  const project = await Project.create({ ownerId: owner._id, title: 'Outage demo', targetMassG: 100 })
  const route = await Route.create({ ownerId: owner._id, projectId: project._id, steps: [] })
  const original = fastApiClient.getRoute
  fastApiClient.getRoute = async () => { throw new FastAPIServiceError('Python unavailable', 502) }
  t.after(() => { fastApiClient.getRoute = original })
  const response = await request(app).post(`/api/v1/projects/${project._id}/comparisons`).auth(generateToken(owner), { type: 'bearer' }).send({ routeIds: [String(route._id), 'route-unavailable'] })
  assert.equal(response.status, 502)
  assert.ok(await Route.findById(route._id))
  assert.equal((await OptimizationRun.findOne({ projectId: project._id }))?.status, 'failed')
})

it('generated comparison rejects a different batch basis', async t => {
  const owner = await User.create({ email: 'basis@review.example', passwordHash: 'unused', role: 'student' })
  const project = await Project.create({ ownerId: owner._id, title: 'Batch demo', targetCompoundId: 'DEMO', targetMassG: 1000 })
  const route = await Route.create({ ownerId: owner._id, projectId: project._id, steps: [] })
  const original = fastApiClient.getRoute
  fastApiClient.getRoute = async () => ({ target_compound_id: 'DEMO', target_mass_g: 100, base_currency: 'USD' }) as any
  t.after(() => { fastApiClient.getRoute = original })
  const response = await request(app).post(`/api/v1/projects/${project._id}/comparisons`).auth(generateToken(owner), { type: 'bearer' }).send({ routeIds: [String(route._id), 'route-wrong-mass'] })
  assert.equal(response.status, 400)
  assert.equal(await OptimizationRun.countDocuments({ projectId: project._id }), 0)
})
