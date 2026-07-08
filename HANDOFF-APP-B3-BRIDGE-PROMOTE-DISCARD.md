# HANDOFF — APP-B3: promote/discard + diff view over the live bridge

**Purpose.** APP-B2 shipped and was proven live this session (2026-07-08): the
in-ChatActivity permission card renders grant state from the bridge, Approve/Revoke
drive real grant/revoke over HTTP, and the full deny → grant → tool-executes → revoke →
deny-all loop ran on the physical phone against the live bridge — entirely through the
app UI. Next brick: **APP-B3**, the promote/discard + diff surface over the bridge, so a
staged self-edit can be reviewed and approved from the phone. Written 2026-07-08.

Anchors verified against `g2-integration` @ **e050419** (tip). Pull source by SHA from
`raw.githubusercontent.com/RID-Games/Xorics.ai/<sha>/<file>`; never `api.github.com`.

---

## STEP 0 — VERIFY, DON'T TRUST

1. **Tip pushed?** `cd ~/xorics-ai && git log --oneline -1` vs
   `git ls-remote origin g2-integration` → both must say **e050419**. (B2's two-file
   commit `64a4f79..e050419`, 210 insertions / 2 deletions, is pushed — confirmed live.)
2. **Workspace clean?** In the REPL, `/promote` → if it offers ANY file, answer N, then
   `/discard`. Belt-and-suspenders: `python3 -c 'import os; ws=os.path.expanduser("~/.cache/xorics/selfedit/work"); print("exists:", os.path.isdir(ws), "| files:", os.listdir(ws) if os.path.isdir(ws) else [])'`
   → must print `exists: False`. If it exists with files, `rm -rf ~/.cache/xorics/selfedit`.
   The workspace self-recreates via `os.makedirs(..., exist_ok=True)` (xorics.py:325), so
   nuking it is always safe.
3. **Bridge fresh?** bridge.py last changed at 8e5482b; B2 did NOT touch bridge.py, so if
   uvicorn is the one that served B1/B2 it is still current. Confirm anyway:
   `ss -ltnp 'sport = :8090'; curl -s http://127.0.0.1:8090/v1/permissions && echo`
   → expect one uvicorn LISTEN + `{"privileged":["str_replace","write_file"],"granted":[]}`.
   Silent curl (`-s` eats connection-refused) = bridge down → relaunch foregrounded:
   `cd ~/xorics-ai && source venv/bin/activate && uvicorn bridge:app --host 127.0.0.1 --port 8090`
4. **Grants reset on restart.** deny-all is the correct resting state and re-appears on
   every bridge relaunch (per-process, in-memory). Do not read `granted:[]` as a bug.

## WHAT LANDED (APP-B2 — one Taildrop delivery, two files, promoted + pushed + live-proven)

- **e050419, two files, 210 +/2 −:**
  - **Bridge.kt** (+53 after deleteFolder): `data class Perms(privileged, granted)` (:218);
    `getPermissions()` GET (:231); `grantTool()` POST (:241); `revokeTool()` POST (:254).
    Exact house OkHttp pattern — `auth(Request.Builder()...)`, `.use { r -> }`, IOException
    on `!isSuccessful` with `body.take(160)`. `parsePerms()` reads both string arrays.
  - **ChatActivity.kt** (+157): `resumeTick` MutableIntState bumped in `onResume` (:60,:62)
    → `LaunchedEffect(resumeTick){ refreshPerms() }` (:146) re-fetches on open AND resume,
    never caches. `permissionAsk()` (:92) scans each assistant reply via `PERM_TOOL`
    (`tool '([^']+)'`) and `PERM_GRANT` (`/grant ([A-Za-z0-9_]+)`) regexes (:88-89), pops the
    card pre-filled on a hit. Manual `Grants` TextButton in the top bar (:202). Approve →
    `grantTool`, Revoke → `revokeTool`, both re-render from the POST body (it IS the new
    state — no second GET). `PermissionsCard` composable at :300.
- **Delivery path taken:** (b) conventional Taildrop, NOT /selfedit. Rationale held: the
  sandbox suite gives ZERO signal on Kotlin, so "sandbox-green" would be vacuous; the real
  gate was a headless `./gradlew assembleDebug` + install + hardware proof. Build was clean
  (`BUILD SUCCESSFUL in 19s`, 35 tasks), APK 51.99 MiB.
- **Live-proven on the physical phone against the live bridge — the full loop:**
  1. Card opened via `Grants` → both tools rendered `denied` (GET render ✓).
  2. In-app ask for a `write_file` call → gate returned the deny, tool BLOCKED at
     `granted:[]` (the chokepoint fired on a real tool call ✓).
  3. Approve `write_file` in the card → tool then RAN and staged `gate_probe.txt` (5 bytes)
     into the sandbox workspace — impossible without the grant, so the grant round-tripped ✓.
  4. Revoke `write_file` in the card → `curl` confirmed `granted:[]` again ✓.
  Deny → grant → execute → revoke → deny-all, all through the app UI, bridge state
  confirmed independently by curl. `gate_probe.txt` was workspace-only (NOT in the live
  tree — `ls ~/xorics-ai/gate_probe.txt` = No such file), cleared by `rm -rf` at close.

## FINDINGS (this session's scar tissue — read before B3)

- **THE v1 TRIGGER IS EFFECTIVELY DECORATIVE — the manager paraphrases the marker away.**
  On BOTH a str_replace ask and the write_file ask, the manager relayed the denial in its
  OWN words ("the `write_file` tool needs an operator grant. Could you grant it first…")
  instead of passing the verbatim `PERMISSION REQUIRED: tool 'write_file'…` string. So
  `permissionAsk()`'s regex never matched and the card NEVER auto-popped. **Every proof
  this session went through the manual `Grants` button.** The manual entry is not a
  nicety — it is load-bearing; the auto-trigger caught nothing. B3/B-later decision: either
  (a) make `_gate_privileged_call` emit a machine-readable marker the manager is instructed
  to pass through verbatim (or that the BRIDGE injects structurally, not the model), or
  (b) formally accept manual-only and drop the regex. Do not trust the auto-pop until the
  deny signal survives the relay. Deny string source: xorics.py:1557 (verbatim, unchanged).
- **NO CLEAR-CHAT IN THE APP — stale transcript is a real hazard.** `ChatActivity` renders
  full history via `getMessages`, and there is no way to clear it. Old turns
  (`# permission gate probe`, "the full test suite passed") persist verbatim on screen.
  This bit twice: (1) it read as if edits were still staged when the workspace was empty —
  cost a verify cycle; (2) it is exactly the text that can feed a FALSE-POSITIVE trigger
  pop (a nav reply popped the card earlier off stale "permission"-flavored history). Small
  follow-on brick: a clear-chat action (new chatId + wipe the local list). Worth doing
  before/alongside any trigger rework, since the two interact.
- **Taildrop collision rename recurs.** `app-debug.apk` sent in a prior session meant the
  new build landed as a suffixed name and the unsuffixed file in Downloads was STALE. Fix
  that worked: delete every `app-debug*.apk` on the phone, re-send once → single clean file.
  Debug signing + same versionCode 11 = in-place update, no uninstall.

## APP-B3 — THE BRICK (explicitly human-gated)

Promote/discard + diff view over the bridge. The operator must be able to, from the phone:
see the diff of a staged self-edit, then promote (commit) or discard it. **The diff eyeball
must survive the port — NEVER auto-promote.** This is the deliberate human gate on the front
half of the self-improvement loop; `/design` fabricates and even structurally-sound edits
have real slips, so a human reads every diff before it lands. No bridge-side pending queue
exists yet — B3 must add the bridge endpoints (a GET for the staged diff, POST promote, POST
discard, all behind `_auth` like the permissions routes) AND the Android surface.

**Delivery-path decision (open — decide at session start):** same tradeoff as B2. The bridge
Python could go through /selfedit (north-star purity), but any Kotlin is Taildrop-only
(sandbox gives no Kotlin signal). Likely split: bridge endpoints via /selfedit with the
hermetic suite as a real gate (they're Python, the suite bites), Android surface via
Taildrop with the hardware proof as the gate. Confirm the driver echo names the big coder
before any /selfedit grind (see gotcha).

## GOTCHAS (carried forward, still live)

- **xo restart resets mode** → /selfedit silently runs on the local 8K coder → on a big
  file it dies: `openai.BadRequestError` (n_ctx 8192 exceeded) propagates and KILLS the
  REPL. Until the hardening brick lands (catch around the completions call, return the
  error as the run's report): `/power` before /selfedit and CONFIRM the `driver:` echo
  names the big coder (MiniMax M3), not the 8K local.
- **Stale-module signature:** freshly promoted routes 404ing over HTTP = uvicorn never
  restarted. **errno 98** on relaunch = orphaned bridge still holds 8090 →
  `ss -ltnp 'sport = :8090' && fuser -k 8090/tcp`, relaunch foregrounded from repo root.
- **Completeness gate false-positive** when the task text names read-only reference .py
  files ("Read bridge.py first" trips INCOMPLETE EDIT). Benign; judge the diff, not the echo.
- **M3 silently normalizes operator typos** (write_fil3→write_file, nootbook→notebook) and
  may swap write_file→str_replace per schema PREFER. All fine — but silence ≠ ground truth;
  judge the diff, not the request echo.

## STANDING WORKFLOW (unchanged, non-negotiable)

One brick/session proven on hardware before commit · /power before /selfedit and CONFIRM
the driver echo · str_replace-only for xorics.py edits · eyeball every /promote diff,
`changed:` must name exactly the expected files · push immediately after promote · restart
xo (and uvicorn) after promoting what they serve · explicit `git add <file>`, never `-A` ·
inbox/, *.bak, llama-swap.yaml, PERSONALITY-TTRPG-NOTES.md never committed · Taildrop with
sha256 gates for Claude-delivered files · ground truth by SHA from raw.githubusercontent ·
whole files only, never diffs · single-line &&-chained one-liners for the phone.

## KICKOFF (first moves next session)

1. Step 0 (tip e050419 both sides; workspace `exists: False`; bridge fresh; deny-all).
2. Re-pull bridge.py + xorics.py + Bridge.kt + ChatActivity.kt by SHA; re-grep the anchors.
3. Decide the B3 delivery path (likely split: bridge Python via /selfedit, Android via
   Taildrop). Then the brick → for Python: hermetic suite green; for Kotlin: headless
   gradle build → install → diff/promote/discard proven on the physical phone against the
   live bridge.
4. Consider pulling the clear-chat brick forward — it interacts with the trigger rework.
5. Handoff at close; commit + push in the same motion.
