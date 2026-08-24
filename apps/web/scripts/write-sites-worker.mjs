import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = new URL('../dist/', import.meta.url)
const rootPath = fileURLToPath(root)
const assets = {}
const contentType = (path) => path.endsWith('.js') ? 'text/javascript; charset=utf-8'
  : path.endsWith('.css') ? 'text/css; charset=utf-8'
    : 'text/html; charset=utf-8'

async function collect(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const full = join(directory, entry.name)
    if (entry.isDirectory()) { if (entry.name !== '.openai' && entry.name !== 'server') await collect(full); continue }
    const path = `/${relative(rootPath, full).replaceAll('\\', '/')}`
    assets[path] = { body: await readFile(full, 'utf8'), contentType: contentType(path) }
  }
}

await collect(rootPath)
// Embed the small Vite bundle so Sites does not need a separate static-assets
// binding. This also makes every SPA route work from a single Worker module.
const source = `const assets = ${JSON.stringify(assets)};
export default {
  async fetch(request) {
    const url = new URL(request.url)
    const path = url.pathname === '/' || !url.pathname.includes('.') ? '/index.html' : url.pathname
    const asset = assets[path]
    return asset ? new Response(asset.body, { headers: { 'content-type': asset.contentType } }) : new Response('Not found', { status: 404 })
  },
}\n`

await mkdir(new URL('../dist/server/', import.meta.url), { recursive: true })
await writeFile(new URL('../dist/server/index.js', import.meta.url), source)
