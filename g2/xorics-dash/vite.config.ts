import { defineConfig, type Plugin } from 'vite'
import fs from 'node:fs'
import path from 'node:path'

/**
 * Xorics Dash dev server (RIDGames).
 *
 * Even Hub whitelists the app's network access, so the WebView never calls the
 * bridge directly. Instead it fetches SAME-ORIGIN and this middleware proxies
 * server-side, where no whitelist applies — the exact pattern xorics-mic-test
 * proved with /ingest:
 *
 *   glasses WebView --same-origin--> vite :5174 --local--> bridge :8090/dashboard/*
 *
 * Routes:
 *   GET  /page/<x>[?query]  ->  GET <bridge>/dashboard/<x>[?query]  (+ bearer)
 *   POST /log               ->  timestamped line in this terminal + dash-events.log
 *
 * Env (all optional):
 *   XORICS_BRIDGE_URL    default http://127.0.0.1:8090
 *   XORICS_BRIDGE_TOKEN  bearer for the bridge (unset bridge accepts anything)
 */
const BRIDGE_URL = (process.env.XORICS_BRIDGE_URL || 'http://127.0.0.1:8090').replace(/\/$/, '')
const BRIDGE_TOKEN = process.env.XORICS_BRIDGE_TOKEN || 'vite'
const EVENT_LOG = path.resolve('dash-events.log')

function logLine(s: string) {
  const line = `${new Date().toISOString()}  ${s}`
  // eslint-disable-next-line no-console
  console.log('[dash] ' + s)
  fs.appendFileSync(EVENT_LOG, line + '\n')
}

function dashProxy(): Plugin {
  return {
    name: 'xorics-dash-proxy',
    configureServer(server) {
      // Same-origin bridge proxy
      server.middlewares.use('/page', (req, res) => {
        void (async () => {
          if (req.method !== 'GET') { res.statusCode = 405; return res.end() }
          // req.url is the part after '/page', e.g. '/nav?to=home'
          const rest = (req.url || '/').replace(/^\/+/, '')
          const target = `${BRIDGE_URL}/dashboard/${rest}`
          try {
            const r = await fetch(target, { headers: { authorization: `Bearer ${BRIDGE_TOKEN}` } })
            const body = await r.text()
            res.statusCode = r.status
            res.setHeader('content-type', r.headers.get('content-type') || 'application/json')
            res.end(body)
            if (!r.ok) logLine(`proxy ${rest} -> HTTP ${r.status}`)
          } catch (e) {
            logLine(`proxy ${rest} -> ERR ${(e as Error).message} (bridge down?)`)
            res.statusCode = 502
            res.setHeader('content-type', 'application/json')
            res.end(JSON.stringify({ lines: ['BRIDGE DOWN', 'start uvicorn :8090'] }))
          }
        })()
      })
      // On-device event log sink
      server.middlewares.use('/log', (req, res) => {
        if (req.method !== 'POST') { res.statusCode = 405; return res.end() }
        const parts: Buffer[] = []
        req.on('data', (c: Buffer) => parts.push(c))
        req.on('end', () => {
          logLine(Buffer.concat(parts).toString('utf8').slice(0, 500))
          res.setHeader('content-type', 'application/json')
          res.end(JSON.stringify({ ok: true }))
        })
      })
    },
  }
}

export default defineConfig({
  plugins: [dashProxy()],
  server: { host: '0.0.0.0', port: 5174 }, // 5174: coexists with mic-test on 5173
})
