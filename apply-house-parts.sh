#!/usr/bin/env bash
# apply-house-parts.sh
#   Three coupled coder-quality features:
#     A) notebook.py  XORICS-FEATURE: house-parts          -- canonical staples + header-geometry
#        rule pinned into every coder turn, so common parts aren't re-searched (and mis-ranked).
#     B) pcb_tools.py XORICS-FEATURE: connector-bare-guard -- a bare find_part('Header')/'connector'
#        no longer returns a JTAG-ranked list; it asks for the geometry (the real fix for the
#        Microsemi_FlashPro-JTAG-10 surfacing first).
#     C) xorics.py    XORICS-FEATURE: read-file            -- a read_file(path) coder tool, so you can
#        hand the coder a long prompt/spec by PATH instead of pasting it over SSH.
#
#   The house-parts (lib,name) list is INSTANTIATION-CHECKED in the skidl venv at apply time; if any
#   entry fails to load, the script ABORTS before editing anything -- the list can't go stale into
#   "authoritative but wrong". Each feature has its own marker and is applied independently (safe to
#   re-run; already-applied files are skipped).
#
# Plan-by-default:  bash apply-house-parts.sh        # preview
# Apply:            bash apply-house-parts.sh go
#   Set XORICS_SKIP_PART_VALIDATION=1 only if the skidl venv is unavailable (NOT recommended).
set -euo pipefail

GO="${1:-}"
ROOT="${XORICS_ROOT:-$HOME/xorics-ai}"
NB="$ROOT/notebook.py"
PCB="$ROOT/pcb_tools.py"
XOR="$ROOT/xorics.py"
SKIDL_PYTHON="${XORICS_SKIDL_PYTHON:-$ROOT/skidl-venv/bin/python}"
TS="$(date +%Y%m%d-%H%M%S)"
TMP_BLOCK="$(mktemp /tmp/house_block.XXXXXX.py)"
trap 'rm -f "$TMP_BLOCK"' EXIT
say(){ printf '%s\n' "$*"; }

for f in "$NB" "$PCB" "$XOR"; do
  [ -f "$f" ] || { say "ERROR: $f not found. Set XORICS_ROOT to your xorics-ai dir."; exit 1; }
done

nb_done=0; pcb_done=0; xor_done=0
grep -q 'XORICS-FEATURE: house-parts'          "$NB"  && nb_done=1  || true
grep -q 'XORICS-FEATURE: connector-bare-guard' "$PCB" && pcb_done=1 || true
grep -q 'XORICS-FEATURE: read-file'            "$XOR" && xor_done=1 || true

if [ "$nb_done" = 1 ] && [ "$pcb_done" = 1 ] && [ "$xor_done" = 1 ]; then
  say "Already applied (all three markers present). Nothing to do."; exit 0
fi

# ---- the house-parts block: single source of truth, validated then injected -------------------
cat > "$TMP_BLOCK" <<'BLOCK'
# XORICS-FEATURE: house-parts
# Canonical staples seeded into every coder session so the coder uses verified names directly
# instead of searching (and mis-ranking) parts it needs on every board. The (lib, name) entries
# are instantiation-checked by apply-house-parts.sh at apply time -- if any fail to load, that
# script aborts before writing, so this list can't go stale into "authoritative but wrong".
HOUSE_PARTS = [
    ("Device", "R", "value='10k'; pins NUMERIC: r[1], r[2]"),
    ("Device", "C", "value='0.1uF'; pins NUMERIC: c[1], c[2]"),
    ("Device", "C_Polarized", "bulk/electrolytic, value='10uF'; pins NUMERIC"),
    ("Device", "Crystal", "value='16MHz'; pins 1, 2"),
    ("Device", "LED", "pins: K, A"),
    ("Switch", "SW_Push", "tactile / reset button; pins NUMERIC"),
    ("MCU_Microchip_ATmega", "ATmega328P-P", "DIP-28 AVR; call part_pins once for the full 28-pin map"),
]

HOUSE_HEADERS = (
    "HEADERS/CONNECTORS -- choose by GEOMETRY; never a bare find_part('Header'):\n"
    "    find_part('Header 2x14') -> Connector_Generic:Conn_02x14_Odd_Even (dual-row, all I/O)\n"
    "    find_part('Header 2x3')  -> Connector_Generic:Conn_02x03_Odd_Even (ISP)\n"
    "    find_part('Header 2 Pin')-> Connector_Generic:Conn_01x02 (power, single-row)\n"
    "  Generic Conn_* pins are NUMERIC: header[1], header[2]; do NOT use pin names."
)


def _house_lines():
    """The static HOUSE PARTS block pinned above the auto-tracked notebook every turn.
    XORICS-FEATURE: house-parts"""
    out = ["--- HOUSE PARTS (verified -- use these names directly; no find_part needed) ---"]
    for lib, name, note in HOUSE_PARTS:
        out.append(f"  Part('{lib}', '{name}')  -- {note}")
    out.append(HOUSE_HEADERS)
    out.append("For any part NOT listed here, use find_part.")
    return out
BLOCK

if [ "$GO" != "go" ]; then
  say "PLAN (no changes will be made):"
  [ "$nb_done"  = 1 ] && say "  - notebook.py : house-parts already present (skip)" \
                      || say "  - notebook.py : seed HOUSE_PARTS + header rule into render()  (+ backup)"
  [ "$pcb_done" = 1 ] && say "  - pcb_tools.py: connector-bare-guard already present (skip)" \
                      || say "  - pcb_tools.py: bare 'Header' -> ask for geometry, not JTAG  (+ backup)"
  [ "$xor_done" = 1 ] && say "  - xorics.py   : read-file already present (skip)" \
                      || say "  - xorics.py   : add read_file(path) tool + register it  (+ backup)"
  say "  - instantiation-check $(python3 -c "import ast;print(len([n for n in ast.literal_eval(open('$TMP_BLOCK').read().split('HOUSE_PARTS = ',1)[1].split(']',1)[0]+']')]))" 2>/dev/null || echo 7) house parts in the skidl venv"
  say "  - ast.parse all three"
  say ""
  say "Run again with:   bash apply-house-parts.sh go"
  exit 0
fi

# ---- validate house parts by instantiation BEFORE editing anything ----------------------------
if [ "$nb_done" = 0 ]; then
  if [ -x "$SKIDL_PYTHON" ] || [ -f "$SKIDL_PYTHON" ]; then
    say "instantiation-checking house parts in: $SKIDL_PYTHON"
    "$SKIDL_PYTHON" - "$TMP_BLOCK" <<'VALIDATE'
import sys, io, contextlib
ns = {}
exec(open(sys.argv[1], encoding="utf-8").read(), ns)
from skidl import Part
bad = []
for lib, name, *_ in ns["HOUSE_PARTS"]:
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            Part(lib, name)
    except Exception as e:
        bad.append(f"{lib}:{name} ({type(e).__name__}: {e})")
if bad:
    print("  HOUSE_PARTS FAILED to load: " + "; ".join(bad))
    sys.exit(3)
print(f"  all {len(ns['HOUSE_PARTS'])} house parts load OK")
VALIDATE
  elif [ "${XORICS_SKIP_PART_VALIDATION:-0}" = 1 ]; then
    say "WARNING: skidl venv not found at $SKIDL_PYTHON; XORICS_SKIP_PART_VALIDATION=1 -> skipping check."
  else
    say "ERROR: skidl venv not found at $SKIDL_PYTHON and validation not skipped."
    say "       Set XORICS_SKIDL_PYTHON, or XORICS_SKIP_PART_VALIDATION=1 to bypass (not recommended)."
    exit 1
  fi
fi

# ---- A) notebook.py: inject block + hook render() ---------------------------------------------
if [ "$nb_done" = 0 ]; then
  cp "$NB" "$NB.bak-$TS"; say "backed up -> $NB.bak-$TS"
  python3 - "$NB" "$TMP_BLOCK" <<'PATCHNB'
import sys
nb, blockf = sys.argv[1], sys.argv[2]
src = open(nb, encoding="utf-8").read()
block = open(blockf, encoding="utf-8").read().rstrip() + "\n\n\n"
assert "XORICS-FEATURE: house-parts" not in src, "already applied"

anchor1 = "class Notebook:"
assert src.count(anchor1) == 1
src = src.replace(anchor1, block + anchor1)

anchor2 = ('            lines = ["", "--- NOTEBOOK (auto-tracked; survives context trimming) ---",\n'
           '                     "RESOLVED -- reuse these EXACT names; do NOT look them up again:"]')
repl2 = ('            lines = _house_lines() + ["", "--- NOTEBOOK (auto-tracked; survives context trimming) ---",\n'
         '                     "RESOLVED -- reuse these EXACT names; do NOT look them up again:"]')
assert src.count(anchor2) == 1, "render() anchor not unique"
src = src.replace(anchor2, repl2)
open(nb, "w", encoding="utf-8").write(src)
print("patched notebook.py (house-parts block + render hook)")
PATCHNB
else
  say "notebook.py: house-parts already present, skipping"
fi

# ---- B) pcb_tools.py: bare-connector guard ----------------------------------------------------
if [ "$pcb_done" = 0 ]; then
  cp "$PCB" "$PCB.bak-$TS"; say "backed up -> $PCB.bak-$TS"
  python3 - "$PCB" <<'PATCHPCB'
import sys
p = sys.argv[1]
src = open(p, encoding="utf-8").read()
assert "XORICS-FEATURE: connector-bare-guard" not in src, "already applied"

helpers = (
'def _is_bare_connector(query: str) -> bool:\n'
'    """True only for a GENERIC header/connector request with no geometry and no specific type\n'
'    (e.g. \'header\', \'pin header\', \'connector\') -- not \'USB connector\' or \'JST\'. KiCad\'s\n'
'    J-prefix space is undiscriminated (2-pin through 40-pin Pi-hats, JTAG, ...), so for the bare\n'
'    case name-ranking surfaces specialty headers (JTAG) first; we ask for the geometry instead.\n'
'    XORICS-FEATURE: connector-bare-guard"""\n'
'    filler = {"header", "headers", "connector", "connectors", "conn", "pin", "pins",\n'
'              "male", "female", "generic", "breakout", "a", "an", "the",\n'
'              "single", "dual", "double", "row", "rows", "way", "ways"}\n'
'    leftover = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if t and t not in filler]\n'
'    return not leftover\n'
'\n'
'\n'
'def _connector_geometry_prompt(query: str) -> str:\n'
'    """Bare \'header\'/\'connector\' query: return the canonical generic forms + how to re-query by\n'
'    size, instead of a mis-ranked part list that puts a JTAG header first.\n'
'    XORICS-FEATURE: connector-bare-guard"""\n'
'    return (\n'
'        f"\'{query}\' is ambiguous -- KiCad generic headers are picked by GEOMETRY, not by name "\n'
'        f"(a bare header search ranks specialty headers like JTAG first, which is wrong). "\n'
'        f"Re-query with the size:\\n"\n'
'        f"  find_part(\'Header 2x14\')  -> Connector_Generic:Conn_02x14_Odd_Even  (dual-row, all I/O)\\n"\n'
'        f"  find_part(\'Header 1x8\')   -> Connector_Generic:Conn_01x08           (single-row)\\n"\n'
'        f"  find_part(\'Header 2 Pin\') -> Connector_Generic:Conn_01x02           (power)\\n"\n'
'        f"  find_part(\'Header 2x3\')   -> Connector_Generic:Conn_02x03_Odd_Even  (ISP)\\n"\n'
'        f"Generic Conn_* pins are NUMERIC: header[1], header[2], ... (no pin names)."\n'
'    )\n'
'\n'
'\n'
)
anchor_h = '    return ("Connector_Generic", f"Conn_{rows:02d}x{per:02d}")\n\n\ndef _order_by_category(query: str, cat: dict, data: dict) -> list:'
repl_h = '    return ("Connector_Generic", f"Conn_{rows:02d}x{per:02d}")\n\n\n' + helpers + 'def _order_by_category(query: str, cat: dict, data: dict) -> list:'
assert src.count(anchor_h) == 1, "helper-insertion anchor not unique"
src = src.replace(anchor_h, repl_h)

anchor_f = ('    if "J" in cat.get("prefixes", ()):               # connector: honor an explicit NxM geometry\n'
            '        geo = _connector_geometry(query)\n'
            '        if geo:\n'
            '            cat = {**cat, "canonical": geo}           # pin the generic header symbol to the top')
repl_f = ('    if "J" in cat.get("prefixes", ()):               # connector: honor an explicit NxM geometry\n'
          '        geo = _connector_geometry(query)\n'
          '        if geo:\n'
          '            cat = {**cat, "canonical": geo}           # pin the generic header symbol to the top\n'
          '        elif _is_bare_connector(query):              # bare \'header\'/\'connector\': no size, no type --\n'
          '            return _connector_geometry_prompt(query)  # ask for geometry, do not rank JTAG to the top')
assert src.count(anchor_f) == 1, "_finalize anchor not unique"
src = src.replace(anchor_f, repl_f)
open(p, "w", encoding="utf-8").write(src)
print("patched pcb_tools.py (bare-connector guard + geometry prompt)")
PATCHPCB
else
  say "pcb_tools.py: connector-bare-guard already present, skipping"
fi

# ---- C) xorics.py: read_file tool + registration ----------------------------------------------
if [ "$xor_done" = 0 ]; then
  cp "$XOR" "$XOR.bak-$TS"; say "backed up -> $XOR.bak-$TS"
  python3 - "$XOR" <<'PATCHXOR'
import sys
p = sys.argv[1]
src = open(p, encoding="utf-8").read()
assert "XORICS-FEATURE: read-file" not in src, "already applied"

impl = (
'# ---- read a local text file (hand the coder a long prompt/spec by path) -------\n'
'def read_file(path: str, max_chars: int = 20000) -> str:\n'
'    """Read a local UTF-8 text file and return its contents, so a long prompt, spec, pin map, or\n'
'    notes file can be handed to the coder by PATH instead of pasted. Output is capped so a huge\n'
'    file can\'t blow the context window. For a saved SKiDL circuit you intend to VALIDATE, use\n'
'    check_circuit_file instead. XORICS-FEATURE: read-file"""\n'
'    from pathlib import Path as _P\n'
'    fp = _P(path).expanduser()\n'
'    if not fp.exists():\n'
'        return f"No file at {path}. Pass a full path, e.g. ~/xorics-ai/prompts/<name>.md."\n'
'    if fp.is_dir():\n'
'        return f"{path} is a directory, not a file. Pass a path to a text file (ls it first)."\n'
'    try:\n'
'        data = fp.read_text(encoding="utf-8", errors="replace")\n'
'    except Exception as e:\n'
'        return f"Could not read {path}: {e}"\n'
'    n = len(data)\n'
'    if n > max_chars:\n'
'        data = data[:max_chars] + f"\\n...[truncated at {max_chars} of {n} chars]"\n'
'    return f"----- contents of {fp} -----\\n{data}"\n'
'\n'
'\n'
)
anchor_i = '# ---- Tool declarations --------------------------------------------------------'
assert src.count(anchor_i) == 1
src = src.replace(anchor_i, impl + anchor_i)

decl_anchor = ('            "path": {"type": "string", "description": "Full path to a saved SKiDL .py, e.g. "\n'
               '                     "~/xorics-ai/circuits/<name>/<name>.py."}},\n'
               '            "required": ["path"]}}},')
decl_new = decl_anchor + ('\n    {"type": "function", "function": {\n'
               '        "name": "read_file",\n'
               '        "description": "Read a local text file by PATH and return its contents. Use when the user "\n'
               '                       "points you at a file instead of pasting it -- a long prompt, spec, pin map, "\n'
               '                       "or notes (e.g. \'follow ~/xorics-ai/prompts/atmega.md\'). For a saved SKiDL "\n'
               '                       "circuit you intend to validate, use check_circuit_file instead.",\n'
               '        "parameters": {"type": "object", "properties": {\n'
               '            "path": {"type": "string", "description": "Full path to a text file, e.g. "\n'
               '                     "~/xorics-ai/prompts/<name>.md."}},\n'
               '            "required": ["path"]}}},')
assert src.count(decl_anchor) == 1
src = src.replace(decl_anchor, decl_new)

ct_anchor = '"search_datasheets", "fetch_datasheet", "web_search")]'
ct_new = '"search_datasheets", "fetch_datasheet", "web_search", "read_file")]'
assert src.count(ct_anchor) == 1
src = src.replace(ct_anchor, ct_new)

ti_anchor = '    "check_circuit_file": check_circuit_file,\n    "find_part": find_part,'
ti_new = '    "check_circuit_file": check_circuit_file,\n    "read_file": read_file,\n    "find_part": find_part,'
assert src.count(ti_anchor) == 1
src = src.replace(ti_anchor, ti_new)
open(p, "w", encoding="utf-8").write(src)
print("patched xorics.py (read_file impl + decl + CODER_TOOLS + TOOL_IMPLS)")
PATCHXOR
else
  say "xorics.py: read-file already present, skipping"
fi

# ---- verify -----------------------------------------------------------------------------------
say ""
say "verify:"
python3 - "$NB" "$PCB" "$XOR" <<'VER'
import sys
nb, pcb, xor = (open(f, encoding="utf-8").read() for f in sys.argv[1:4])
def c(s, t): return s.count(t)
print(f"  notebook _house_lines  : {c(nb,'def _house_lines')}  (expect 1)")
print(f"  notebook render hook   : {c(nb,'_house_lines() + [')}  (expect 1)")
print(f"  pcb bare-guard funcs   : {c(pcb,'def _is_bare_connector')+c(pcb,'def _connector_geometry_prompt')}  (expect 2)")
print(f"  pcb _finalize branch   : {c(pcb,'return _connector_geometry_prompt(query)')}  (expect 1)")
print(f"  xorics read_file impl  : {c(xor,'def read_file(')}  (expect 1)")
print(f"  xorics read_file wired : {c(xor,'\"read_file\"')}  (expect 3: decl+CODER_TOOLS+TOOL_IMPLS)")
VER
say ""
say "syntax check:"
python3 -c "import ast; ast.parse(open('$NB',encoding='utf-8').read());  print('  notebook.py  OK')"
python3 -c "import ast; ast.parse(open('$PCB',encoding='utf-8').read()); print('  pcb_tools.py OK')"
python3 -c "import ast; ast.parse(open('$XOR',encoding='utf-8').read()); print('  xorics.py    OK')"
say ""
say "DONE. Restart xorics:  cd ~/xorics-ai && source venv/bin/activate && python xorics.py"
say "Long prompts by file:  drop a .md in ~/xorics-ai/prompts/, then in /code: 'read_file ~/xorics-ai/prompts/<name>.md and follow it'."
