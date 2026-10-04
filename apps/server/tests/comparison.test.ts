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
import { fastApiClient, FastAPIServiceError } from '../src/services/fastapiClient.js'
import { normalizeRouteMetrics, buildRouteSnapshot } from '../src/services/comparisonService.js'

describe('Phase 3 — FastAPI Bridge and Route Comparison API', () => {
  let mongoServer: MongoMemoryServer
  let userToken: string
  let userBToken: string
  let projectId: string
  let route1Id: string
  let route2Id: string

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
    const resA = await request(app).post('/api/v1/auth/register').send({
      email: 'chemistA@example.com',
      password: 'password123',
      name: 'Chemist A',
    })
    userToken = resA.body.token

    // Create User B
    const resB = await request(app).post('/api/v1/auth/register').send({
      email: 'chemistB@example.com',
      password: 'password123',
      name: 'Chemist B',
    })
    userBToken = resB.body.token

    // Create Project for User A
    const proj = await request(app)
      .post('/api/v1/projects')
      .set('Authorization', `Bearer ${userToken}`)
      .send({
        title: 'Demo Benzamide Workspace',
        targetCompoundId: 'DEMO-TARGET-1',
        targetCompoundName: 'Demo benzamide target',
        targetMassG: 1000,
        targetMassUnit: 'g',
      })
    projectId = proj.body.project._id

    // Create 2 manual routes for User A
    const r1 = await request(app)
      .post(`/api/v1/projects/${projectId}/routes`)
      .set('Authorization', `Bearer ${userToken}`)
      .send({
        origin: 'manual',
        steps: [
          {
            position: 1,
            name: 'Step 1: Benzoic Acid + Ammonia',
            reactants: ['DEMO-START-A', 'DEMO-START-C'],
            productLabel: 'DEMO-TARGET-1',
            yieldPercent: 84.0,
            durationHours: 4.0,
            durationUnit: 'h',
            solvent: 'DCM',
            conditions: 'rt',
            hazardNotes: 'Mild exotherm',
            evidenceRef: 'US-DEMO-01',
          },
        ],
      })
    route1Id = r1.body.route._id

    const r2 = await request(app)
      .post(`/api/v1/projects/${projectId}/routes`)
      .set('Authorization', `Bearer ${userToken}`)
      .send({
        origin: 'manual',
        steps: [
          {
            position: 1,
            name: 'Step 1: Benzoyl Chloride + Ammonia',
            reactants: ['DEMO-START-B', 'DEMO-START-C'],
            productLabel: 'DEMO-TARGET-1',
            yieldPercent: 91.0,
            durationHours: 2.5,
            durationUnit: 'h',
            solvent: 'THF',
            conditions: '0°C to rt',
            hazardNotes: 'Corrosive acid chloride',
            evidenceRef: 'US-DEMO-02',
          },
        ],
      })
    route2Id = r2.body.route._id
  })

  describe('Metric Normalization Rules (No Fabrication)', () => {
    it('5. handles missing yield by marking status partial/unknown without substituting 0', () => {
      // Step 1 has 80% yield, Step 2 has null (unknown) yield
      const metrics = normalizeRouteMetrics(
        'manual-1',
        'manual',
        [
          {
            position: 1,
            name: 'Step 1',
            reactants: [],
            inputLabels: [],
            productLabel: 'Inter 1',
            yieldPercent: 80.0,
            durationHours: 2.0,
            durationUnit: 'h',
            solvent: '',
            conditions: '',
            hazardNotes: '',
            evidenceRef: '',
            dataSource: 'manual',
          },
          {
            position: 2,
            name: 'Step 2',
            reactants: [],
            inputLabels: [],
            productLabel: 'Final',
            yieldPercent: null,
            durationHours: 3.0,
            durationUnit: 'h',
            solvent: '',
            conditions: '',
            hazardNotes: '',
            evidenceRef: '',
            dataSource: 'manual',
          },
        ],
        'USD'
      )

      assert.equal(metrics.yield.value, null)
      assert.equal(metrics.yield.status, 'partial')
      assert.match(metrics.yield.warnings[0], /missing yield data/i)
      assert.match(metrics.yield.warnings[0], /never substituted with 0/i)
    })

    it('6. handles missing duration without fabricating total sum', () => {
      const metrics = normalizeRouteMetrics(
        'manual-2',
        'manual',
        [
          {
            position: 1,
            name: 'Step 1',
            reactants: [],
            inputLabels: [],
            productLabel: 'Inter 1',
            yieldPercent: 90.0,
            durationHours: 5.0,
            durationUnit: 'h',
            solvent: '',
            conditions: '',
            hazardNotes: '',
            evidenceRef: '',
            dataSource: 'manual',
          },
          {
            position: 2,
            name: 'Step 2',
            reactants: [],
            inputLabels: [],
            productLabel: 'Final',
            yieldPercent: 85.0,
            durationHours: null,
            durationUnit: 'h',
            solvent: '',
            conditions: '',
            hazardNotes: '',
            evidenceRef: '',
            dataSource: 'manual',
          },
        ],
        'USD'
      )

      assert.equal(metrics.duration.value, null)
      assert.equal(metrics.duration.status, 'partial')
      assert.match(metrics.duration.warnings[0], /missing duration/i)
    })

    it('7. preserves cost completeness warning when actual cost is incomplete', () => {
      const metrics = normalizeRouteMetrics(
        'gen-1',
        'generated',
        [],
        'USD',
        {
          actual_material_cost: 45.5,
          actual_cost_coverage: 0.65, // < 80% coverage
          currency: 'USD',
          warnings: ['Missing quotes for 1 starting material'],
        }
      )

      assert.equal(metrics.cost.status, 'partial')
      assert.ok(metrics.cost.warnings.some((w) => w.includes('Partial material estimate')))
    })

    it('8. marks safety as unknown when steps lack hazard notes without claiming safe', () => {
      const metrics = normalizeRouteMetrics(
        'manual-3',
        'manual',
        [
          {
            position: 1,
            name: 'Step 1',
            reactants: [],
            inputLabels: [],
            productLabel: 'Final',
            yieldPercent: 95.0,
            durationHours: 1.0,
            durationUnit: 'h',
            solvent: '',
            conditions: '',
            hazardNotes: '', // Empty
            evidenceRef: '',
            dataSource: 'manual',
          },
        ],
        'USD'
      )

      assert.equal(metrics.safety.status, 'unknown')
      assert.equal(metrics.safety.value, 'Unreviewed safety profile')
      assert.match(metrics.safety.warnings[0], /cannot be assumed safe/i)
    })
  })

  describe('FastAPI Bridge and Error Mapping', () => {
    it('1. proxies target resolution through server-side client when FastAPI is reachable', async () => {
      // Mock fastApiClient.resolveTarget
      const original = fastApiClient.resolveTarget
      fastApiClient.resolveTarget = async (query: string) => ({
        resolved: true,
        target: {
          compound_id: 'DEMO-TARGET-1',
          preferred_name: 'Demo benzamide target',
          smiles: 'NC(=O)c1ccccc1',
          molecular_weight: 121.139,
        },
        reviewed_producing_reactions: 2,
      })

      const res = await request(app)
        .post('/api/v1/targets/resolve')
        .set('Authorization', `Bearer ${userToken}`)
        .send({ query: 'Demo benzamide target' })

      fastApiClient.resolveTarget = original

      assert.equal(res.status, 200)
      assert.equal(res.body.resolved, true)
      assert.equal(res.body.target.compound_id, 'DEMO-TARGET-1')
    })

    it('4. preserves coverage_gap when FastAPI returns no complete reviewed route', async () => {
      const original = fastApiClient.generateRoutes
      fastApiClient.generateRoutes = async () => ({
        target: { compound_id: 'BENCH-UNKNOWN', preferred_name: 'Unknown Target' },
        routes: [],
        coverage_gap: true,
        message: 'No complete evidence-bounded route connects this target to reviewed starting materials.',
      })

      const res = await request(app)
        .post('/api/v1/routes/generate')
        .set('Authorization', `Bearer ${userToken}`)
        .send({ compound_id: 'BENCH-UNKNOWN', target_mass_g: 1000 })

      fastApiClient.generateRoutes = original

      assert.equal(res.status, 200)
      assert.equal(res.body.coverage_gap, true)
      assert.equal(res.body.routes.length, 0)
      assert.match(res.body.message, /No complete evidence-bounded route/i)
    })

    it('2 & 3. maps FastAPI timeout and connection failures safely into 504 and 502', async () => {
      // Test timeout (504)
      const original = fastApiClient.resolveTarget
      fastApiClient.resolveTarget = async () => {
        throw new FastAPIServiceError('Request timed out after 5000ms', 504)
      }

      const timeoutRes = await request(app)
        .post('/api/v1/targets/resolve')
        .set('Authorization', `Bearer ${userToken}`)
        .send({ query: 'Apixaban' })

      assert.equal(timeoutRes.status, 504)
      assert.match(timeoutRes.body.error, /timed out/i)

      // Test connection failure (502)
      fastApiClient.resolveTarget = async () => {
        throw new FastAPIServiceError('FastAPI chemistry service is currently unavailable', 502)
      }

      const connRes = await request(app)
        .post('/api/v1/targets/resolve')
        .set('Authorization', `Bearer ${userToken}`)
        .send({ query: 'Apixaban' })

      fastApiClient.resolveTarget = original

      assert.equal(connRes.status, 502)
      assert.match(connRes.body.error, /unavailable/i)
    })
  })

  describe('Route Comparison and Snapshot Immutability', () => {
    it('9 & 11. compares 2 manual routes and saves completed optimizationRun snapshot', async () => {
      const res = await request(app)
        .post(`/api/v1/projects/${projectId}/comparisons`)
        .set('Authorization', `Bearer ${userToken}`)
        .send({
          routeIds: [route1Id, route2Id],
          baseCurrency: 'USD',
        })

      assert.equal(res.status, 201)
      const comp = res.body.comparison
      assert.equal(comp.status, 'completed')
      assert.equal(comp.routeSnapshots.length, 2)
      assert.equal(comp.routeSnapshots[0].routeId, route1Id)
      assert.equal(comp.routeSnapshots[0].origin, 'manual')
      assert.equal(comp.routeSnapshots[0].metrics.yield.value, 84.0)
      assert.equal(comp.routeSnapshots[0].metrics.yield.status, 'complete')
      assert.equal(comp.routeSnapshots[1].metrics.yield.value, 91.0)
      assert.equal(comp.routeSnapshots[1].metrics.yield.status, 'complete')

      // Verify record saved in MongoDB
      const saved = await OptimizationRun.findById(comp._id)
      assert.ok(saved)
      assert.equal(saved.routeSnapshots.length, 2)
    })

    it('10. compares authoritative generated routes alongside manual routes', async (t) => {
      const generatedMock = {
        route_id: 'route-gen-99',
        target_compound_id: 'DEMO-TARGET-1',
        target_mass_g: 1000,
        base_currency: 'USD',
        step_count: 1,
        steps: [
          {
            reaction_id: 'DEMO-RXN-A',
            reaction_name: 'Synthetic fixture route A',
            transformation_key: 'DEMO-AMIDE-A',
            product_compound_id: 'DEMO-TARGET-1',
            yield_percent: 84.0,
            demonstrated_scale_g: 5000.0,
            confidence: 0.95,
            is_synthetic: true,
            evidence: { label: 'Synthetic fixture' },
            inputs: [{ compound_id: 'DEMO-START-A', preferred_name: 'Benzoic Acid' }],
          },
        ],
        evaluation: {
          actual_material_cost: 40.0,
          actual_cost_coverage: 1.0,
          feasibility_score: 84.75,
          currency: 'USD',
          warnings: [],
        },
      }

      const originalGetRoute = fastApiClient.getRoute
      fastApiClient.getRoute = async () => generatedMock as any
      t.after(() => { fastApiClient.getRoute = originalGetRoute })
      const res = await request(app)
        .post(`/api/v1/projects/${projectId}/comparisons`)
        .set('Authorization', `Bearer ${userToken}`)
        .send({
          routeIds: [route1Id, 'route-gen-99'],
          generatedRoutes: [{ ...generatedMock, evaluation: { actual_material_cost: 1 } }],
          baseCurrency: 'USD',
        })

      assert.equal(res.status, 201)
      const comp = res.body.comparison
      assert.equal(comp.routeSnapshots.length, 2)
      assert.equal(comp.routeSnapshots[0].origin, 'manual')
      assert.equal(comp.routeSnapshots[1].origin, 'generated')
      assert.equal(comp.routeSnapshots[1].metrics.cost.value, 40.0)
      assert.equal(comp.routeSnapshots[1].metrics.cost.status, 'complete')
    })

    it('12. enforces route count boundary: minimum 2 and maximum 10 routes', async () => {
      // Less than 2 routes
      const resUnder = await request(app)
        .post(`/api/v1/projects/${projectId}/comparisons`)
        .set('Authorization', `Bearer ${userToken}`)
        .send({ routeIds: [route1Id] })
      assert.equal(resUnder.status, 400)

      // More than 10 routes
      const resOver = await request(app)
        .post(`/api/v1/projects/${projectId}/comparisons`)
        .set('Authorization', `Bearer ${userToken}`)
        .send({
          routeIds: Array.from({ length: 11 }, () => route1Id),
        })
      assert.equal(resOver.status, 400)
    })

    it('13 & 14. guarantees snapshot immutability: editing a route after comparison does NOT alter the saved comparison snapshot', async () => {
      // 1. Perform initial comparison
      const compRes = await request(app)
        .post(`/api/v1/projects/${projectId}/comparisons`)
        .set('Authorization', `Bearer ${userToken}`)
        .send({ routeIds: [route1Id, route2Id] })

      const comparisonId = compRes.body.comparison._id
      const initialStep1Name = compRes.body.comparison.routeSnapshots[0].steps[0].name
      const initialYield = compRes.body.comparison.routeSnapshots[0].metrics.yield.value

      // 2. Modify Route 1 drastically in project workspace
      await request(app)
        .patch(`/api/v1/projects/${projectId}/routes/${route1Id}`)
        .set('Authorization', `Bearer ${userToken}`)
        .send({
          steps: [
            {
              position: 1,
              name: 'Drastically Altered Reaction Step',
              yieldPercent: 20.0,
            },
          ],
        })

      // 3. Re-fetch the saved comparison snapshot
      const getCompRes = await request(app)
        .get(`/api/v1/projects/${projectId}/comparisons/${comparisonId}`)
        .set('Authorization', `Bearer ${userToken}`)

      assert.equal(getCompRes.status, 200)
      const snapshot = getCompRes.body.comparison.routeSnapshots[0]

      // Verify the snapshot retains original unmodified data
      assert.equal(snapshot.steps[0].name, initialStep1Name)
      assert.equal(snapshot.metrics.yield.value, initialYield)
      assert.equal(snapshot.steps[0].yieldPercent, 84.0) // Unchanged from initial
    })

    it('15. records failed comparison run on failure while preserving all existing project routes', async () => {
      const beforeRoutes = await Route.find({ projectId })
      assert.equal(beforeRoutes.length, 2)

      // Attempt comparison with non-existent route IDs
      const failRes = await request(app)
        .post(`/api/v1/projects/${projectId}/comparisons`)
        .set('Authorization', `Bearer ${userToken}`)
        .send({
          routeIds: [new mongoose.Types.ObjectId().toString(), new mongoose.Types.ObjectId().toString()],
        })

      assert.equal(failRes.status, 400)

      // Confirm failed optimization run is saved
      const failedRun = await OptimizationRun.findOne({ projectId, status: 'failed' })
      assert.ok(failedRun)
      assert.equal(failedRun.status, 'failed')

      // Confirm project routes are completely intact and never erased
      const afterRoutes = await Route.find({ projectId })
      assert.equal(afterRoutes.length, 2)
    })

    it('isolates comparisons between users (User B cannot access User A’s comparison)', async () => {
      const compRes = await request(app)
        .post(`/api/v1/projects/${projectId}/comparisons`)
        .set('Authorization', `Bearer ${userToken}`)
        .send({ routeIds: [route1Id, route2Id] })

      const comparisonId = compRes.body.comparison._id

      // User B tries to get User A's comparison
      const resB = await request(app)
        .get(`/api/v1/projects/${projectId}/comparisons/${comparisonId}`)
        .set('Authorization', `Bearer ${userBToken}`)

      assert.equal(resB.status, 404)
    })
  })
})
