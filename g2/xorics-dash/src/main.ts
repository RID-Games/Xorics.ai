/**
 * Xorics Dash — Even Hub WebView app  v0.1 (skeleton)
 *
 * ConDash-style modular HUD: HOME = menu List + header, PAGE = full-width text
 * pane fed same-origin from the Xorics bridge (Vite middleware proxies
 * /page/* -> bridge /dashboard/*, sidestepping Even Hub's network whitelist —
 * same trick xorics-mic-test proved with /ingest).
 *
 * ⚠ UNVERIFIED ON HARDWARE. Built against @evenrealities/even_hub_sdk 0.0.11
 * type defs + the proven xorics-mic-test SDK usage. Iterate from the lens.
 *
 * On-device findings (FW 2.2.5.10, first lens contact 2026-07-04):
 *   1. Scroll never reaches the app — the OS moves the list highlight itself
 *      and emits nothing. Selection is only observable at click time, via idx.
 *   2. proto3 default-omission: CLICK_EVENT = 0, so a tap arrives as a
 *      listEvent with NO eventType. Same for idx 0 — an undefined idx on a
 *      click means the FIRST item (0-based assumed; confirm by tapping each).
 *   3. Every event is delivered twice, ~0–2 ms apart — deduped in the handler.
 *   4. Ring input already works via the SDK: sysEvent DOUBLE_CLICK
 *      src=TOUCH_EVENT_FROM_RING. No ring-specific code needed.
 */
import {
  waitForEvenAppBridge,
  ListContainerProperty,
  ListItemContainerProperty,
  TextContainerProperty,
  CreateStartUpPageContainer,
  RebuildPageContainer,
  TextContainerUpgrade,
  OsEventTypeList,
  EventSourceType,
} from '@evenrealities/even_hub_sdk'

// ---- pages ------------------------------------------------------------------
type Page = { id: string; label: string }

const FALLBACK_PAGES: Page[] = [
  { id: 'agent', label: 'Agent' },
  { id: 'nav', label: 'Nav' },
  { id: 'news', label: 'News' },
]
let pages: Page[] = FALLBACK_PAGES

// ---- state --------------------------------------------------------------------
let view: 'home' | 'page' = 'home'
let current: Page | null = null
let selName: string | undefined // last highlighted menu item (from listEvent)
let selIdx: number | undefined
let battery: number | undefined
let homeCreated = false
let lastAct = 0 // debounce: one gesture can surface on >1 event channel
let lastSig = '' // dedupe: FW delivers every event twice, ~0–2 ms apart
let lastSigAt = 0

// ---- same-origin logging (visible in the Vite terminal on RIDGames) ----------
function log(s: string) {
  // eslint-disable-next-line no-console
  console.log('[dash] ' + s)
  try {
    void fetch('/log', { method: 'POST', headers: { 'content-type': 'text/plain' }, body: s })
  } catch {
    /* never let logging break the UI */
  }
}
const evName = (t: OsEventTypeList | undefined) => (t === undefined ? '-' : OsEventTypeList[t] ?? String(t))
const srcName = (s: EventSourceType | undefined) => (s === undefined ? '-' : EventSourceType[s] ?? String(s))

// ---- bridge + data ------------------------------------------------------------
const bridge = await waitForEvenAppBridge()
log('bridge ready')

async function api<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(path)
    if (!r.ok) {
      log(`api ${path} -> HTTP ${r.status}`)
      return null
    }
    return (await r.json()) as T
  } catch (e) {
    log(`api ${path} -> ERR ${(e as Error).message}`)
    return null
  }
}

// ---- containers ---------------------------------------------------------------
// Canvas: 576 x 288, top-left origin (proven by mic-test's full-canvas container).
function headerText(): string {
  const t = new Date()
  const hh = String(t.getHours()).padStart(2, '0')
  const mm = String(t.getMinutes()).padStart(2, '0')
  return `XORICS  ${hh}:${mm}  bat:${battery === undefined ? '?' : battery + '%'}`
}

function menuContainer(): ListContainerProperty {
  return new ListContainerProperty({
    xPosition: 8, yPosition: 8, width: 200, height: 272,
    borderWidth: 1, borderColor: 2, paddingLength: 4,
    containerID: 1, containerName: 'menu',
    isEventCapture: 1,
    itemContainer: new ListItemContainerProperty({
      itemCount: pages.length,
      itemName: pages.map((p) => p.label),
      isItemSelectBorderEn: 1,
    }),
  })
}

function headerContainer(): TextContainerProperty {
  return new TextContainerProperty({
    xPosition: 216, yPosition: 8, width: 352, height: 40,
    borderWidth: 0, borderColor: 2, paddingLength: 4,
    containerID: 2, containerName: 'header',
    isEventCapture: 0,
    content: headerText(),
  })
}

// ---- views ----------------------------------------------------------------------
async function showHome() {
  view = 'home'
  current = null
  const menu = menuContainer()
  const header = headerContainer()
  if (!homeCreated) {
    const created = await bridge.createStartUpPageContainer(
      new CreateStartUpPageContainer({ containerTotalNum: 2, listObject: [menu], textObject: [header] })
    )
    log(`createStartUpPageContainer -> ${created}`)
    homeCreated = true
    if (created === 0) return
    // non-zero: page already exists (relaunch) — fall through to rebuild
  }
  await bridge.rebuildPageContainer(
    new RebuildPageContainer({ containerTotalNum: 2, listObject: [menu], textObject: [header] })
  )
  log('view -> HOME')
}

async function openPage(p: Page) {
  view = 'page'
  current = p
  const body = new TextContainerProperty({
    xPosition: 8, yPosition: 8, width: 560, height: 272,
    borderWidth: 0, borderColor: 2, paddingLength: 4,
    containerID: 1, containerName: 'body',
    isEventCapture: 1,
    content: `${p.label.toUpperCase()}\nLoading…`,
  })
  await bridge.rebuildPageContainer(new RebuildPageContainer({ containerTotalNum: 1, textObject: [body] }))
  log(`view -> PAGE ${p.id}`)
  const data = await api<{ lines: string[] }>(`/page/${p.id}`)
  if (view !== 'page' || current?.id !== p.id) return // user already backed out
  try {
    await bridge.textContainerUpgrade(
      new TextContainerUpgrade({ containerID: 1, content: data?.lines?.join('\n') ?? `${p.label.toUpperCase()}\n(no data)` })
    )
  } catch (e) {
    log(`upgrade ERR ${(e as Error).message}`)
  }
}

function openSelected() {
  let p: Page | undefined
  if (selName !== undefined) p = pages.find((x) => x.label === selName)
  if (!p && selIdx !== undefined) p = pages[selIdx]
  void openPage(p ?? pages[0])
}

// Debounce: the same physical gesture may arrive on listEvent AND sysEvent.
function act(fn: () => void) {
  const now = Date.now()
  if (now - lastAct < 350) return
  lastAct = now
  fn()
}

// ---- events ---------------------------------------------------------------------
bridge.onEvenHubEvent((e) => {
  const le = e.listEvent
  const te = e.textEvent
  const se = e.sysEvent
  if (se?.eventType === OsEventTypeList.IMU_DATA_REPORT) return // spam

  // FW 2.2.5.10 delivers every event twice, ~0–2 ms apart — collapse the pair.
  const sig = JSON.stringify([le?.eventType, le?.currentSelectItemIndex, le?.currentSelectItemName, te?.eventType, se?.eventType, se?.eventSource])
  const now = Date.now()
  if (sig === lastSig && now - lastSigAt < 80) return
  lastSig = sig
  lastSigAt = now

  if (le) {
    log(`listEvent ${evName(le.eventType)} sel="${le.currentSelectItemName ?? '-'}" idx=${le.currentSelectItemIndex ?? '-'}`)
    if (le.currentSelectItemName !== undefined) selName = le.currentSelectItemName
    if (le.currentSelectItemIndex !== undefined) selIdx = le.currentSelectItemIndex
    // proto3 omits default-valued fields and CLICK_EVENT = 0, so taps arrive
    // with NO eventType. Undefined idx on a click = first item (idx 0 omitted).
    const isClick = le.eventType === OsEventTypeList.CLICK_EVENT || le.eventType === undefined
    if (isClick && view === 'home') {
      const raw = le.currentSelectItemIndex
      const p = pages[raw ?? 0] ?? pages[0]
      log(`click -> "${p.label}" (raw idx=${raw ?? '-'}) [wrong page? then idx is 1-based — report]`)
      act(() => void openPage(p))
    }
  }
  if (te) {
    log(`textEvent ${evName(te.eventType)} container=${te.containerName ?? te.containerID ?? '-'}`)
    if (view === 'page') {
      if (te.eventType === OsEventTypeList.DOUBLE_CLICK_EVENT) act(() => void showHome())
      // CLICK inside a page = drill-into-row, next increment (needs row lists)
    }
  }
  if (se) {
    log(`sysEvent ${evName(se.eventType)} src=${srcName(se.eventSource)}`)
    // Fallbacks in case CLICK/DOUBLE_CLICK only surface on the sys channel:
    if (se.eventType === OsEventTypeList.CLICK_EVENT && view === 'home') act(openSelected)
    if (se.eventType === OsEventTypeList.DOUBLE_CLICK_EVENT && view === 'page') act(() => void showHome())
    if (se.eventType === OsEventTypeList.FOREGROUND_ENTER_EVENT) {
      // Re-assert whichever view we think we're in (cheap; safe on relaunch)
      if (view === 'home') void showHome()
      else if (current) void openPage(current)
    }
  }
})

bridge.onDeviceStatusChanged((s) => {
  battery = s.batteryLevel
  log(`deviceStatus battery=${s.batteryLevel ?? '?'} wearing=${s.isWearing ?? '?'} charging=${s.isCharging ?? '?'}`)
})

// Header clock/battery refresh — only meaningful on HOME (PAGE rebuild drops it).
setInterval(() => {
  if (view !== 'home') return
  bridge
    .textContainerUpgrade(new TextContainerUpgrade({ containerID: 2, content: headerText() }))
    .catch(() => {/* transient BLE write error */})
}, 30_000)

// ---- boot ------------------------------------------------------------------------
const cfg = await api<{ pages: Page[] }>('/page/pages')
if (cfg?.pages?.length) pages = cfg.pages
log(`pages: ${pages.map((p) => p.label).join(', ')}`)
await showHome()
