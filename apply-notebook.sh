#!/usr/bin/env bash
# apply-notebook.sh — XORICS-FEATURE: coder-notebook
#
# Adds an externalized, context-trim-proof NOTEBOOK to the coder loop:
#   - AUTO-WRITES every successful lookup into a compact block pinned in the system
#     message, so it survives _trim_history (which always keeps the head full-size).
#   - HARD-REFUSES identical lookups (find_part / part_pins / search_datasheets) after
#     LOOKUP_REPEAT_LIMIT cached echoes. check_circuit/compile_check are never gated.
#   - Errored calls are exempt (record() runs only on success), so a flaky call can retry.
#
# Plan-by-default:  bash apply-notebook.sh        # preview, no changes
# Apply:            bash apply-notebook.sh go      # write + patch + verify
#
# Override location if needed:  XORICS_ROOT=/path/to/xorics-ai bash apply-notebook.sh go
set -euo pipefail

GO="${1:-}"
ROOT="${XORICS_ROOT:-$HOME/xorics-ai}"
XOR="$ROOT/xorics.py"
NB="$ROOT/notebook.py"
TS="$(date +%Y%m%d-%H%M%S)"
say(){ printf '%s\n' "$*"; }

[ -f "$XOR" ] || { say "ERROR: $XOR not found. Set XORICS_ROOT to your xorics-ai dir."; exit 1; }

if grep -q 'XORICS-FEATURE: coder-notebook' "$XOR" 2>/dev/null; then
  say "Already applied (marker present in xorics.py). Nothing to do."
  exit 0
fi

if [ "$GO" != "go" ]; then
  say "PLAN (no changes will be made):"
  say "  1. write   $NB"
  say "             (new module: Notebook class + atomic write-on-change markdown persistence)"
  say "  2. backup  $XOR  ->  $XOR.bak-$TS"
  say "  3. patch   $XOR  (4 anchored edits, each asserted unique; aborts clean if an anchor moved)"
  say "             - import Notebook"
  say "             - _agent_loop init: make notebook (coder-only) + capture base_system"
  say "             - re-pin notebook into messages[0] right after _trim_history"
  say "             - dispatch: gate (dedup/refuse) before impl, record() after success"
  say "  4. verify  grep markers + ast.parse both files"
  say ""
  say "Run again with:   bash apply-notebook.sh go"
  exit 0
fi

# ---------------------------------------------------------------------------
# 1. notebook.py  (new module)
# ---------------------------------------------------------------------------
cat > "$NB" <<'NBEOF'
#!/usr/bin/env python3
"""
Xorics coder notebook -- externalized, context-trim-proof memory for the coder loop.
XORICS-FEATURE: coder-notebook

WHY THIS EXISTS
The SKiDL build loop used to spin on part_pins: it re-fetched lookups it had already
resolved and never wrote a netlist. Two compounding causes -- (1) no resolved-parts
memory, so each identical call looked new, and (2) 32K context overflow scrolling the
early results out of the window. Prose guidance ("don't re-search a part you already
found") didn't hold, because it's just text the model can ignore.

This notebook is the mechanical fix:
  * AUTO-WRITE: every successful lookup is recorded by the harness (not the model) into
    a compact block that rides in the system message -- so it survives _trim_history,
    which always keeps the head full-size. The coder is re-grounded every single turn.
  * DEDUP GUARD: identical lookups are capped. The first repeat returns the CACHED result
    plus a nudge; further repeats are HARD-REFUSED (nudge only, impl never called) -- a
    guard the model can't prose its way around, because the tool simply doesn't fire.

Scope decisions (session handoff 2026-06-16):
  * Auto-write only for now. A model-driven note() tool is on the do-later list.
  * Dedup applies to READ-ONLY lookups only (find_part / part_pins / search_datasheets),
    never to check_circuit/compile_check -- driving the coder TO the validator is the goal.
  * Errored calls are exempt: record() runs only on success, so a transient failure never
    populates the cache and a legitimate retry is never refused.

STORAGE: RAM is the source of truth. We flush an atomic, write-on-change markdown file
purely for durability + inspection (`cat` it to watch what the coder thinks it knows).
The notebook must NEVER take down the coder loop, so every public method is defensive.
"""

import json
import os
import re
import time

# How many identical repeats of a lookup to tolerate before hard-refusing.
#   0 = refuse on the very first repeat (the pinned block already re-serves the data)
#   1 = allow one cached echo, then refuse  (default: gentle insurance)
LOOKUP_REPEAT_LIMIT = 1

# Read-only lookups that are safe to dedup. NOT check_circuit / compile_check.
LOOKUP_TOOLS = ("find_part", "part_pins", "search_datasheets")

NOTEBOOK_DIR = os.environ.get("XORICS_NOTEBOOK_DIR", "notebooks")


def _slug(text, n=48):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s[:n] or "design"


def _short(text, n=120):
    return " ".join(str(text).split())[:n]


class Notebook:
    def __init__(self, task=""):
        self.task = task if isinstance(task, str) else str(task)
        self._cache = {}     # key -> cached result text (successful lookups only)
        self._display = {}   # key -> (name, args) for rendering RESOLVED lines
        self._order = []     # keys in first-seen order
        self._repeats = {}   # key -> repeat count
        self._parts = False
        self._pins = False
        self._checked = False
        self._built = False
        self._tripped = False   # a lookup hit the refusal -> coder is repeating
        self._last_written = None
        try:
            os.makedirs(NOTEBOOK_DIR, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            self.path = os.path.join(NOTEBOOK_DIR, f"{_slug(self.task)}-{ts}.md")
        except Exception:
            self.path = None

    # key canonicalization: case/space-insensitive so trivially-different repeats still
    # collide; original args are kept separately for display.
    def _key(self, name, args):
        norm = {}
        for k, v in (args or {}).items():
            norm[k] = " ".join(str(v).split()).lower() if isinstance(v, str) else v
        return name + "|" + json.dumps(norm, sort_keys=True)

    # gate: called BEFORE the impl. None => let it run. str => short-circuit the call.
    def gate(self, name, args):
        try:
            if name not in LOOKUP_TOOLS:
                return None
            key = self._key(name, args)
            if key not in self._cache:          # never successfully done -> allow
                return None
            self._repeats[key] = self._repeats.get(key, 0) + 1
            if self._repeats[key] <= LOOKUP_REPEAT_LIMIT:
                return ("[notebook] You already resolved this -- reusing the cached result. "
                        "Do NOT look it up again; use these exact names and move on.\n\n"
                        + self._cache[key])
            self._tripped = True
            self._flush()
            return ("[notebook] REFUSED: identical lookup already resolved. The result is in "
                    "your NOTEBOOK (top of this message). Stop researching -- write the complete "
                    "SKiDL script now and call check_circuit.")
        except Exception:
            return None     # never block the loop on a notebook bug

    # record: called AFTER a successful impl (errors raise before this is reached).
    def record(self, name, args, result):
        try:
            if name == "find_part":
                self._parts = True
                self._pins = True          # find_part also prints the top match's pins
            elif name == "part_pins":
                self._pins = True
            elif name == "check_circuit":
                self._checked = True
                if getattr(result, "status", None) == "built" or "CIRCUIT BUILT" in str(result):
                    self._built = True
            if name in LOOKUP_TOOLS:
                key = self._key(name, args)
                if key not in self._cache:
                    self._cache[key] = str(result)
                    self._display[key] = (name, dict(args or {}))
                    self._order.append(key)
            self._flush()
        except Exception:
            pass            # a flush/parse hiccup must never discard a good result

    # render: the compact block pinned into the system message each turn.
    def render(self):
        try:
            lines = ["", "--- NOTEBOOK (auto-tracked; survives context trimming) ---",
                     "RESOLVED -- reuse these EXACT names; do NOT look them up again:"]
            if self._order:
                for key in self._order:
                    name, args = self._display[key]
                    a = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
                    lines.append(f"  - {name}({a}) -> {_short(self._cache[key])}")
            else:
                lines.append("  - (nothing resolved yet)")

            def box(b):
                return "[x]" if b else "[ ]"

            lines.append(f"PROGRESS: {box(self._parts)} parts  {box(self._pins)} pins  "
                         f"{box(self._checked)} netlist checked  {box(self._built)} BUILT")
            if self._parts and self._pins and not self._built and (self._tripped or self._order):
                lines.append("-> You have your parts and pins. STOP looking things up. Write the "
                             "complete SKiDL script and call check_circuit NOW.")
            lines.append("----------------------------------------------------------")
            return "\n".join(lines) + "\n"
        except Exception:
            return ""       # degrade to no pinned block rather than crash the turn

    # atomic, write-on-change flush to a cat-able markdown file.
    def _flush(self):
        if not self.path:
            return
        try:
            body = ("# Xorics coder notebook\n"
                    f"task: {self.task}\n"
                    f"updated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"repeat-limit: {LOOKUP_REPEAT_LIMIT}\n"
                    + self.render())
            if body == self._last_written:      # write-on-change: skip identical content
                return
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(body)
            os.replace(tmp, self.path)          # atomic
            self._last_written = body
        except Exception:
            pass
NBEOF
say "wrote $NB"

# ---------------------------------------------------------------------------
# 2. backup
# ---------------------------------------------------------------------------
cp "$XOR" "$XOR.bak-$TS"
say "backed up -> $XOR.bak-$TS"

# ---------------------------------------------------------------------------
# 3. patch xorics.py  (anchored; asserts each anchor is unique; no half-apply)
# ---------------------------------------------------------------------------
python3 - "$XOR" <<'PATCHEOF'
import sys
p = sys.argv[1]
src = open(p, encoding="utf-8").read()
assert "XORICS-FEATURE: coder-notebook" not in src, "already applied"

edits = [
 # A. import
 ("from pcb_tools import check_circuit, find_part, part_pins, save_circuit",
  "from notebook import Notebook                                              # XORICS-FEATURE: coder-notebook\n"
  "from pcb_tools import check_circuit, find_part, part_pins, save_circuit"),

 # B. _agent_loop init: coder-only notebook + capture the static system prompt
 ('    interactive = checkpoint and sys.stdin.isatty()\n    final_text = "(no final message)"',
  '    interactive = checkpoint and sys.stdin.isatty()\n    final_text = "(no final message)"\n'
  '    # XORICS-FEATURE: coder-notebook -- externalized resolved-parts memory + dedup guard\n'
  '    notebook = Notebook(task=messages[1]["content"]) if tag == "coder" and len(messages) > 1 else None\n'
  '    base_system = messages[0]["content"]'),

 # C. re-pin the notebook into the always-kept head, right after the trim
 ("        step += 1\n        messages = _trim_history(messages)",
  "        step += 1\n        messages = _trim_history(messages)\n"
  "        if notebook:  # XORICS-FEATURE: pin notebook into the always-kept head\n"
  "            messages[0] = {**messages[0], \"content\": base_system + notebook.render()}"),

 # D. dispatch: gate before the impl; record only on a fresh success
 ("                result = TOOL_IMPLS[name](**args)",
  "                gate = notebook.gate(name, args) if notebook else None  # XORICS-FEATURE: dedup guard\n"
  "                if gate is not None:\n"
  "                    result = gate  # cached echo / hard refusal; impl NOT called\n"
  "                else:\n"
  "                    result = TOOL_IMPLS[name](**args)\n"
  "                    if notebook:\n"
  "                        notebook.record(name, args, result)  # success only; errors raise before here"),
]

for anchor, repl in edits:
    n = src.count(anchor)
    assert n == 1, f"anchor not unique (found {n}x): {anchor[:60]!r}"
    src = src.replace(anchor, repl)

open(p, "w", encoding="utf-8").write(src)
print("patched xorics.py (4 edits)")
PATCHEOF

# ---------------------------------------------------------------------------
# 4. verify
# ---------------------------------------------------------------------------
say ""
say "verify:"
say "  marker (coder-notebook) : $(grep -c 'XORICS-FEATURE: coder-notebook' "$XOR")  (expect 2)"
say "  notebook import         : $(grep -c 'from notebook import Notebook' "$XOR")  (expect 1)"
say "  gate call               : $(grep -c 'notebook.gate' "$XOR")  (expect 1)"
say "  render pin              : $(grep -c 'base_system + notebook.render' "$XOR")  (expect 1)"
say "  notebook.py present     : $([ -f "$NB" ] && echo yes || echo NO)"
say ""
say "syntax check:"
python3 -c "import ast; ast.parse(open('$XOR',encoding='utf-8').read()); print('  xorics.py   OK')"
python3 -c "import ast; ast.parse(open('$NB',encoding='utf-8').read()); print('  notebook.py OK')"
say ""
say "DONE. Backup at $XOR.bak-$TS"
say "Tunable: LOOKUP_REPEAT_LIMIT at the top of notebook.py (1 = one cached echo then refuse; 0 = refuse on first repeat)."
say "Watch it live:  cat \"\$(ls -t $ROOT/notebooks/*.md 2>/dev/null | head -1)\""
