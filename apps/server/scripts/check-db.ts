import '../src/config.js'
import { connectMongo, disconnectMongo } from '../src/db.js'

try {
  const mongo = await connectMongo()
  await mongo.connection.db!.command({ ping: 1 })
  console.log('Database connection and authenticated ping passed.')
} catch (error) {
  // Never print connection errors verbatim: they can contain URI credentials.
  const name = error instanceof Error ? error.name : 'DatabaseError'
  console.error(`Database check failed (${name}). Check the private URI, database user permissions, and Atlas network access list.`)
  process.exitCode = 1
} finally {
  await disconnectMongo()
}
