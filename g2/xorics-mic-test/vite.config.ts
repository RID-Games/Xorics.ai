import { defineConfig, type Plugin } from 'vite'
import fs from 'node:fs'
import path from 'node:path'

// PCM format coming from the glasses
const SAMPLE_RATE = 16000
const CHANNELS = 1
const BITS = 16
const BYTES_PER_SEC = (SAMPLE_RATE * CHANNELS * BITS) / 8 // 32000

// live-transcription windowing
const WINDOW_SECONDS = Number(process.env.XORICS_WINDOW_SECONDS || 6)
const WINDOW_BYTES = WINDOW_SECONDS * BYTES_PER_SEC
const MIN_TAIL_BYTES = BYTES_PER_SEC / 2 // skip <0.5s scraps on finalize
const BRIDGE_STT = process.env.XORICS_STT_URL || 'http://127.0.0.1:8090/stt'

const WAV_OUT = path.resolve('capture.wav')         // full session, debug/raw material
const TRANSCRIPT_OUT = path.resolve('transcript.log') // live transcript

function wavHeader(dataLen: number): Buffer {
  const byteRate = BYTES_PER_SEC
  const blockAlign = (CHANNELS * BITS) / 8
  const b = Buffer.alloc(44)
  b.write('RIFF', 0); b.writeUInt32LE(36 + dataLen, 4); b.write('WAVE', 8)
  b.write('fmt ', 12); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(CHANNELS, 22)
  b.writeUInt32LE(SAMPLE_RATE, 24); b.writeUInt32LE(byteRate, 28); b.writeUInt16LE(blockAlign, 32); b.writeUInt16LE(BITS, 34)
  b.write('data', 36); b.writeUInt32LE(dataLen, 40)
  return b
}

// ---- full-session WAV (placeholder header, patched on finalize) ------------
let fd: number | null = null
let fullBytes = 0
function openWav() { fd = fs.openSync(WAV_OUT, 'w'); fs.writeSync(fd, wavHeader(0)); fullBytes = 0 }
function appendWav(b: Buffer) { if (fd === null) openWav(); fs.writeSync(fd as number, b); fullBytes += b.length }
function finalizeWav() { if (fd === null) return; fs.writeSync(fd as number, wavHeader(fullBytes), 0, 44, 0); fs.closeSync(fd); fd = null }

function logLine(s: string) {
  // eslint-disable-next-line no-console
  console.log('[transcript] ' + s)
  fs.appendFileSync(TRANSCRIPT_OUT, `${new Date().toISOString()}  ${s}\n`)
}

// ---- live windowed transcription via the bridge ----------------------------
let chunks: Buffer[] = []
let queuedBytes = 0
let pumping = false

function takeWindow(): Buffer | null {
  if (queuedBytes < WINDOW_BYTES) return null
  const out: Buffer[] = []
  let need = WINDOW_BYTES
  while (need > 0) {
    const c = chunks[0]
    if (c.length <= need) { out.push(c); need -= c.length; chunks.shift() }
    else { out.push(c.subarray(0, need)); chunks[0] = c.subarray(need); need = 0 }
  }
  queuedBytes -= WINDOW_BYTES
  return Buffer.concat(out)
}

async function transcribe(pcm: Buffer) {
  const wav = Buffer.concat([wavHeader(pcm.length), pcm])
  try {
    const r = await fetch(BRIDGE_STT, { method: 'POST', headers: { 'content-type': 'audio/wav' }, body: wav })
    if (!r.ok) { logLine(`[stt ${r.status}] ${(await r.text()).slice(0, 140)}`); return }
    const j = (await r.json()) as { text?: string }
    const text = (j.text || '').trim()
    if (text) logLine(text)
  } catch (e) {
    logLine(`[stt ERR] ${(e as Error).message}`)
  }
}

async function pump() {
  if (pumping) return
  pumping = true
  let w: Buffer | null
  while ((w = takeWindow()) !== null) await transcribe(w)
  pumping = false
}

async function finalize() {
  // flush whatever tail remains as one last window
  if (queuedBytes >= MIN_TAIL_BYTES) {
    const tail = Buffer.concat(chunks)
    chunks = []; queuedBytes = 0
    await transcribe(tail)
  } else {
    chunks = []; queuedBytes = 0
  }
  finalizeWav()
}

function pcmCapture(): Plugin {
  return {
    name: 'xorics-pcm-capture',
    configureServer(server) {
      server.middlewares.use('/ingest', (req, res) => {
        if (req.method !== 'POST') { res.statusCode = 405; return res.end() }
        const parts: Buffer[] = []
        req.on('data', (c: Buffer) => parts.push(c))
        req.on('end', () => {
          const body = Buffer.concat(parts)
          appendWav(body)
          chunks.push(body); queuedBytes += body.length
          void pump()
          res.setHeader('content-type', 'application/json')
          res.end(JSON.stringify({ ok: true, total: fullBytes }))
        })
      })
      server.middlewares.use('/finalize', (req, res) => {
        if (req.method !== 'POST') { res.statusCode = 405; return res.end() }
        const bytes = fullBytes
        void finalize()
        res.setHeader('content-type', 'application/json')
        res.end(JSON.stringify({ ok: true, bytes, seconds: +(bytes / BYTES_PER_SEC).toFixed(1), wav: WAV_OUT, transcript: TRANSCRIPT_OUT }))
        logLine(`--- session end: ${(bytes / BYTES_PER_SEC).toFixed(1)}s captured ---`)
      })
    },
  }
}

export default defineConfig({
  plugins: [pcmCapture()],
  server: { host: '0.0.0.0', port: 5173 },
})
