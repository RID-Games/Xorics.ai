# HANDOFF — APP-B2: Android permission UI over the live bridge surface

**Purpose.** APP-B1 shipped and was proven live this session (2026-07-08): the bridge now
exposes GET /v1/permissions + POST grant/revoke (tested), and the full cross-process loop —
grant via curl → app-chat ask → verified staged edit → discard → revoke — ran on hardware.
Next brick: **APP-B2**, the Android surface (render grant state, Approve/Deny, PERMISSION
REQUIRED trigger). Written 2026-07-08.

Anchors verified against `g2-integration` @ **19ddd50** (tip). Pull source by SHA from
`raw.githubusercontent.com/RID-Games/Xorics.ai/<sha>/<file>`; never `api.github.com`.

---

## STEP 0 — VERIFY, DON'T TRUST

1. **Tip pushed?** `cd ~/xorics-ai && git log --oneline -4` vs
   `git ls-remote origin g2-integration` → both must say **19ddd50**. This session found
   B2–B4 stranded unpushed AND one skipped /promote. Staged ≠ promoted ≠ pushed —
   three gates; "Everything up-to-date" after a skipped promote reads like success.
2. **Workspace clean?** The capstone staged `# permission-gate probe` into notebook.py in
   the shared selfedit workspace. If /discard wasn't run at close: in the REPL, /promote →
   if it offers notebook.py, answer N, then /discard. NEVER land the probe.
3. **Bridge fresh?** bridge.py last changed at 8e5482b; if uvicorn predates that promote,
   restart it. Stale-module signature: routes you can grep in the file 404 over HTTP.
4. **xo restart state:** mode AND grants are per-process and reset on every restart.
   /power must be re-entered after each relaunch; forgetting is not cosmetic (see gotchas).

## WHAT LANDED (APP-B1 — two /selfedit bricks, both promoted + pushed + live-proven)

- **8e5482b routes:** bridge.py +41 lines after /v1/models — `_permissions_state()` helper;
  `GET /v1/permissions` (:351); `POST /v1/permissions/grant` (:357); `POST
  /v1/permissions/revoke` (:367). All handlers `_auth(request)` first (house direct-route
  pattern). Grant 400s off `_grant_tool()==False`; revoke pre-checks `_is_privileged` then
  calls `_revoke_tool` unconditionally (idempotent 200). Non-dict body / non-str tool → 400.
  Known + accepted: malformed non-JSON body → 500 (`request.json()` raises pre-guard).
  Live-proven with the 5-curl chain: state / grant / 400 / revoke / state — exact.
- **19ddd50 test:** `test_bridge_permissions.py` (86 lines) —
  `os.environ.pop("XORICS_BRIDGE_TOKEN")` **before** `import bridge` (_TOKEN is read at
  import, bridge.py:64), `TestClient(bridge.app)`, snapshot + deterministic clear of
  `_TOOL_GRANTS` inside the try, restore in the finally, 13 checks, `SystemExit(1 if _fail)`.
- **Capstone proven on hardware:** curl grant → Android app chat probe → str_replace ran in
  the BRIDGE process → staged to the shared workspace → full suite green in the sandbox →
  honest "verified, suite passed" relay on the phone → discard → curl revoke → deny-all.
  The loop the app UI will drive is real, today, with zero Android code.

## FACTS APP-B2 HANGS ON

- **Endpoints** (all behind `_auth`; token env `XORICS_BRIDGE_TOKEN`, unset ⇒ header
  ignored, tailnet still gates):
  - `GET /v1/permissions` → `{"privileged":["str_replace","write_file"],"granted":[...]}`
  - `POST /v1/permissions/grant`  body `{"tool": name}` → 200 new state, or 400
    `{"detail":"not a privileged tool"}`
  - `POST /v1/permissions/revoke` same body → 200 new state (idempotent), 400 same detail
- **Grants are PER-PROCESS, in-memory, deny-all on every bridge restart.** The app must
  render from GET on open/resume, never cache, and expect re-grant after restarts. The
  REPL's /grants shows the REPL's set, not the bridge's — different processes, both correct.
- **Deny string** (xorics.py `_gate_privileged_call`, verbatim):
  `PERMISSION REQUIRED: tool '<name>' needs an operator grant — ask the operator to run
  /grant <name>, then retry.`
  v1 trigger: regex the tool name out of the assistant reply to pop the card. **Best-effort
  only** — the manager relays in its own words and can paraphrase the marker away. The card
  must also be reachable manually (from the state GET) or misses are silent.
- **App tree:** `com.rid.xorics` (v0.10, versionCode 11) on this branch; `ChatActivity` is
  the chat surface; builds run headless on RIDGames CLI (Gradle 8.10.2 / AGP 8.7.3, JDK 21).

## APP-B2 — THE BRICK

In-ChatActivity card (or a small permissions screen): on open/resume, GET /v1/permissions
and render privileged vs granted; Approve → POST grant; Deny/Revoke → POST revoke;
re-render from the response body (it IS the new state — no second GET needed). v1 trigger:
scan each assistant reply for the PERMISSION REQUIRED marker / tool name and surface the
card pre-filled. No bridge-side pending queue yet — that is APP-B3 territory.
**APP-B3 (later, explicitly human-gated):** promote/discard + diff view over the bridge —
the diff eyeball must survive the port; never auto-promote.

**Delivery-path decision (open — decide at session start):** the Kotlin edits could go
through /selfedit (north-star purity: Xorics builds himself), but the sandbox suite gives
ZERO signal on Kotlin — "sandbox-green" would be vacuous; the real gate is a headless
`./gradlew assembleDebug` + install + hardware proof. Options: (a) /selfedit for the file
writes with the gradle build as the manual proof gate, (b) conventional Taildrop delivery.
Either way the proof is the card working on the physical phone against the live bridge
(grant/deny round trip + trigger pop).

## GOTCHAS (this session's scar tissue)

- **xo restart resets mode** → /selfedit silently runs on the local 8K coder → on a big
  file it dies: `openai.BadRequestError` (8442 > 8192 n_ctx) propagates and **kills the
  whole REPL**. Hardening brick (queued, not started): catch around the completions call
  and return the error as the run's report instead of dying. Until then: confirm the
  `driver:` echo names MiniMax M3 before letting any selfedit grind.
- **Stale-module signature:** freshly promoted routes 404ing = uvicorn never restarted.
  **errno 98** on relaunch = an orphaned bridge still holds 8090 →
  `ss -ltnp 'sport = :8090' && fuser -k 8090/tcp`, then relaunch foregrounded. Launch line
  (documented in bridge.py docstring ~:22):
  `cd ~/xorics-ai && source venv/bin/activate && uvicorn bridge:app --host 127.0.0.1 --port 8090`
- **Completeness gate, two edges:** (a) blind to NEW files (targets = names ∩ os.listdir) —
  the operator eyeball is the only guard on new-file tasks; `changed:` must name exactly
  the expected file. (b) FALSE POSITIVE when the task text names read-only reference .py
  files ("Read bridge.py and test_api.py first" tripped INCOMPLETE EDIT). Benign — the
  footer's own "if they were meant to change" hedge is the judgment call. Recurs every
  time a task cites reference files.
- **M3 silently normalizes operator typos** (nootbook→notebook; the write_file schema's own
  example grounded it) and swapped write_file→str_replace because its schema says PREFER.
  Both fine — but silence ≠ ground truth; judge the diff, not the request echo.

## STANDING WORKFLOW (unchanged, non-negotiable)

One brick/session proven on hardware before commit · /power before /selfedit and CONFIRM
the driver echo · str_replace-only for xorics.py edits · eyeball every /promote diff,
`changed:` must name exactly the expected files · push immediately after promote · restart
xo (and uvicorn) after promoting what they serve · explicit `git add <file>`, never `-A` ·
inbox/, *.bak, llama-swap.yaml, PERSONALITY-TTRPG-NOTES.md never committed · Taildrop with
sha256 gates for Claude-delivered files · ground truth by SHA from raw.githubusercontent.

## KICKOFF (first moves next session)

1. Step 0 (tip 19ddd50 both sides; probe discarded; bridge fresh; /power confirmed).
2. Re-pull bridge.py + xorics.py by SHA; re-grep the anchors above.
3. Decide the APP-B2 delivery path; then the brick → headless gradle build → install →
   card proven on the physical phone against the live bridge.
4. Handoff at close; commit + push in the same motion.
