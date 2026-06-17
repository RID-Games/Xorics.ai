#!/usr/bin/env bash
# apply-pins-and-connector.sh
#   Fix 1  XORICS-FEATURE: notebook-full-pins   (notebook.py)
#     The pinned block ran every cached lookup through _short (120 chars), clipping a big
#     part's pin list -- so the coder couldn't read the real KiCad pin names and guessed
#     off web pinouts (XTAL1 -> PB6, both wrong). Keep part_pins results FULL; give
#     find_part a generous cap (top-match pins survive); 120 for the rest.
#
#   Fix 2  XORICS-FEATURE: connector-geometry   (pcb_tools.py)
#     find_part('Pin Header 2x16') returned 10-pin JTAG connectors because "fewest pins"
#     ordering buries big breakout headers. Parse an explicit NxM geometry and pin the
#     canonical Connector_Generic:Conn_02x16_Odd_Even to the top via the existing canonical
#     mechanism. Validated before injection -> a bad parse degrades, never breaks.
#
# Plan-by-default:  bash apply-pins-and-connector.sh        # preview
# Apply:            bash apply-pins-and-connector.sh go
set -euo pipefail

GO="${1:-}"
ROOT="${XORICS_ROOT:-$HOME/xorics-ai}"
XNB="$ROOT/notebook.py"
PCB="$ROOT/pcb_tools.py"
TS="$(date +%Y%m%d-%H%M%S)"
say(){ printf '%s\n' "$*"; }

[ -f "$XNB" ] || { say "ERROR: $XNB not found (run apply-notebook.sh first)."; exit 1; }
[ -f "$PCB" ] || { say "ERROR: $PCB not found. Set XORICS_ROOT to your xorics-ai dir."; exit 1; }

nb_done=0; pcb_done=0
grep -q 'XORICS-FEATURE: notebook-full-pins' "$XNB" && nb_done=1 || true
grep -q 'XORICS-FEATURE: connector-geometry'  "$PCB" && pcb_done=1 || true

if [ "$nb_done" = 1 ] && [ "$pcb_done" = 1 ]; then
  say "Both fixes already applied. Nothing to do."
  exit 0
fi

if [ "$GO" != "go" ]; then
  say "PLAN (no changes will be made):"
  [ "$nb_done" = 1 ]  && say "  - notebook.py : already has full-pins fix (skip)" \
                      || say "  - notebook.py : patch render() to keep full part_pins lists  (+ backup)"
  [ "$pcb_done" = 1 ] && say "  - pcb_tools.py: already has connector-geometry (skip)" \
                      || say "  - pcb_tools.py: add _connector_geometry + hook into _finalize  (+ backup)"
  say "  - verify markers + ast.parse"
  say ""
  say "Run again with:   bash apply-pins-and-connector.sh go"
  exit 0
fi

# --------------------------------------------------------------------------
# Fix 1: notebook.py
# --------------------------------------------------------------------------
if [ "$nb_done" = 0 ]; then
  cp "$XNB" "$XNB.bak-$TS"; say "backed up -> $XNB.bak-$TS"
  python3 - "$XNB" <<'PATCHNB'
import sys
p = sys.argv[1]
src = open(p, encoding="utf-8").read()
assert "XORICS-FEATURE: notebook-full-pins" not in src, "already applied"

edits = [
 # let _short accept n=None meaning "no cap"
 ('def _short(text, n=120):\n    return " ".join(str(text).split())[:n]',
  'def _short(text, n=120):\n'
  '    s = " ".join(str(text).split())\n'
  '    return s if n is None else s[:n]'),

 # per-tool cap in render(): part_pins -> full, find_part -> 600, else 120
 ('                    a = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())\n'
  '                    lines.append(f"  - {name}({a}) -> {_short(self._cache[key])}")',
  '                    a = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())\n'
  '                    # XORICS-FEATURE: notebook-full-pins -- the pin list the coder must connect to\n'
  '                    # must survive verbatim; part_pins is never capped, find_part keeps its top-match pins.\n'
  '                    cap = {"part_pins": None, "find_part": 600}.get(name, 120)\n'
  '                    lines.append(f"  - {name}({a}) -> {_short(self._cache[key], cap)}")'),
]
for anchor, repl in edits:
    n = src.count(anchor)
    assert n == 1, f"notebook anchor not unique (found {n}x): {anchor[:50]!r}"
    src = src.replace(anchor, repl)
open(p, "w", encoding="utf-8").write(src)
print("patched notebook.py (2 edits)")
PATCHNB
else
  say "notebook.py: full-pins fix already present, skipping"
fi

# --------------------------------------------------------------------------
# Fix 2: pcb_tools.py
# --------------------------------------------------------------------------
if [ "$pcb_done" = 0 ]; then
  cp "$PCB" "$PCB.bak-$TS"; say "backed up -> $PCB.bak-$TS"
  python3 - "$PCB" <<'PATCHPCB'
import sys
p = sys.argv[1]
src = open(p, encoding="utf-8").read()
assert "XORICS-FEATURE: connector-geometry" not in src, "already applied"

geo_fn = (
"def _connector_geometry(query: str):\n"
"    \"\"\"Map an explicit header geometry in the query to the canonical KiCad generic-connector\n"
"    symbol: '2x16' / '2 x 16' / '02x16' -> Connector_Generic:Conn_02x16_Odd_Even; '1x8' ->\n"
"    Conn_01x08. KiCad Conn_* names are zero-padded; double-row uses the _Odd_Even pin-numbering\n"
"    variant, single-row has no suffix. Returns ('Connector_Generic', '<symbol>') or None.\n"
"    Geometry must be EXPLICIT -- a bare 'header' stays ambiguous and is left to normal ordering.\n"
"    XORICS-FEATURE: connector-geometry\n"
"    \"\"\"\n"
"    m = re.search(r\"\\b(\\d{1,2})\\s*x\\s*(\\d{1,3})\\b\", query.lower())\n"
"    if not m:\n"
"        return None\n"
"    rows, per = int(m.group(1)), int(m.group(2))\n"
"    if rows < 1 or per < 1:\n"
"        return None\n"
"    if rows == 1:\n"
"        return (\"Connector_Generic\", f\"Conn_01x{per:02d}\")\n"
"    if rows == 2:\n"
"        return (\"Connector_Generic\", f\"Conn_02x{per:02d}_Odd_Even\")\n"
"    return (\"Connector_Generic\", f\"Conn_{rows:02d}x{per:02d}\")\n"
)

edits = [
 # 1) insert the helper just before _order_by_category
 ("def _order_by_category(query: str, cat: dict, data: dict) -> list:",
  geo_fn + "\n\ndef _order_by_category(query: str, cat: dict, data: dict) -> list:"),

 # 2) hook it into _finalize: for the connector category, an explicit NxM geometry becomes the
 #    per-query "canonical" so the existing inject+order-first path pins it to the top.
 ('    canon = cat.get("canonical")\n'
  '    if canon:\n'
  '        have = {l + ":" + n for l, n in data["verified"]}',
  '    if "J" in cat.get("prefixes", ()):               # connector: honor an explicit NxM geometry\n'
  '        geo = _connector_geometry(query)\n'
  '        if geo:\n'
  '            cat = {**cat, "canonical": geo}           # pin the generic header symbol to the top\n'
  '    canon = cat.get("canonical")\n'
  '    if canon:\n'
  '        have = {l + ":" + n for l, n in data["verified"]}'),
]
for anchor, repl in edits:
    n = src.count(anchor)
    assert n == 1, f"pcb_tools anchor not unique (found {n}x): {anchor[:50]!r}"
    src = src.replace(anchor, repl)
open(p, "w", encoding="utf-8").write(src)
print("patched pcb_tools.py (2 edits)")
PATCHPCB
else
  say "pcb_tools.py: connector-geometry already present, skipping"
fi

# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------
say ""
say "verify:"
say "  notebook-full-pins  : $(grep -c 'XORICS-FEATURE: notebook-full-pins' "$XNB")  (expect 1)"
say "  connector-geometry  : $(grep -c 'XORICS-FEATURE: connector-geometry' "$PCB")  (expect 1)"
say "  _connector_geometry : $(grep -c 'def _connector_geometry' "$PCB")  (expect 1)"
say ""
say "syntax check:"
python3 -c "import ast; ast.parse(open('$XNB',encoding='utf-8').read()); print('  notebook.py  OK')"
python3 -c "import ast; ast.parse(open('$PCB',encoding='utf-8').read()); print('  pcb_tools.py OK')"
say ""
say "DONE. Backups: $XNB.bak-$TS , $PCB.bak-$TS"
say "Re-run the ATmega328P design -- the ATmega's full pin list now stays pinned, and"
say "find_part('Pin Header 2x16') should resolve to Connector_Generic:Conn_02x16_Odd_Even."
