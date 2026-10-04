import mongoose from 'mongoose'

export async function connectMongo(uri = process.env.MONGODB_URI || 'mongodb://127.0.0.1:27017/synthai') {
  await mongoose.connect(uri, { serverSelectionTimeoutMS: 10000 })
  console.log('Connected to persistent MongoDB')
  return mongoose
}

export async function disconnectMongo() {
  await mongoose.disconnect()
}
