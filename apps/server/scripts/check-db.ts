import '../src/config.js'
import { connectMongo, disconnectMongo } from '../src/db.js'

try {
  const mongo = await connectMongo()
  await mongo.connection.db!.command({ ping: 1 })
  console.log('Database connection and authenticated ping passed.')
} catch (error) {
  // Connection details can include the URI, so keep the output limited to a
  // safe error class and a credential-free reason.
  const name = error instanceof Error ? error.name : 'DatabaseError'
  const rawMessage = error instanceof Error ? error.message : ''
  const reason = rawMessage
    .replace(/mongodb(?:\+srv)?:\/\/[^\s]+/gi, '[redacted MongoDB URI]')
    .replace(/\b[A-Za-z0-9._%+-]+:[^@\s]+@/g, '[redacted credentials]@')
  console.error(`Database check failed (${name}): ${reason || 'unknown connection error'}`)
  process.exitCode = 1
} finally {
  await disconnectMongo()
}
