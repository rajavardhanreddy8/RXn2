import '../src/config.js'
import bcrypt from 'bcryptjs'
import { connectMongo, disconnectMongo } from '../src/db.js'
import { User } from '../src/models/User.js'

const email = process.env.SYNTHAI_ADMIN_EMAIL?.trim().toLowerCase()
const password = process.env.SYNTHAI_ADMIN_PASSWORD
if (!email || !password || password.length < 12) {
  throw new Error('Set SYNTHAI_ADMIN_EMAIL and a SYNTHAI_ADMIN_PASSWORD of at least 12 characters')
}
await connectMongo()
try {
  await User.findOneAndUpdate({ email }, { email, passwordHash: await bcrypt.hash(password, 10), role: 'admin' }, { upsert: true })
  console.log('Course reviewer account created; sign in to view all projects')
} finally {
  await disconnectMongo()
}
