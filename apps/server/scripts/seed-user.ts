import '../src/config.js'
import bcrypt from 'bcryptjs'
import { connectMongo, disconnectMongo } from '../src/db.js'
import { User } from '../src/models/User.js'

const email = process.env.SYNTHAI_USER_EMAIL?.trim().toLowerCase()
const password = process.env.SYNTHAI_USER_PASSWORD
const name = process.env.SYNTHAI_USER_NAME?.trim()

if (!email || !password || password.length < 12) {
  throw new Error('Set SYNTHAI_USER_EMAIL and a SYNTHAI_USER_PASSWORD of at least 12 characters')
}

await connectMongo()
try {
  await User.findOneAndUpdate(
    { email },
    { email, passwordHash: await bcrypt.hash(password, 10), name, role: 'student' },
    { upsert: true },
  )
  console.log('SynthAI user account provisioned')
} finally {
  await disconnectMongo()
}
