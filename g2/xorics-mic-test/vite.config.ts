import { defineConfig, type Plugin } from 'vite'
import fs from 'node:fs'
import path from 'node:path'

// 16 kHz mono 16-bit PCM
const SAMPLE_RATE = 16000
const CHANNELS = 1
const BITS = 16
const OUT = path.resolve('capture.wav')

function wavHeader(dataLen: number): Buffer {
  const byteRate = (SAMPLE_RATE * CHANNELS * BITS) / 8
  const blockAlign = (CHANNELS * BITS) / 8
  const b = Buffer.alloc(44)
  b.write('RIFF', 0)
  b.writeUInt32LE(36 + dataLen, 4)
  b.write('WAVE', 8)
  b.write('fmt ', 12)
  b.writeUInt32LE(16, 16)       // fmt chunk size
  b.writeUInt16LE(1, 20)        // PCM
  b.writeUInt16LE(CHANNELS, 22)
  b.writeUInt32LE(SAMPLE_RATE, 24)
  b.writeUInt32LE(byteRate, 28)
  b.writeUInt16LE(blockAlign, 32)
  b.writeUInt16LE(BITS, 34)
  b.write('data', 36)
  b.writeUInt32LE(dataLen, 40)
  return b
}

let fd: number | null = null
let dataBytes = 0

function openWav() {
  fd = fs.openSync(OUT, 'w')
  fs.writeSync(fd, wavHeader(0)) // placeholder header
  dataBytes = 0
}

function finalizeWav() {
  if (fd === null) return
  fs.writeSync(fd, wavHeader(dataBytes), 0, 44, 0) // patch RIFF/data sizes
  fs.closeSync(fd)
  fd = null
}

function pcmCapture(): Plugin {
  return {
    name: 'xorics-pcm-capture',
    configureServer(server) {
      server.middlewares.use('/ingest', (req, res) => {
        if (req.method !== 'POST') { res.statusCode = 405; return res.end() }
        const chunks: Buffer[] = []
        req.on('data', (c: Buffer) => chunks.push(c))
        req.on('end', () => {
          const body = Buffer.concat(chunks)
          if (fd === null) openWav() // new session overwrites previous capture.wav
          fs.writeSync(fd as number, body)
          dataBytes += body.length
          res.setHeader('content-type', 'application/json')
          res.end(JSON.stringify({ ok: true, total: dataBytes }))
        })
      })
      server.middlewares.use('/finalize', (req, res) => {
        if (req.method !== 'POST') { res.statusCode = 405; return res.end() }
        const bytes = dataBytes
        finalizeWav()
        res.setHeader('content-type', 'application/json')
        res.end(JSON.stringify({ ok: true, bytes, seconds: +(bytes / 32000).toFixed(1), file: OUT }))
        // eslint-disable-next-line no-console
        console.log(`[capture] wrote ${OUT} — ${bytes} bytes (~${(bytes / 32000).toFixed(1)}s)`)
      })
    },
  }
}

export default defineConfig({
  plugins: [pcmCapture()],
  server: { host: '0.0.0.0', port: 5173 },
})
