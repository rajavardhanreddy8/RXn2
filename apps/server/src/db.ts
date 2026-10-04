import mongoose from 'mongoose'

export async function connectMongo(uri = process.env.MONGODB_URI) {
  if (!uri) throw new Error('Set MONGODB_URI in the private root .env or backend hosting secrets')
  if (!/^mongodb(?:\+srv)?:\/\//.test(uri)) throw new Error('MONGODB_URI must be a MongoDB connection string')
  await mongoose.connect(uri, {
    ...(process.env.MONGODB_DB_NAME ? { dbName: process.env.MONGODB_DB_NAME } : {}),
    serverSelectionTimeoutMS: 10000,
    maxPoolSize: 10,
  })
  console.log('Connected to persistent MongoDB')
  return mongoose
}

export async function disconnectMongo() {
  await mongoose.disconnect()
}
