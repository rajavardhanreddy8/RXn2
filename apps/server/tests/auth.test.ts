import { describe, it, before, after, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import request from 'supertest'
import { MongoMemoryServer } from 'mongodb-memory-server'
import mongoose from 'mongoose'
import { app } from '../src/index.js'
import { User } from '../src/models/User.js'

describe('Phase 1 - Authentication API', () => {
  let mongoServer: MongoMemoryServer

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
    await User.deleteMany({})
  })

  it('registers a new user and returns JWT in cookie + body', async () => {
    const res = await request(app)
      .post('/api/v1/auth/register')
      .send({
        email: 'student1@example.com',
        password: 'password123',
        name: 'Student One',
      })

    assert.equal(res.status, 201)
    assert.equal(res.body.user.email, 'student1@example.com')
    assert.equal(res.body.user.role, 'student')
    assert.ok(res.body.token)
    assert.ok(res.headers['set-cookie'])
  })

  it('rejects duplicate email registration with 409', async () => {
    await request(app).post('/api/v1/auth/register').send({
      email: 'student1@example.com',
      password: 'password123',
    })

    const res = await request(app).post('/api/v1/auth/register').send({
      email: 'student1@example.com',
      password: 'differentpassword',
    })

    assert.equal(res.status, 409)
    assert.match(res.body.error, /already registered/i)
  })

  it('rejects registration with short password', async () => {
    const res = await request(app).post('/api/v1/auth/register').send({
      email: 'student1@example.com',
      password: '123',
    })

    assert.equal(res.status, 400)
    assert.equal(res.body.error, 'Validation failed')
  })

  it('blocks public registration outside the approved provisioning flow', async () => {
    const original = {
      nodeEnv: process.env.NODE_ENV,
      lifecycle: process.env.npm_lifecycle_event,
      nodeTestContext: process.env.NODE_TEST_CONTEXT,
      allowRegistration: process.env.SYNTHAI_ALLOW_REGISTRATION,
    }
    process.env.NODE_ENV = 'production'
    process.env.npm_lifecycle_event = ''
    process.env.NODE_TEST_CONTEXT = ''
    process.env.SYNTHAI_ALLOW_REGISTRATION = 'false'
    try {
      const res = await request(app).post('/api/v1/auth/register').send({
        email: 'blocked@example.com',
        password: 'password123',
      })
      assert.equal(res.status, 403)
    } finally {
      process.env.NODE_ENV = original.nodeEnv
      process.env.npm_lifecycle_event = original.lifecycle
      process.env.NODE_TEST_CONTEXT = original.nodeTestContext
      process.env.SYNTHAI_ALLOW_REGISTRATION = original.allowRegistration
    }
  })

  it('logs in an existing user with valid credentials', async () => {
    await request(app).post('/api/v1/auth/register').send({
      email: 'student1@example.com',
      password: 'password123',
    })

    const res = await request(app).post('/api/v1/auth/login').send({
      email: 'student1@example.com',
      password: 'password123',
    })

    assert.equal(res.status, 200)
    assert.equal(res.body.user.email, 'student1@example.com')
    assert.ok(res.body.token)
  })

  it('rejects login with wrong password with 401', async () => {
    await request(app).post('/api/v1/auth/register').send({
      email: 'student1@example.com',
      password: 'password123',
    })

    const res = await request(app).post('/api/v1/auth/login').send({
      email: 'student1@example.com',
      password: 'wrongpassword',
    })

    assert.equal(res.status, 401)
    assert.match(res.body.error, /Invalid email or password/i)
  })

  it('returns current profile via /api/v1/auth/me when authenticated', async () => {
    const reg = await request(app).post('/api/v1/auth/register').send({
      email: 'student1@example.com',
      password: 'password123',
      name: 'Student One',
    })

    const cookie = reg.headers['set-cookie']

    const res = await request(app)
      .get('/api/v1/auth/me')
      .set('Cookie', cookie)

    assert.equal(res.status, 200)
    assert.equal(res.body.user.email, 'student1@example.com')
    assert.equal(res.body.user.name, 'Student One')
  })

  it('rejects /api/v1/auth/me without authentication with 401', async () => {
    const res = await request(app).get('/api/v1/auth/me')
    assert.equal(res.status, 401)
    assert.match(res.body.error, /Authentication required/i)
  })

  it('clears session cookie on logout', async () => {
    const res = await request(app).post('/api/v1/auth/logout')
    assert.equal(res.status, 200)
    assert.match(res.body.message, /logged out/i)
  })
})
