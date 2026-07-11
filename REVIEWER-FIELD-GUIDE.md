# REVIEWER FIELD GUIDE v2 — the reviewer seat, externalized
Rewritten late 2026-07-10 at a29092b, end of the day the question got answered.
For Zawayix operating alone, or for any strong model seated as reviewer later.
The triad: a model writes, the sandbox/suite/hardware verify, the human approves.
This file is the third voice — the one that read diffs and named failures.

## 1. The laws (violate none)
- Kotlin NEVER promotes. Answer n every time. Ship Kotlin THROUGH android_deploy.
- xorics.py edits: str_replace only, never whole-file writes.
- One file per /selfedit task. Multi-file tasks drop the second file.
- `/promote` commits but never pushes. Push immediately, every time.
- Promote never reloads a running process: restart `xo` after promoting; restart
  the bridge with `systemctl --user restart xorics-bridge` (never kill — systemd
  respawns). Bridge restart wipes ALL grants to deny-all; re-grant from the card.
- `/power` must be re-entered after every xo restart; confirm the MiniMax-M3 echo.
- `/reset` before every new arc.
- THE WORKSPACE PERSISTS REJECTED WRITES within a task. Every task text says so;
  every diff gets checked for doubles. A NEW /selfedit resets the workspace from
  live — you cannot patch a stage with a follow-up task, and an unpromoted stage
  is ERASED by the next task. Land or /discard before firing the next one.
- Never edit the live tree while a stage exists.
- At the y/N prompt, anything that is not exactly `y` is a decline — it fails
  closed and the stage survives. Re-run /promote and answer for real.
- Explicit `git add <file>` only. Never `git add -A` (inbox/, llama-swap.yaml,
  *.bak, PERSONALITY-TTRPG-NOTES.md must never be staged).
- Ground truth: pull source by full commit SHA from
  raw.githubusercontent.com/RID-Games/Xorics.ai/<SHA>/<file>. Committed ≠ pushed.
- Multi-line files travel by Taildrop with a sha256 gate, verified BEFORE running.
  An empty inbox failing sha256sum is the gate working.
- Grants live in the card (or REPL /grant). Typing "granted" in chat grants
  nothing — the card is the chokepoint; chat is theater to it.

## 2. Verb map + promote decision table
**promote** = apply a PYTHON stage to the live tree + commit (the REPL y).
**deploy** = ship an ANDROID stage to the phone via android_deploy; never touches
git; mechanically refuses any changed set that leaves android/. Slash commands
exist only in the REPL — the app has no /selfedit; it has the plain-language loop
(§7).

| Change is in…            | Suite green? | Answer |
|--------------------------|--------------|--------|
| android/ (any Kotlin)    | yes          | **n** — then deploy; phone is the oracle |
| android/                 | no           | n, investigate |
| xorics.py / bridge / py  | yes, diff clean | **y**, then push (+ restarts as needed) |
| tests only               | yes          | y or hand-edit (tests have no special law) |
| anything                 | **red**      | **STOP.** Red at HEAD bricks every future
selfedit verify. Never commit red. Growing _PRIVILEGED_TOOLS trips
test_bridge_permissions.py ON PURPOSE — update the tripwire in the same arc. |

y-legal is not y-automatic. The suite is blind to duplication, omission, and
semantics. The diff read catches what the suite cannot — that is the job.

## 3. Review tells by brick type
**Any diff:** `changed:` names exactly the expected files. Count every added
symbol — each def, registration, schema entry, set member appears EXACTLY ONCE.
The zero-knowledge rule: if you cannot explain the diff back to yourself, the
answer is n. That is the gate working, not a limitation.

**Kotlin pure refactor:** byte-compare moved blocks against the pre-move SHA.
**Kotlin feature with async:** any assignment a later guard checks must happen
BEFORE the coroutine launches — `x = v; launch { if (x == v) … }` is a guard;
assignment inside the launch right before the check is a tautology (this exact
bug was staged once and looked perfect). Every blocking call inside scope.launch
needs try/catch; new loaders mirror the file's existing loaders.
**Python new-tool brick — four sites or it doesn't exist:** def, TOOL_IMPLS
registration, TOOLS schema, _PRIVILEGED_TOOLS if gated. The Grants card renders
sorted(_PRIVILEGED_TOOLS) from the bridge — the card listing the new name is
itself a wiring proof.

## 4. Failure signature catalog (match symptoms before inventing theories)
1. **App stuck "thinking…", journal shows only GET polls:** the reply was never
   written; find the POST first. (Model-call failures are ⚠ replies since 2922d5f.)
2. **⚠ … exceed_context_size_error, n_prompt_tokens=X, n_ctx=16384:** the base
   turn outgrew the window. Since a29092b, tool results are capped at 24000 chars
   for local models — a 400 now means system+schemas+history grew, not a tool
   result. Raising n_ctx chases a growing file; navigation tools are the fix.
3. **⚠ empty reply from MiniMax-M3 (finish_reason=abort):** provider-side kill
   under heavy reasoning. Not credits, not length. Retry once; twice on one task
   = task too big for M3 — shrink it or hand-patch (§7).
4. **"nothing pending to promote" after a task "ran":** empty reply, zero writes.
5. **Same edit TWICE in a diff:** workspace-persistence trap. n + /discard +
   re-run with exactly-once language. Never fix a poisoned stage.
6. **Post-hoc narrative fabrication:** the ACTION layer is honest (mechanical
   guards, real exit codes, tool-named specifics in refusals); the EXPLANATION
   layer confabulates when asked about state it cannot see ("what did we stage"
   answered with invented state, self-contradicting within a minute; offers of
   actions no tool provides; invented commands like /stage). Cards and tool
   strings answer state questions; the manager's memory narrates — narration is
   not evidence. A real filename inside a refusal = the tool ran.
7. **Frozen anchor:** identical `str_replace ERROR: old_str not found` ≥3× — M3
   composed the anchor from expectation, not from its read; bigger re-reads won't
   fix it. Countermeasure: pin the minimal SINGLE-LINE old_str in the task text.
8. **The 13k fingerprint (dropped tool argument):** whole-read failure minus
   ranged-read failure ≈ tokens of the skipped prefix ⇒ the model chained
   grep→ranged read but omitted max_chars (default 200000 is M3-sized). Fixed
   structurally by the a29092b cap; the arithmetic remains the diagnostic.
9. **str_replace on a line PREFIX corrupts the workspace** — task texts say "the
   FULL line including both braces."
10. **Bridge behaves pre-edit after a promote:** stale process; check the unit's
   start time vs promote time; restart.

## 5. Driving the models
**Prompting ladder — start at L0, escalate only on evidence:**
- **L0 (default):** plain intent + boundary armor: "In <file> ONLY … change
  nothing else … exactly once when you finish" + the workspace-persistence
  warning. The single best edit of 2026-07-10 was an L0 prompt with no paths and
  no line numbers ("add a rule that we don't replace non-blank lines with test
  probes") — the loop grounded itself. Vague fails SAFE here: the sandbox
  verifies, the diff read decides; an imprecise prompt costs a retry, never
  correctness.
- **L1 (after one confusing failure):** add a semantic landmark, not a line
  number — "the line that appends tool results."
- **L2 (after frozen-anchor thrash):** pin the exact single-line old_str —
  fetched with `grep -n` in bash or grep_file in the app. Specificity is lookup,
  not knowledge.
- **L3 (≥2 failed rounds):** patcher or hand-edit; the suite verifies. The
  loop's dignity is not the mission.
Pinned prose beats intent for anything multi-step; M3 executes pinned bodies
faithfully and reorders unpinned logic. Task texts of every landed brick live
verbatim in `git log` — copy, adapt, re-fire. Never depend on a small model
passing safe tool arguments; make the loop safe by default (the a29092b cap is
that principle in code).

## 6. Docket
**DONE 2026-07-10:** android_deploy (b76eefc; Gates A/A2/B passed on hardware,
first no-sudo Taildrop) · grep_file + start_line (b59198e; Gate C passed 20:56)
· model-aware tool-result cap in _agent_loop, 24000 chars, MINIMAX exempt
(a29092b) · max_tokens + empty-reply guard (2922d5f) · anti-gutting coder rule,
operator-authored at L0 (dda76ff) · permissions tripwire updated (ebf8b41).
**NEXT, in rough order:**
- selfedit-side grep_file resolving against the WORKSPACE copy (grepping live
  while editing the workspace yields wrong line numbers after its own edits —
  that subtlety IS the brick) + start_line passthrough on the selfedit read
  wrapper. Evidence it matters: M3 reached for both mid-task (KeyError
  'grep_file'; unexpected keyword 'start_line').
- Manager system-prompt nudge for uncoached tool selection: one line — "xorics.py
  is ~130KB: grep_file first, then read_file with start_line and a small
  max_chars; never read it whole." The cheap lever before any model swap.
- describe_stage tool (grounded answer for "what's staged" — kills signature 6's
  most common trigger).
- In-app installer (PackageInstaller; one-tap close of the deploy loop; Kotlin —
  ship it THROUGH deploy).
- Provider status/credits in app · bridge-side /power · ⚠ wording polish for the
  abort case · n_ctx experiment (config-only; cannot fix whole-file reads).

## 7. Runbooks
**App-native edit loop (proven end-to-end 2026-07-10):** plain-language ask →
grant write_file/str_replace on the card → sandbox-verified writes (~30s each;
an INSTANT success claim is the fabrication tell) → read the diff on the Edits
card → Kotlin law unchanged (decline promote) → "deploy the staged edit" →
DEPLOYED reply (files, exit 0, size, sha256) → notification → install →
hardware-prove → explicit git add → commit → push → /discard. BUILD FAILED
replies are real gradle output; the live tree then holds the staged copies —
fix or `git checkout -- <files>`.
**Gate transcripts (canonical):** refusal with nothing staged conveys "nothing
staged — run a selfedit first" (paraphrase expected; invented syntax is garnish);
android-only guard names the offending file; DEPLOYED carries count, exit 0,
bytes, sha256.
**Recovery:** poisoned/stale stage → /discard. Live dirtied → git checkout --
<files>. Bridge weird → systemctl --user restart xorics-bridge, then re-grant.
xo weird → Ctrl-C, relaunch, /power. Committed-not-pushed → push before ending.
**Hand-patch pattern (≥2 rounds of thrash):** guarded one-shot Python — asserts
every anchor unique, asserts nothing already present, compiles the result BEFORE
writing, prints a unified diff, refuses on second run; Taildrop + sha256 gate;
verified by ./run_tests.sh. Precedents: inbox/patch_android_deploy.py,
inbox/patch_grep_and_ranged_read.py.

## 8. Seating a future reviewer (any strong model)
Give it: this file, the latest HANDOFF-*.md, the pull-by-SHA pattern, the current
`git ls-remote` tip, and one instruction — "verify remote state yourself before
designing; never trust a session's claims over git log and raw source." Its jobs
are §3, §4, §5. The seat was always a seat.

## 9. State as of writing (a29092b, ~21:00 2026-07-10)
Tree green (32/0), everything pushed, no stage pending, all three deploy gates
passed on hardware. The day, in commits: 272da13 split · B6b-2 history menu ·
1014db5 read_file→manager · 2922d5f loud failures · b76eefc android_deploy ·
ebf8b41 tripwire · b59198e navigation tools · dda76ff anti-gutting (operator's
own L0 prompt) · a29092b result cap. The question first asked at 10:44 — "which
names are in MANAGER_TOOLS" — was answered, grounded, at 20:56: ten names,
ending with grep_file reading its own name in the tuple that grants it. The
system can still fail. It can no longer fail silently, and it can no longer lie
about failing. That property, not any single brick, is what the next session
inherits.
