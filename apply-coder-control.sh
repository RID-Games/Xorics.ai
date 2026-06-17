#!/usr/bin/env bash
# apply-coder-control.sh
#   XORICS-FEATURE: coder-control   (xorics.py only)
#
#   Makes the human's STOP authoritative, and fixes the /code trap that routes work to the
#   manager by accident. Two coupled changes, both in xorics.py:
#
#   1) REPL: "/code <text>" and "/chat <text>" now SWITCH mode AND run <text> as the first
#      message. Previously only a bare "/code" switched; "/code design a board..." fell through
#      to the manager verbatim, so the manager delegated instead of you driving the coder.
#
#   2) Stop propagation: when you stop a DELEGATED coder run at a checkpoint, run_coder now
#      returns a result carrying status="user_stopped" (same trick as pcb_tools.CheckResult).
#      The shared agent loop sees that status and BREAKS the manager loop instead of letting
#      gpt-oss re-delegate the same task. Stopping the inner coder no longer gets overridden.
#
#   Coder-only loops are unaffected (delegate_to_coder isn't in CODER_TOOLS, so no tool ever
#   returns user_stopped there; the coder's own stop path is unchanged).
#
# Plan-by-default:  bash apply-coder-control.sh        # preview
# Apply:            bash apply-coder-control.sh go
set -euo pipefail

GO="${1:-}"
ROOT="${XORICS_ROOT:-$HOME/xorics-ai}"
XOR="$ROOT/xorics.py"
TS="$(date +%Y%m%d-%H%M%S)"
say(){ printf '%s\n' "$*"; }

[ -f "$XOR" ] || { say "ERROR: $XOR not found. Set XORICS_ROOT to your xorics-ai dir."; exit 1; }

if grep -q 'XORICS-FEATURE: coder-control' "$XOR"; then
  say "Already applied (coder-control present in xorics.py). Nothing to do."
  exit 0
fi

if [ "$GO" != "go" ]; then
  say "PLAN (no changes will be made):"
  say "  - xorics.py: add _ToolResult status carrier        (+ backup)"
  say "  - xorics.py: agent loop breaks on user_stopped (no re-delegate)"
  say "  - xorics.py: run_coder tags stops as user_stopped"
  say "  - xorics.py: REPL '/code <text>' and '/chat <text>' switch mode AND run the text"
  say "  - verify markers + ast.parse"
  say ""
  say "Run again with:   bash apply-coder-control.sh go"
  exit 0
fi

cp "$XOR" "$XOR.bak-$TS"; say "backed up -> $XOR.bak-$TS"

python3 - "$XOR" <<'PATCH'
import sys
p = sys.argv[1]
src = open(p, encoding="utf-8").read()
assert "XORICS-FEATURE: coder-control" not in src, "already applied"

edits = []

# 1) _ToolResult status carrier, inserted before _save_deliverable.
edits.append((
"def _save_deliverable(text: str, task: str):",
'class _ToolResult(str):\n'
'    """A tool result that reads as its text but can carry a control-flow status for the agent\n'
'    loop (mirrors pcb_tools.CheckResult). status=\'user_stopped\' tells the MANAGER loop that the\n'
'    human halted a delegated coder run, so it must NOT re-delegate the task.\n'
'    XORICS-FEATURE: coder-control\n'
'    """\n'
'    def __new__(cls, text, status=None):\n'
'        obj = super().__new__(cls, text)\n'
'        obj.status = status\n'
'        return obj\n'
'\n'
'\n'
'def _save_deliverable(text: str, task: str):'))

# 2a) initialise the stop flag alongside built_code
edits.append((
"        built_code = None\n        for tc in msg.tool_calls:",
"        built_code = None\n"
"        stopped_msg = None                       # XORICS-FEATURE: coder-control\n"
"        for tc in msg.tool_calls:"))

# 2b) detect user_stopped and break the (manager) loop before any re-delegation
edits.append((
'            if getattr(result, "status", None) == "built":\n'
'                built_code = args.get("code")\n'
'        if built_code is not None:',
'            if getattr(result, "status", None) == "built":\n'
'                built_code = args.get("code")\n'
'            elif getattr(result, "status", None) == "user_stopped":  # XORICS-FEATURE: coder-control\n'
'                stopped_msg = str(result)        # human halted a delegated coder; do not re-delegate\n'
'        if stopped_msg is not None:\n'
'            print("    \u25a0 coder stopped at your request \u2014 not re-delegating.")\n'
'            final_text = stopped_msg\n'
'            break\n'
'        if built_code is not None:'))

# 3) run_coder tags a stopped result so the manager loop can see it
edits.append((
'    if final_text.startswith("(stopped"):\n'
'        snap = _snapshot_wip(messages, task)\n'
'        if snap:\n'
'            return f"{final_text}\\n\\n[Xorics snapshotted the in-progress design to: {snap}]"\n'
'        return final_text + "\\n\\n[Nothing to snapshot yet \u2014 no code was submitted to a validator.]"',
'    if final_text.startswith("(stopped"):\n'
'        snap = _snapshot_wip(messages, task)\n'
'        if snap:\n'
'            return _ToolResult(f"{final_text}\\n\\n[Xorics snapshotted the in-progress design to: {snap}]",\n'
'                               "user_stopped")   # XORICS-FEATURE: coder-control\n'
'        return _ToolResult(final_text + "\\n\\n[Nothing to snapshot yet \u2014 no code was submitted to a validator.]",\n'
'                           "user_stopped")'))

# 4) REPL: "/code <text>" / "/chat <text>" switch mode AND run the inline text
edits.append((
'            if q == "/code":\n'
'                BRAIN = CODER; print("\u2192 manual coding mode (driving qwen3-coder directly)\\n"); continue\n'
'            if q == "/chat":\n'
'                BRAIN = MANAGER; print("\u2192 manager mode (gpt-oss; delegates coding)\\n"); continue\n'
'            ans = ask(q)',
'            if q == "/code" or q.startswith("/code "):   # XORICS-FEATURE: coder-control\n'
'                BRAIN = CODER; print("\u2192 manual coding mode (driving qwen3-coder directly)\\n")\n'
'                q = q[5:].strip()\n'
'                if not q:\n'
'                    continue\n'
'            elif q == "/chat" or q.startswith("/chat "):\n'
'                BRAIN = MANAGER; print("\u2192 manager mode (gpt-oss; delegates coding)\\n")\n'
'                q = q[5:].strip()\n'
'                if not q:\n'
'                    continue\n'
'            ans = ask(q)'))

for anchor, repl in edits:
    n = src.count(anchor)
    assert n == 1, f"anchor not unique (found {n}x): {anchor[:60]!r}"
    src = src.replace(anchor, repl)

open(p, "w", encoding="utf-8").write(src)
print("patched xorics.py (4 edits: _ToolResult + loop break + run_coder tag + REPL inline)")
PATCH

say ""
say "verify:"
say "  _ToolResult class   : $(grep -c 'class _ToolResult' "$XOR")  (expect 1)"
say "  user_stopped checks : $(grep -c 'user_stopped' "$XOR")  (expect 4)"
say "  inline /code switch  : $(grep -c 'q.startswith(\"/code \")' "$XOR")  (expect 1)"
say ""
say "syntax check:"
python3 -c "import ast; ast.parse(open('$XOR',encoding='utf-8').read()); print('  xorics.py OK')"
say ""
say "DONE. Restart xorics so it reloads:  cd ~/xorics-ai && source venv/bin/activate && python xorics.py"
say "Then drive the coder DIRECTLY:  /code <your ATmega instruction>   (stop now actually stops)"
