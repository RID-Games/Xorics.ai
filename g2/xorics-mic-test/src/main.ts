/**
 * Xorics — Glasses Mic Capture (Even Hub plugin)  v0.2
 *
 * Capture proven: glasses mic, 16 kHz mono PCM, continuous, always-on,
 * tap-controlled, phone-state-independent, no Even cloud.
 *
 * v0.2 adds forwarding: batches PCM and POSTs it SAME-ORIGIN back to the Vite
 * dev server (which is serving this plugin from RIDGames), where a middleware
 * writes capture.wav. Same-origin sidesteps Even Hub's network whitelist.
 *
 * API verified against @evenrealities/even_hub_sdk 0.0.11 type defs +
 * gpsnmeajp/g2_helloworld.
 */
import {
  waitForEvenAppBridge,
  TextContainerProperty,
  CreateStartUpPageContainer,
  RebuildPageContainer,
  TextContainerUpgrade,
  OsEventTypeList,
  AudioInputSource,
} from '@evenrealities/even_hub_sdk'

// ---- state -----------------------------------------------------------------
let micOn = false
let grantStr = '-'
let bytes = 0
let micStart = 0
let lastFrame = 0
let stoppedAt = 0
let everStopped = false

// forwarding
let pending: Uint8Array[] = []
let txBytes = 0
let txErr = ''

const EXPECTED_KBPS = 32

// ---- bridge + initial page -------------------------------------------------
const bridge = await waitForEvenAppBridge()

const main = new TextContainerProperty({
  xPosition: 0, yPosition: 0, width: 576, height: 288,
  borderWidth: 0, borderColor: 2, paddingLength: 4,
  containerID: 1, containerName: 'main',
  isEventCapture: 1,
  content: 'XORICS MIC TEST\nTap temple to start',
})

const created = await bridge.createStartUpPageContainer(
  new CreateStartUpPageContainer({ containerTotalNum: 1, textObject: [main] })
)
if (created !== 0) {
  await bridge.rebuildPageContainer(
    new RebuildPageContainer({ containerTotalNum: 1, textObject: [main] })
  )
}

// ---- lens readout ----------------------------------------------------------
function lines(): string {
  const tx = `tx:${(txBytes / 1024).toFixed(0)}KB${txErr ? ' ' + txErr : ''}`
  if (!micOn) {
    return `XORICS MIC TEST\nstate: OFF — tap\ngrant:${grantStr}  ${tx}\nlast: ${(bytes / 1024).toFixed(0)}KB`
  }
  const now = Date.now()
  const secs = (now - micStart) / 1000
  const kb = bytes / 1024
  const rate = secs > 1 ? kb / secs : 0
  const live = now - lastFrame < 2500
  if (!live && !everStopped && bytes > 0) { everStopped = true; stoppedAt = Math.round((lastFrame - micStart) / 1000) }
  const stream = bytes === 0 ? 'NO AUDIO' : (live ? 'streaming' : `STOPPED @${stoppedAt}s`)
  return [
    'XORICS MIC TEST',
    `state: ON (glasses)`,
    `${secs.toFixed(0)}s  ${kb.toFixed(0)}KB  ${rate.toFixed(1)}KB/s`,
    `stream: ${stream}`,
    `grant:${grantStr}  ${tx}`,
  ].join('\n')
}

async function render() {
  try {
    await bridge.textContainerUpgrade(new TextContainerUpgrade({ containerID: 1, content: lines() }))
  } catch { /* transient BLE write error */ }
}
setInterval(render, 2000)

// ---- forwarding ------------------------------------------------------------
async function flush() {
  if (!micOn || pending.length === 0) return
  const total = pending.reduce((n, a) => n + a.length, 0)
  const buf = new Uint8Array(total)
  let o = 0
  for (const a of pending) { buf.set(a, o); o += a.length }
  pending = []
  try {
    const r = await fetch('/ingest', {
      method: 'POST',
      headers: { 'content-type': 'application/octet-stream' },
      body: buf,
    })
    if (r.ok) { txBytes += total; txErr = '' }
    else { txErr = 'HTTP' + r.status }
  } catch {
    txErr = 'NET'   // POST blocked => visible on the lens
  }
}
setInterval(flush, 2000)

// ---- mic toggle ------------------------------------------------------------
async function toggleMic() {
  if (!micOn) {
    bytes = 0; everStopped = false; stoppedAt = 0
    txBytes = 0; txErr = ''; pending = []
    micStart = Date.now(); lastFrame = micStart
    const ok = await bridge.audioControl(true, AudioInputSource.Glasses)
    grantStr = ok ? 'ok' : 'DENIED'
    micOn = ok
  } else {
    await bridge.audioControl(false)
    micOn = false
    await flush()
    try { await fetch('/finalize', { method: 'POST' }) } catch { /* ignore */ }
  }
  render()
}

// ---- events ----------------------------------------------------------------
bridge.onEvenHubEvent((event) => {
  if (event.audioEvent) {
    bytes += event.audioEvent.audioPcm.length
    lastFrame = Date.now()
    if (micOn) pending.push(event.audioEvent.audioPcm)
    return
  }
  const sys = event.sysEvent?.eventType
  if (
    sys === OsEventTypeList.FOREGROUND_EXIT_EVENT ||
    sys === OsEventTypeList.FOREGROUND_ENTER_EVENT ||
    sys === OsEventTypeList.IMU_DATA_REPORT ||
    sys === OsEventTypeList.ABNORMAL_EXIT_EVENT ||
    sys === OsEventTypeList.SYSTEM_EXIT_EVENT
  ) return

  const tt = event.textEvent?.eventType
  if (tt === OsEventTypeList.DOUBLE_CLICK_EVENT) return
  if (event.textEvent || tt === OsEventTypeList.CLICK_EVENT || sys === OsEventTypeList.CLICK_EVENT) {
    void toggleMic()
  }
})

console.log('[xorics-mic-test v0.2] ready; expected ~' + EXPECTED_KBPS + ' KB/s')
