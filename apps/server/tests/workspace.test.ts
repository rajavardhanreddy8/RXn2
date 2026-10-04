import { describe, it, before, after, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import request from 'supertest'
import { MongoMemoryServer } from 'mongodb-memory-server'
import mongoose from 'mongoose'
import { app } from '../src/index.js'
import { User } from '../src/models/User.js'
import { Project } from '../src/models/Project.js'
import { Route } from '../src/models/Route.js'

describe('Phase 2 - Project and Route Workspace API', () => {
  let mongoServer: MongoMemoryServer
  let userAToken: string
  let userBToken: string

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
    await Route.deleteMany({})
    await Project.deleteMany({})
    await User.deleteMany({})

    // Create User A
    const resA = await request(app).post('/api/v1/auth/register').send({
      email: 'userA@example.com',
      password: 'passwordA123',
      name: 'User A',
    })
    userAToken = resA.body.token

    // Create User B
    const resB = await request(app).post('/api/v1/auth/register').send({
      email: 'userB@example.com',
      password: 'passwordB123',
      name: 'User B',
    })
    userBToken = resB.body.token
  })

  describe('Project Management', () => {
    it('creates a project with valid fields and assigns owner from session', async () => {
      const res = await request(app)
        .post('/api/v1/projects')
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          title: 'Synthesis of Apixaban',
          targetCompoundId: 'BENCH-APIXABAN',
          targetCompoundName: 'Apixaban',
          targetMassG: 500,
          targetMassUnit: 'g',
          notes: 'Targeting 500g pilot batch scale',
        })

      assert.equal(res.status, 201)
      assert.equal(res.body.project.title, 'Synthesis of Apixaban')
      assert.equal(res.body.project.targetMassG, 500)
      assert.equal(res.body.project.targetMassUnit, 'g')
      assert.ok(res.body.project.ownerId)
      assert.ok(res.body.project.createdAt)
      assert.ok(res.body.project.updatedAt)
    })

    it('rejects project creation with empty title', async () => {
      const res = await request(app)
        .post('/api/v1/projects')
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          title: '   ',
          targetMassG: 100,
        })

      assert.equal(res.status, 400)
      assert.equal(res.body.error, 'Validation failed')
    })

    it('rejects project creation with non-positive mass', async () => {
      const res = await request(app)
        .post('/api/v1/projects')
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          title: 'Invalid Mass Project',
          targetMassG: -5,
        })

      assert.equal(res.status, 400)
      assert.equal(res.body.error, 'Validation failed')
    })

    it('rejects project creation with invalid mass unit', async () => {
      const res = await request(app)
        .post('/api/v1/projects')
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          title: 'Invalid Unit Project',
          targetMassG: 10,
          targetMassUnit: 'pounds',
        })

      assert.equal(res.status, 400)
      assert.equal(res.body.error, 'Validation failed')
    })

    it('lists only the authenticated user’s projects', async () => {
      // User A creates 2 projects
      await request(app)
        .post('/api/v1/projects')
        .set('Authorization', `Bearer ${userAToken}`)
        .send({ title: 'User A Project 1', targetMassG: 100 })

      await request(app)
        .post('/api/v1/projects')
        .set('Authorization', `Bearer ${userAToken}`)
        .send({ title: 'User A Project 2', targetMassG: 200 })

      // User B creates 1 project
      await request(app)
        .post('/api/v1/projects')
        .set('Authorization', `Bearer ${userBToken}`)
        .send({ title: 'User B Project 1', targetMassG: 300 })

      // User A retrieves list
      const resA = await request(app)
        .get('/api/v1/projects')
        .set('Authorization', `Bearer ${userAToken}`)

      assert.equal(resA.status, 200)
      assert.equal(resA.body.projects.length, 2)
      assert.equal(resA.body.total, 2)

      // User B retrieves list
      const resB = await request(app)
        .get('/api/v1/projects')
        .set('Authorization', `Bearer ${userBToken}`)

      assert.equal(resB.status, 200)
      assert.equal(resB.body.projects.length, 1)
      assert.equal(resB.body.projects[0].title, 'User B Project 1')
    })

    it('retrieves single project by ID and updates project', async () => {
      const created = await request(app)
        .post('/api/v1/projects')
        .set('Authorization', `Bearer ${userAToken}`)
        .send({ title: 'Original Title', targetMassG: 100 })

      const projectId = created.body.project._id

      // Get project
      const getRes = await request(app)
        .get(`/api/v1/projects/${projectId}`)
        .set('Authorization', `Bearer ${userAToken}`)

      assert.equal(getRes.status, 200)
      assert.equal(getRes.body.project.title, 'Original Title')

      // Update project
      const patchRes = await request(app)
        .patch(`/api/v1/projects/${projectId}`)
        .set('Authorization', `Bearer ${userAToken}`)
        .send({ title: 'Updated Title', targetMassG: 250, notes: 'New notes' })

      assert.equal(patchRes.status, 200)
      assert.equal(patchRes.body.project.title, 'Updated Title')
      assert.equal(patchRes.body.project.targetMassG, 250)
      assert.equal(patchRes.body.project.notes, 'New notes')
    })

    it('cascade deletes project and its associated child routes', async () => {
      const proj = await request(app)
        .post('/api/v1/projects')
        .set('Authorization', `Bearer ${userAToken}`)
        .send({ title: 'Project with Routes', targetMassG: 100 })

      const projectId = proj.body.project._id

      // Create 2 routes under this project
      await request(app)
        .post(`/api/v1/projects/${projectId}/routes`)
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          origin: 'manual',
          steps: [{ position: 1, name: 'Step 1' }],
        })

      await request(app)
        .post(`/api/v1/projects/${projectId}/routes`)
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          origin: 'manual',
          steps: [{ position: 1, name: 'Step A' }],
        })

      // Verify routes exist
      const beforeRoutes = await Route.find({ projectId })
      assert.equal(beforeRoutes.length, 2)

      // Delete project
      const delRes = await request(app)
        .delete(`/api/v1/projects/${projectId}`)
        .set('Authorization', `Bearer ${userAToken}`)

      assert.equal(delRes.status, 200)
      assert.equal(delRes.body.deletedRoutesCount, 2)

      // Verify project is deleted
      const projectAfter = await Project.findById(projectId)
      assert.equal(projectAfter, null)

      // Verify child routes are also deleted
      const afterRoutes = await Route.find({ projectId })
      assert.equal(afterRoutes.length, 0)
    })
  })

  describe('Route and Step Management', () => {
    let projectId: string

    beforeEach(async () => {
      const p = await request(app)
        .post('/api/v1/projects')
        .set('Authorization', `Bearer ${userAToken}`)
        .send({ title: 'Workspace Project', targetMassG: 1000 })
      projectId = p.body.project._id
    })

    it('creates a manual route with ordered steps and default values', async () => {
      const res = await request(app)
        .post(`/api/v1/projects/${projectId}/routes`)
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          origin: 'manual',
          steps: [
            {
              position: 1,
              name: 'Amide Coupling',
              reactants: ['DEMO-START-A', 'DEMO-START-C'],
              inputLabels: ['Benzoic Acid', 'Ammonia'],
              productLabel: 'Benzamide',
              yieldPercent: 88.5,
              durationHours: 4.5,
              durationUnit: 'h',
              solvent: 'Dichloromethane',
              conditions: 'Room temperature, 1 atm',
              hazardNotes: 'Mild exotherm',
              evidenceRef: 'US-2024-001234',
              dataSource: 'manual_entry',
            },
          ],
        })

      assert.equal(res.status, 201)
      assert.equal(res.body.route.projectId, projectId)
      assert.equal(res.body.route.origin, 'manual')
      assert.equal(res.body.route.revision, 1)
      assert.equal(res.body.route.steps.length, 1)
      assert.equal(res.body.route.steps[0].yieldPercent, 88.5)
      assert.equal(res.body.route.steps[0].durationHours, 4.5)
    })

    it('allows incomplete draft steps with null/missing scientific fields', async () => {
      const res = await request(app)
        .post(`/api/v1/projects/${projectId}/routes`)
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          origin: 'manual',
          steps: [
            {
              position: 1,
              name: 'Draft Step with Unknown Yield',
              reactants: ['START-A'],
              yieldPercent: null,
              durationHours: null,
            },
          ],
        })

      assert.equal(res.status, 201)
      assert.equal(res.body.route.steps[0].yieldPercent, null)
      assert.equal(res.body.route.steps[0].durationHours, null)
      assert.equal(res.body.route.steps[0].name, 'Draft Step with Unknown Yield')
    })

    it('rejects yield percentage outside 0-100', async () => {
      const resOver = await request(app)
        .post(`/api/v1/projects/${projectId}/routes`)
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          origin: 'manual',
          steps: [{ position: 1, yieldPercent: 105 }],
        })
      assert.equal(resOver.status, 400)
      assert.equal(resOver.body.error, 'Validation failed')

      const resUnder = await request(app)
        .post(`/api/v1/projects/${projectId}/routes`)
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          origin: 'manual',
          steps: [{ position: 1, yieldPercent: -10 }],
        })
      assert.equal(resUnder.status, 400)
    })

    it('rejects negative duration', async () => {
      const res = await request(app)
        .post(`/api/v1/projects/${projectId}/routes`)
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          origin: 'manual',
          steps: [{ position: 1, durationHours: -2 }],
        })
      assert.equal(res.status, 400)
      assert.equal(res.body.error, 'Validation failed')
    })

    it('updates route, reorders steps, and increments revision number', async () => {
      const created = await request(app)
        .post(`/api/v1/projects/${projectId}/routes`)
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          origin: 'manual',
          steps: [
            { position: 1, name: 'Step 1' },
            { position: 2, name: 'Step 2' },
          ],
        })

      const routeId = created.body.route._id
      assert.equal(created.body.route.revision, 1)

      // Update and reorder steps (Step 2 becomes position 1, Step 1 becomes position 2)
      const updateRes = await request(app)
        .patch(`/api/v1/projects/${projectId}/routes/${routeId}`)
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          steps: [
            { position: 1, name: 'Step 2 (Reordered to 1)' },
            { position: 2, name: 'Step 1 (Reordered to 2)' },
            { position: 3, name: 'New Step 3' },
          ],
        })

      assert.equal(updateRes.status, 200)
      assert.equal(updateRes.body.route.revision, 2)
      assert.equal(updateRes.body.route.steps.length, 3)
      assert.equal(updateRes.body.route.steps[0].name, 'Step 2 (Reordered to 1)')
      assert.equal(updateRes.body.route.steps[1].name, 'Step 1 (Reordered to 2)')
      assert.equal(updateRes.body.route.steps[2].name, 'New Step 3')
    })

    it('deletes an individual route', async () => {
      const created = await request(app)
        .post(`/api/v1/projects/${projectId}/routes`)
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          origin: 'manual',
          steps: [{ position: 1, name: 'To be deleted' }],
        })

      const routeId = created.body.route._id

      const delRes = await request(app)
        .delete(`/api/v1/projects/${projectId}/routes/${routeId}`)
        .set('Authorization', `Bearer ${userAToken}`)

      assert.equal(delRes.status, 200)
      assert.equal(delRes.body.deletedRouteId, routeId)

      const getRes = await request(app)
        .get(`/api/v1/projects/${projectId}/routes/${routeId}`)
        .set('Authorization', `Bearer ${userAToken}`)

      assert.equal(getRes.status, 404)
    })
  })

  describe('Cross-User Ownership Security', () => {
    let projectAId: string
    let routeAId: string

    beforeEach(async () => {
      // User A creates project and route
      const p = await request(app)
        .post('/api/v1/projects')
        .set('Authorization', `Bearer ${userAToken}`)
        .send({ title: 'User A Secret Project', targetMassG: 500 })

      projectAId = p.body.project._id

      const r = await request(app)
        .post(`/api/v1/projects/${projectAId}/routes`)
        .set('Authorization', `Bearer ${userAToken}`)
        .send({
          origin: 'manual',
          steps: [{ position: 1, name: 'Secret Route' }],
        })

      routeAId = r.body.route._id
    })

    it('denies User B from reading User A’s project (returns 404 without leakage)', async () => {
      const res = await request(app)
        .get(`/api/v1/projects/${projectAId}`)
        .set('Authorization', `Bearer ${userBToken}`)

      assert.equal(res.status, 404)
      assert.equal(res.body.error, 'Project not found')
    })

    it('denies User B from modifying User A’s project', async () => {
      const res = await request(app)
        .patch(`/api/v1/projects/${projectAId}`)
        .set('Authorization', `Bearer ${userBToken}`)
        .send({ title: 'Hacked Project' })

      assert.equal(res.status, 404)
      assert.equal(res.body.error, 'Project not found')

      // Confirm User A's project remains untouched
      const original = await Project.findById(projectAId)
      assert.equal(original?.title, 'User A Secret Project')
    })

    it('denies User B from deleting User A’s project', async () => {
      const res = await request(app)
        .delete(`/api/v1/projects/${projectAId}`)
        .set('Authorization', `Bearer ${userBToken}`)

      assert.equal(res.status, 404)
      assert.equal(res.body.error, 'Project not found')

      // Confirm project still exists
      const original = await Project.findById(projectAId)
      assert.ok(original)
    })

    it('denies User B from reading, creating, or modifying routes on User A’s project', async () => {
      // GET routes
      const getRoutes = await request(app)
        .get(`/api/v1/projects/${projectAId}/routes`)
        .set('Authorization', `Bearer ${userBToken}`)
      assert.equal(getRoutes.status, 404)

      // POST route
      const postRoute = await request(app)
        .post(`/api/v1/projects/${projectAId}/routes`)
        .set('Authorization', `Bearer ${userBToken}`)
        .send({ steps: [{ position: 1, name: 'Unauthorized Step' }] })
      assert.equal(postRoute.status, 404)

      // PATCH route
      const patchRoute = await request(app)
        .patch(`/api/v1/projects/${projectAId}/routes/${routeAId}`)
        .set('Authorization', `Bearer ${userBToken}`)
        .send({ origin: 'generated' })
      assert.equal(patchRoute.status, 404)

      // DELETE route
      const delRoute = await request(app)
        .delete(`/api/v1/projects/${projectAId}/routes/${routeAId}`)
        .set('Authorization', `Bearer ${userBToken}`)
      assert.equal(delRoute.status, 404)
    })
  })
})
