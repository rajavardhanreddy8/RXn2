import { mkdir, writeFile } from 'node:fs/promises'

// Sites deploys a Cloudflare Worker. The Vite bundle remains static; this tiny
// worker serves the SPA shell for client routes and delegates assets to Sites.
const source = `export default {
  async fetch(request, env) {
    const url = new URL(request.url)
    const isAsset = url.pathname.includes('.')
    if (url.pathname === '/' || !isAsset) {
      return env.ASSETS.fetch(new Request(new URL('/index.html', url), request))
    }
    return env.ASSETS.fetch(request)
  },
}\n`

await mkdir(new URL('../dist/server/', import.meta.url), { recursive: true })
await writeFile(new URL('../dist/server/index.js', import.meta.url), source)
