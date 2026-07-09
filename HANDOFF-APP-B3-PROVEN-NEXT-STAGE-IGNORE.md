# HANDOFF — APP-B3 PROVEN END TO END; next brick: selfedit stage-ignore (data-loss trap live)

**Purpose.** APP-B3 is DONE and proven on hardware, phone-only: chat-staged write →
Edits card renders files+diff from the live endpoint → Discard proven → re-stage →
Promote+push from the card → sandbox re-verified → committed and pushed. Both of last
session's ghosts were root-caused and closed the same morning. One NEW hazard was found
and worked around by hand: **the promote path will clobber live runtime files** (data/,
state/) staged into the workspace — the stage-ignore brick is now TOP PRIORITY and has a
sharp spec below. Network sleep-drop is MITIGATED, not closed. Written 2026-07-09 ~12:00.

Anchors (verify, don't trust — `git ls-remote origin g2-integration` at session start):
- `c729d64` — bridge.py APP-B3 routes (GET/promote/discard). Live-proven.
- `ba90c95` — xorics.py `_selfedit_diff` decode hardening (`_read_lines`, NUL→"(binary
  file)", errors="replace"). Pushed THIS session (last session claimed pushed; remote said
  otherwise — the push had died on the flapping tailnet).
- `963c89f` — APP-B3-PROBE.txt, **promoted + pushed from the phone via the card**. The
  proof commit.
- One more commit on top if the close-out ran (Bridge.kt, ChatActivity.kt,
  test_bridge_selfedit.py — see STEP 0). Pull source by SHA from
  `raw.githubusercontent.com/RID-Games/Xorics.ai/<TIP>/<file>`. NEVER api.github.com.

## STEP 0 — VERIFY, DON'T TRUST
1. `cd ~/xorics-ai && git status --short && git log --oneline -4` — if Bridge.kt /
   ChatActivity.kt / test_bridge_selfedit.py are still uncommitted, the close-out never
   ran; run it FIRST (one-liner in KICKOFF). Remote must match local:
   `git ls-remote origin g2-integration | cut -c1-7`.
2. Bridge health: `systemctl --user status xorics-bridge --no-pager | head -5` and
   `ss -tlnp | grep 8090` — the 8090 owner PID must belong to the unit. If any FOREIGN
   uvicorn owns 8090, that's the stale-squatter failure again (see FINDINGS).
3. `bridgeup` is REDEFINED (in ~/.bashrc):
   `systemctl --user restart xorics-bridge && sleep 2 && journalctl --user -u xorics-bridge -n 20 --no-pager`
   NEVER foreground uvicorn on 8090 again — that is what created the squatter.
4. Workspace: post-promote reset means `~/.cache/xorics/selfedit/` should be absent/empty.

## WHAT LANDED THIS SESSION (all proven on hardware)
- **500 #2 closed — it was 500 #1 served by a stale process.** The decode fix was never
  in the running bridge: last night's foregrounded `bridgeup` uvicorn (old module in
  memory) still owned 8090; the systemd unit crash-looped errno-98 behind it (~370
  restarts at 4s intervals). `fuser -k 8090/tcp` evicted it; the unit took the port;
  HTTP 200 with correct JSON immediately. In-shell differential proved the fixed code
  first: `venv/bin/python3 -c "import xorics; ..."` → `IN-SHELL OK — 2 changed:
  ['APP-B3-PROBE.txt', 'data/xorics.db']`.
- **False "clean load" explained:** `review_self_edit` returns `(_selfedit_diff(rels) if
  rels else "")` — an EMPTY workspace never exercises the diff, so last session's
  "nothing pending" render proved nothing about the fix being live. A promote resets the
  workspace, so every post-promote render is the empty path. Don't accept empty-state
  loads as code proof.
- **Full card proof:** files list + probe diff rendered from the real endpoint; Discard →
  "nothing pending / DISCARDED, live tree untouched"; re-stage via chat (write_file, real
  ~30s sandbox verify); db-sync workaround; Promote+push → `PROMOTED … (963c89f) …
  PUSHED: ba90c95..963c89f`. Read side, destroy side, and write side all live.
- **Network:** loopback curl (`127.0.0.1:8090`) adopted for all shell probes — takes the
  tailnet out of every diagnosis. Phone: Tailscale battery-unrestricted applied; **sleep
  still drops the tunnel** — remaining steps in NETWORK below.

## THE DATA-LOSS TRAP (why stage-ignore is now the brick)
`promote_self_edit` copies EVERY changed rel into the live tree BEFORE `git add`. The
workspace stages `data/` and `state/`, and live `data/xorics.db` mutates with every chat —
so the db is essentially ALWAYS in the changed set via the chat path. Tapping Promote in
that state would overwrite the live db with a stale snapshot (losing chats/state), and
`git add` would then FAIL because `data/` is gitignored — damage done, nothing committed,
status message understating it. **Manual workaround used this session (keep using until
the brick lands):** after the write is verified and BEFORE promote, with no chat messages
in between:
`cp ~/xorics-ai/data/xorics.db ~/.cache/xorics/selfedit/work/data/xorics.db`
then Refresh the card and ONLY promote when files lists exactly the intended file(s).

**Settled from source (ba90c95):** `promote_self_edit` runs `git -C REPO_ROOT` — the
LIVE tree's git, never the workspace copy. The old comment claiming promotion needs the
workspace `.git` is wrong; dropping it from staging is safe.

## NEXT BRICK — stage-ignore + binary skip (single xorics.py change, /selfedit route)
Human-gated, `/power` first (confirm `driver:` echoes MiniMax M3), eyeball the diff:
(a) Widen `_SELFEDIT_STAGE_IGNORE` (currently: venv, skidl-venv, .venv, __pycache__,
    .mypy_cache, .pytest_cache, node_modules, *.pyc, *.log) to ALSO drop:
    `.git`, `data`, `state`, `inbox`, `android`, `android.bak-*`, `*.bak`, `*.bak-*`,
    `hermes-runtime`, `nominatim`, `nominatim-venv`, `sketches`, `notebooks`, `circuits`,
    `rag_index`, `nav`, `llama-swap.yaml`, `datasheets`. (Largely mirrors .gitignore;
    android/ excluded because Kotlin goes via Taildrop+hardware proof, and the sandbox
    suite can't verify it anyway.)
(b) Belt-and-suspenders in `_selfedit_changed_files`: skip a rel when either side's bytes
    contain NUL (binary never reaches diff or promote), and wrap the LIVE-side
    `open(live_abs,"rb").read()` in the same try/except OSError as the workspace side
    (it is currently unguarded — a vanishing/odd live file 500s the review).
(c) Add the missing test to test_bridge_selfedit.py: stage a binary file → review returns
    it filtered/"(binary file)" and never throws; suite stays green (currently 32 pass).
Acceptance on hardware: chat-stage a probe → card lists ONLY the probe with NO cp
workaround → Discard → re-stage → Promote+push clean.

## FINDINGS (this session's scar tissue)
- **Committed ≠ pushed, again, with teeth:** the decode fix existed only locally while the
  handoff said "pushed". First act of any session: ls-remote vs local HEAD.
- **A restart is not a restart if someone else owns the port.** `systemctl restart` exits
  0 even when the fresh worker immediately dies errno-98 behind a squatter. Health check
  = `ss -tlnp | grep 8090` PID belongs to the unit, not "restart returned 0".
- **Manager grant-blindness:** grants live per bridge process; the manager only learns
  them by ATTEMPTING a call. After two real denials in the transcript it answered from
  its own narration ("I still don't have permission") and would not retry on "I gave you
  permission". What worked: a message asserting new state + imperative ("the write_file
  grant is now live in the bridge — attempt the call now and report the tool's actual
  result"). Mechanical fix candidate (small brick): bridge injects a one-line context note
  on every grant change (`grants updated: write_file=granted`) so state changes arrive as
  data, not operator claims.
- **Fake-success tell:** a REAL write_file sits ~30s in the podman verify; an INSTANT
  "created successfully" bubble is the manager echoing transcript shapes. Also true the
  other way: the verify's latency means a card Refresh can race a real write — wait out
  the sandbox before declaring a miss. Disk/card = truth; bubbles = vibes.
- **The error banner is sticky** (last failure stays rendered below the chat even after
  recovery). Cosmetic; fold "clear on next success" into the clear-chat brick.
- **Do NOT bridge-restart to fix chat weirdness:** grants wipe to deny-all (per-process)
  but history is persisted and reloaded — a restart keeps the poison and loses the grant.

## NETWORK — mitigated, not closed
Battery-unrestricted: applied. Sleep still drops the tunnel. Remaining, in order:
1. OS-level Always-on VPN: Settings → Connections → More connection settings → VPN →
   gear next to Tailscale → Always-on (leave "block connections without VPN" OFF).
2. Battery → Background usage limits → add Tailscale to **Never sleeping apps** (OneUI's
   app sleeper is independent of the unrestricted toggle).
3. If it still drops: the in-app wake-lock brick is the durable answer — PULLED FORWARD.
Loopback curls for all shell diagnosis; the `.ts.net` name only matters to the app.

## STANDING WORKFLOW (unchanged except bridgeup)
One brick/session proven on hardware before commit · `/power` before `/selfedit`, confirm
driver echo · str_replace-only for xorics.py · eyeball every promote diff; `changed:` must
name exactly the expected files · promote-from-card pushes; REPL /promote does not ·
`bridgeup` = systemd restart + journal tail (never foreground uvicorn on 8090) · explicit
`git add <file>`, never -A · inbox/, *.bak, llama-swap.yaml, PERSONALITY-TTRPG-NOTES.md
never committed · Taildrop + sha256 for Claude-delivered files · ground truth by SHA from
raw.githubusercontent (CURRENT tip) · whole files, never diffs · single-line &&-chained
one-liners · db-sync cp before ANY promote until the stage-ignore brick lands.

## KICKOFF (next session)
1. STEP 0. If close-out pending:
   `cd ~/xorics-ai && git add android/app/src/main/java/com/rid/xorics/Bridge.kt android/app/src/main/java/com/rid/xorics/ChatActivity.kt test_bridge_selfedit.py && git commit -m "APP-B3: Edits surface (SelfEditCard, armed confirm, slowClient) + selfedit route tests" && git push origin`
   Optional: `git rm APP-B3-PROBE.txt && git commit -m "remove promote probe" && git push origin`
2. Finish NETWORK steps 1–2 on the phone; 3× sleep-wake Edits loads is the gate.
3. The stage-ignore brick per the spec above: `/power` → `/selfedit` (task text can lift
   the spec verbatim) → eyeball diff → REPL /promote → **push immediately** → `bridgeup`
   → hardware acceptance (card lists only the probe, no cp).
4. Handoff at close; commit + push in the same motion.

## PARKED / ON THE HORIZON
- **Grant-change context injection** (small, kills the grant-blindness class) — NEW.
- **Clear-chat brick** (new chatId + wipe local list + clear sticky error banner) —
  case file now 3 sessions deep; pull forward after stage-ignore.
- **Android wake-lock** — pulled forward if NETWORK step 1–2 don't hold.
- Permission auto-trigger still decorative (manual Grants button load-bearing) · binary
  diff test folds into the brick · R1 ring Phase 0 BLE capture · GPS origin via WebView
  geolocation · PCB draft-gap (`CODER_BACKSTOP=40` re-probe) · `/plan → /design` front
  door (dry-run 2026-07-08 is its spec evidence: forced read-before-plan + stash-not-chat)
  · scope-drop loud-failure in `run_self_edit` · unmounted sdb1 (931G) · long-term:
  avatar, TTS, personality, pick-and-sort claw robot.
