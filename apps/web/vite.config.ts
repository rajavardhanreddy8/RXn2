import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { sites } from '@openai/sites-vite-plugin'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  return {
    plugins: [react(), ...(env.SYNTHAI_HOSTED_BUILD === 'true' ? [sites()] : [])],
    server: {
      port: 5173,
      proxy: {
        '/api/v1': {
          target: env.VITE_EXPRESS_PROXY || 'http://127.0.0.1:4000',
          changeOrigin: true,
        },
        '/v1': {
          target: env.VITE_API_PROXY || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
        '/api': {
          target: env.VITE_API_PROXY || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
  }
})
