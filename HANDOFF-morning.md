# Xorics — Morning Handoff (after 2026-06-17 ~01:00 session)

## TL;DR
Tonight the **re-fetch spin is dead**, three tool fixes shipped and were verified live, and a
design went **all the way to a verified BUILT** through the real pipeline for the first time. The
board that built was an *ESP32-C3* design (the manager drifted — see Known Issues), not the ATmega.
Your ATmega script is written and saved but **not yet verified**. The morning's first move is one
apply-script (`apply-check-circuit-file.sh`) that lets the coder validate a saved circuit *by path*,
which removes the paste-over-SSH friction entirely — then verifying the ATmega is a single instruction.

---

## What shipped tonight (all applied + verified on RIDGames)
Every one is live; backups are timestamped `*.bak-*` next to each file. These scripts are kept for
reference / git / reproducibility — **you do NOT need to re-run them.**

1. **Coder notebook** (`notebook.py` new + `xorics.py`) — marker `coder-notebook`.
   Externalized, context-trim-proof memory pinned in the system message. Auto-writes every
   successful lookup; **hard-refuses identical lookups** after one cached echo (`LOOKUP_REPEAT_LIMIT`,
   default 1). Killed the part_pins spin. Lives at `~/xorics-ai/notebooks/<slug>-<ts>.md` (cat-able).

2. **Notebook full-pins + connector geometry** (`notebook.py` + `pcb_tools.py`) — markers
   `notebook-full-pins`, `connector-geometry`.
   - Full-pins: the notebook no longer truncates a `part_pins` result, so the ATmega's whole pin list
     stays pinned (fixed the XTAL1→PB6 guessing loop).
   - Geometry: `find_part('... 2x16')` resolves to `Connector_Generic:Conn_02x16_Odd_Even` and sorts
     to the top (beats the "fewest pins" ordering that surfaced JTAG headers).

3. **Connector bare-count** (`pcb_tools.py`) — marker `connector-geometry-count`.
   `find_part('Header 2 Pin')` / `'2-pin'` / `'10 position'` → single-row `Conn_01x02` etc.

### Connector cheat-sheet (how the coder should query headers)
- `find_part('Header 2x14')` → `Conn_02x14_Odd_Even` (main breakout, all I/O)
- `find_part('Header 2 Pin')` → `Conn_01x02` (power)
- `find_part('Header 2x3')`  → `Conn_02x03_Odd_Even` (ISP)
- Generic `Conn_*` parts use **numeric** pins: `header[1]`, `header[2]`, NOT names.
- Space-separated geometry works; underscore symbol-name forms (`Conn_02x16`) degrade to normal search.

---

## The milestone
A design ran end-to-end: script executed → ERC passed → netlist generated → **power-topology grader
passed** → harness locked it `CIRCUIT BUILT` and stopped editing. First confirmed BUILT of the whole
effort. The coder also self-corrected a real ERC error (`Insufficient drive current on net GND`) and
rebuilt. **The coder + check_circuit + power-grader loop works.**

Saved as: `~/xorics-ai/circuits/esp32_c3_BUILT.py` (464 bytes). Keep it.

---

## Immediate next task (morning, in order)

**Step 1 — apply the by-path tool** (removes the paste friction):
```
bash ~/xorics-ai/inbox/apply-check-circuit-file.sh go
```
(or wherever you drop it). It adds `check_circuit_file(path)` to the coder.

**Step 2 — restart xorics** so it picks up the new tool:
```
cd ~/xorics-ai && source venv/bin/activate && python xorics.py
```

**Step 3 — verify the ATmega in `/code`** (type `/code` first, then paste this — no script needed):
> Call check_circuit_file on `~/xorics-ai/circuits/write_a_skidl_script_that_defines_a_mini/write_a_skidl_script_that_defines_a_mini.py`.
> Keep the power, decoupling, crystal, and reset sections — they're correct. The headers are wrong:
> they use `Connector:Microsemi_FlashPro-JTAG-10` (a JTAG header) with JTAG pin names. Replace them
> with real breakout headers via `find_part('Header 2x14')` (main, all I/O), `find_part('Header 2 Pin')`
> (power), `find_part('Header 2x3')` (ISP); connect by pin NUMBER; break out all 28 I/O pins. Then fix
> by calling check_circuit with the corrected code until it builds.

At the first checkpoint, type `20` to give it room.

### How to read the outcome (so you don't have to think hard)
- **BUILT** → your ATmega board passed. Save it: `cp "$(ls -t ~/xorics-ai/circuits/*/*.py | head -1)" ~/xorics-ai/circuits/atmega328p_BUILT.py`.
- **ERC: unconnected / no-driver on I/O pins** → that's the *weak-grader* limit, not a coder fail:
  breakout pins float by design and ERC can't know that. This is the IPC-2221 / ERC-tuning frontier,
  not tonight's bug. Note it and move on.
- **part/pin not found** → a tooling gap; we fix the tool next.

---

## Known issues found tonight (queued, not blocking)
1. **Manager mis-routes pasted code.** When handed the ATmega script in `/chat`, gpt-oss interrogated
   it line-by-line, then delegated an *invented* task and the coder built a different (ESP32) board.
   The manager lacks `check_circuit` and shouldn't try to "understand" code. Fix shape: a manager-prompt
   patch — "if the user pastes code or a file, delegate it to the coder verbatim; do not ask clarifying
   questions about its contents." **Until fixed, always verify circuits via `/code`, never `/chat`.**
2. **No verify-by-path (now FIXED by the morning's apply-script).** Was the night's real blocker; the
   coder couldn't read `circuits/<slug>.py`, and phone-SSH clipboard ≠ desktop Wayland clipboard, so
   `wl-copy` doesn't reach Android. `check_circuit_file` makes this moot.
3. **Dim grey tool-echo text.** `compile_check`/`check_circuit` result echoes render in a dim color.
   Cosmetic ANSI styling, not an error. Low priority.
4. **Firmware can "complete" unverified.** A LoRa run declared "Task completed!" while every
   `compile_check` was FAILED because `LoRa.h` isn't installed in the arduino-cli env. Firmware analog
   of the empty-build problem — the coder should not call a build done when compile_check never passed.

---

## ATmega script state (so you know what's good vs broken)
File: `~/xorics-ai/circuits/write_a_skidl_script_that_defines_a_mini/write_a_skidl_script_that_defines_a_mini.py`
- **Good:** power (VCC/GND), decoupling (0.1µF + 10µF), crystal (16MHz on `XTAL1/PB6` + `XTAL2/PB7`,
  both 22pF load caps), reset (10k pull-up + button on `~{RESET}/PC6`). Pin names correct.
- **Broken:** all three headers use `Connector:Microsemi_FlashPro-JTAG-10` (JTAG programmer header)
  with JTAG pin names (TCK/TDO/VJTAG); only 3 I/O pins broken out instead of 28. → that's what the
  Step-3 prompt fixes.

---

## Still-open roadmap (carried forward)
- **Manager-prompt patch** (issue #1) — small, high-value; probably next after the ATmega verify.
- **IPC-2221 physics calculator** as the Layer-3 strong grader; also the answer to "ERC can't tell a
  breakout pin is meant to float."
- **note() tool** for the coder (model-written notebook entries the harness can't infer) — deferred.
- **Same-failing-script guard** for check_circuit (different from lookup dedup).
- Claude-consult tool; scaffolding engine; GitHub launch; phone access (Tailscale-only); web-parts layer.

### Arbor (RUC-NLPIR) — design reference for the scaffolding engine + held-out grader
**Goal:** mine Arbor's design for two already-planned milestones — the scaffolding engine and the
strong-grader story — and re-implement the useful patterns *natively* in Xorics. NOT a dependency to
bolt on (that would break the "from scratch, no framework" principle, same reason the notebook was
built native rather than forked).

What Arbor is: an open (Apache-2.0) autonomous research agent that turns a long-horizon objective
into a cumulative search. Runs locally; model-flexible via LiteLLM — **supports Ollama / vLLM /
OpenAI-compatible local gateways**, so it can point at llama-swap. Two agents (Coordinator +
Executor) run a six-step cycle (observe → ideate → select → dispatch → backpropagate → decide) over
an **Idea Tree** (hypotheses branch; pruned if they fail, harvested if they work; insights propagate
upward).

Three concrete patterns worth borrowing:
1. **Held-out evaluator discipline** — iterate on a cheap "dev" grader but only ACCEPT a change if it
   clears a margin on a stricter "held-out" grader. Maps directly onto our layered graders: iterate
   against ERC (weak/Layer-1), accept a board only if it also passes the IPC-2221/DRC strong grader
   (Layer-3). This is a direct fix for "ERC is gameable" (empty-build, floating-pins).
2. **Idea Tree with insight backpropagation** — a more developed version of the notebook/roadmap idea:
   externalized persistent state for a search longer than context, with what-failed/what-worked
   propagating to later attempts. Reference design for the scaffolding engine.
3. **Isolated git worktree per experiment** — main untouched until you choose to merge; fits the
   reversibility/security-consciousness principle (stronger than today's snapshot-on-stop).

Honest caveats: (a) adopting the runtime wholesale conflicts with the no-framework principle — borrow
patterns, don't fork; (b) Arbor optimizes a *scalar metric* over a tree, whereas PCB design is mostly
*discrete constraint satisfaction* — the held-out-evaluator and worktree ideas transfer cleanly, the
metric-climbing core only partially; (c) it's heavyweight and this is scaffolding-engine-stage work,
not the next task. Revisit when the scaffolding engine comes up.

Sources:
- Repo (Apache-2.0, install, framework, config): https://github.com/RUC-NLPIR/Arbor
- Project page (hypothesis-tree method): https://ruc-nlpir.github.io/Arbor/
- Paper: https://arxiv.org/abs/2606.11926
- Skill suite (Claude Code `/arbor-research-agent`, if you want to trial the patterns interactively
  before building native): https://github.com/RUC-NLPIR/Arbor/blob/main/skills/README.md

---

## Files in this handoff
**Run in the morning:**
- `apply-check-circuit-file.sh` — adds `check_circuit_file(path)`. Run first (Step 1 above). Tested
  against tonight's full patch chain; plan-by-default, idempotent, backs up both files.

**Reference only (already applied tonight — do NOT re-run):**
- `apply-notebook.sh`
- `apply-pins-and-connector.sh`
- `apply-connector-count.sh`

**Already on RIDGames (no action):**
- `~/xorics-ai/circuits/esp32_c3_BUILT.py` — the verified board from tonight. Keep.
- `~/xorics-ai/circuits/write_a_skidl_script_that_defines_a_mini/...py` — ATmega, awaiting verify.

### Mobile / Taildrop reminder
Before re-sending any script: `rm` the old copy from `~/xorics-ai/inbox/` AND delete it from the
phone's Downloads, or it lands as `-2`. Verify after pulling, e.g.
`grep -c check-circuit-file ~/xorics-ai/inbox/apply-check-circuit-file.sh`.
