#!/usr/bin/env bash
# apply-check-circuit-file.sh
#   XORICS-FEATURE: check-circuit-file   (pcb_tools.py + xorics.py)
#
#   Adds a coder tool: check_circuit_file(path). It reads a SAVED SKiDL script from disk and runs
#   the existing check_circuit on its contents -- so the coder can validate or repair an existing
#   circuits/<name>/<name>.py WITHOUT the whole script being pasted back in over SSH. This removes
#   the clipboard/paste friction entirely: verifying a saved board becomes one instruction.
#
#   Returns whatever check_circuit returns (the status-carrying result), so the agent loop's BUILT
#   detection is unchanged. Intended flow: check_circuit_file(path) once to see errors, then fix by
#   calling check_circuit(code=...) with the corrected script inline (that path captures BUILT code).
#
# Plan-by-default:  bash apply-check-circuit-file.sh        # preview
# Apply:            bash apply-check-circuit-file.sh go
set -euo pipefail

GO="${1:-}"
ROOT="${XORICS_ROOT:-$HOME/xorics-ai}"
PCB="$ROOT/pcb_tools.py"
XOR="$ROOT/xorics.py"
TS="$(date +%Y%m%d-%H%M%S)"
say(){ printf '%s\n' "$*"; }

[ -f "$PCB" ] || { say "ERROR: $PCB not found. Set XORICS_ROOT to your xorics-ai dir."; exit 1; }
[ -f "$XOR" ] || { say "ERROR: $XOR not found."; exit 1; }

pcb_done=0; xor_done=0
grep -q 'XORICS-FEATURE: check-circuit-file' "$PCB" && pcb_done=1 || true
grep -q 'check_circuit_file' "$XOR" && xor_done=1 || true

if [ "$pcb_done" = 1 ] && [ "$xor_done" = 1 ]; then
  say "Already applied (check_circuit_file present in both files). Nothing to do."
  exit 0
fi

if [ "$GO" != "go" ]; then
  say "PLAN (no changes will be made):"
  [ "$pcb_done" = 1 ] && say "  - pcb_tools.py: already has check_circuit_file (skip)" \
                      || say "  - pcb_tools.py: add check_circuit_file(path)  (+ backup)"
  [ "$xor_done" = 1 ] && say "  - xorics.py   : already registers check_circuit_file (skip)" \
                      || say "  - xorics.py   : import + TOOLS decl + CODER_TOOLS + TOOL_IMPLS  (+ backup)"
  say "  - verify + ast.parse"
  say ""
  say "Run again with:   bash apply-check-circuit-file.sh go"
  exit 0
fi

# --------------------------------------------------------------------------
# pcb_tools.py: the function
# --------------------------------------------------------------------------
if [ "$pcb_done" = 0 ]; then
  cp "$PCB" "$PCB.bak-$TS"; say "backed up -> $PCB.bak-$TS"
  python3 - "$PCB" <<'PATCHPCB'
import sys
p = sys.argv[1]
src = open(p, encoding="utf-8").read()
assert "XORICS-FEATURE: check-circuit-file" not in src, "already applied"

fn = (
'def check_circuit_file(path: str) -> str:\n'
'    """Validate a SKiDL script that is already SAVED on disk, by path: read the file and run\n'
'    check_circuit on its contents. Lets the coder repair an existing circuits/<name>/<name>.py\n'
'    without the whole script being pasted back in. Returns whatever check_circuit returns (the\n'
'    status-carrying result), so the agent loop\'s BUILT detection still works.\n'
'    XORICS-FEATURE: check-circuit-file\n'
'    """\n'
'    p = Path(path).expanduser()\n'
'    if not p.exists():\n'
'        return (f"No circuit file at {path}. Pass the full path to a saved script, e.g. "\n'
'                f"~/xorics-ai/circuits/<name>/<name>.py (list them with: ls circuits/*/*.py).")\n'
'    try:\n'
'        code = p.read_text()\n'
'    except Exception as e:\n'
'        return f"Could not read {path}: {e}"\n'
'    return check_circuit(code)\n'
)

anchor = 'def save_circuit(code: str, name: str = "circuit") -> str:'
assert src.count(anchor) == 1, "save_circuit anchor not unique"
src = src.replace(anchor, fn + "\n\n" + anchor)
open(p, "w", encoding="utf-8").write(src)
print("patched pcb_tools.py (check_circuit_file added)")
PATCHPCB
else
  say "pcb_tools.py: already has check_circuit_file, skipping"
fi

# --------------------------------------------------------------------------
# xorics.py: import + tool decl + CODER_TOOLS + TOOL_IMPLS
# --------------------------------------------------------------------------
if [ "$xor_done" = 0 ]; then
  cp "$XOR" "$XOR.bak-$TS"; say "backed up -> $XOR.bak-$TS"
  python3 - "$XOR" <<'PATCHXOR'
import sys
p = sys.argv[1]
src = open(p, encoding="utf-8").read()
assert "check_circuit_file" not in src, "already applied"

edits = [
 # import
 ("from pcb_tools import check_circuit, find_part, part_pins, save_circuit",
  "from pcb_tools import check_circuit, check_circuit_file, find_part, part_pins, save_circuit"),

 # TOOLS: add a decl right after the check_circuit decl
 ('        "parameters": {"type": "object", "properties": {\n'
  '            "code": {"type": "string", "description": "The complete SKiDL Python script to run."}},\n'
  '            "required": ["code"]}}},',
  '        "parameters": {"type": "object", "properties": {\n'
  '            "code": {"type": "string", "description": "The complete SKiDL Python script to run."}},\n'
  '            "required": ["code"]}}},\n'
  '    {"type": "function", "function": {\n'
  '        "name": "check_circuit_file",\n'
  '        "description": "Validate a SKiDL script ALREADY SAVED on disk, by PATH, without pasting it "\n'
  '                       "back in: reads the file, runs the script + ERC + netlist, returns built/failed "\n'
  '                       "with the ERC report and errors. Use to check or repair an existing "\n'
  '                       "circuits/<name>/<name>.py. After seeing errors, fix the design by calling "\n'
  '                       "check_circuit with the corrected code inline.",\n'
  '        "parameters": {"type": "object", "properties": {\n'
  '            "path": {"type": "string", "description": "Full path to a saved SKiDL .py, e.g. "\n'
  '                     "~/xorics-ai/circuits/<name>/<name>.py."}},\n'
  '            "required": ["path"]}}},'),

 # CODER_TOOLS membership
 ('               in ("compile_check", "check_circuit", "find_part", "part_pins",',
  '               in ("compile_check", "check_circuit", "check_circuit_file", "find_part", "part_pins",'),

 # TOOL_IMPLS mapping
 ('    "check_circuit": check_circuit,\n    "find_part": find_part,',
  '    "check_circuit": check_circuit,\n    "check_circuit_file": check_circuit_file,\n    "find_part": find_part,'),
]
for anchor, repl in edits:
    n = src.count(anchor)
    assert n == 1, f"xorics anchor not unique (found {n}x): {anchor[:50]!r}"
    src = src.replace(anchor, repl)
open(p, "w", encoding="utf-8").write(src)
print("patched xorics.py (import + TOOLS + CODER_TOOLS + TOOL_IMPLS)")
PATCHXOR
else
  say "xorics.py: already registers check_circuit_file, skipping"
fi

# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------
say ""
say "verify:"
say "  pcb function     : $(grep -c 'def check_circuit_file' "$PCB")  (expect 1)"
say "  xorics import    : $(grep -c 'check_circuit, check_circuit_file' "$XOR")  (expect 1)"
say "  xorics TOOL_IMPL : $(grep -c '\"check_circuit_file\": check_circuit_file' "$XOR")  (expect 1)"
say ""
say "syntax check:"
python3 -c "import ast; ast.parse(open('$PCB',encoding='utf-8').read()); print('  pcb_tools.py OK')"
python3 -c "import ast; ast.parse(open('$XOR',encoding='utf-8').read()); print('  xorics.py   OK')"
say ""
say "DONE."
say "Verify the ATmega in ONE step now: in /code, paste:"
say "  Call check_circuit_file on ~/xorics-ai/circuits/write_a_skidl_script_that_defines_a_mini/write_a_skidl_script_that_defines_a_mini.py"
say "  then fix the headers (find_part 'Header 2x14'/'Header 2 Pin'/'Header 2x3', numeric pins, all 28 I/O) until it builds."
