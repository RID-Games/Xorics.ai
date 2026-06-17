#!/usr/bin/env bash
# apply-connector-count.sh
#   XORICS-FEATURE: connector-geometry-count  (pcb_tools.py)
#
#   Extends _connector_geometry so a BARE pin-count resolves, not just NxM:
#     'Header 2 Pin' / '2-pin' / '10 position' / '2 way'  ->  Connector_Generic:Conn_01x02 (etc.)
#   A bare count is treated as a single-row 1xN header (the classic breakaway strip, which is what
#   '2-pin male header for each I/O' means). Two-row stays explicit via the NxM form ('2x16').
#   Validated before injection upstream, so a miss degrades to normal ordering -- never breaks.
#
# Requires the connector-geometry function from apply-pins-and-connector.sh to already be present.
# Plan-by-default:  bash apply-connector-count.sh        # preview
# Apply:            bash apply-connector-count.sh go
set -euo pipefail

GO="${1:-}"
ROOT="${XORICS_ROOT:-$HOME/xorics-ai}"
PCB="$ROOT/pcb_tools.py"
TS="$(date +%Y%m%d-%H%M%S)"
say(){ printf '%s\n' "$*"; }

[ -f "$PCB" ] || { say "ERROR: $PCB not found. Set XORICS_ROOT to your xorics-ai dir."; exit 1; }

if ! grep -q 'XORICS-FEATURE: connector-geometry' "$PCB"; then
  say "ERROR: base connector-geometry not found in pcb_tools.py."
  say "Run apply-pins-and-connector.sh first, then this."
  exit 1
fi
if grep -q 'XORICS-FEATURE: connector-geometry-count' "$PCB"; then
  say "Already applied (connector-geometry-count present). Nothing to do."
  exit 0
fi

if [ "$GO" != "go" ]; then
  say "PLAN (no changes will be made):"
  say "  - backup  $PCB -> $PCB.bak-$TS"
  say "  - patch   _connector_geometry: add bare pin-count fallback (1 anchored edit)"
  say "  - verify  marker + ast.parse"
  say ""
  say "Run again with:   bash apply-connector-count.sh go"
  exit 0
fi

cp "$PCB" "$PCB.bak-$TS"; say "backed up -> $PCB.bak-$TS"

python3 - "$PCB" <<'PATCHEOF'
import sys
p = sys.argv[1]
src = open(p, encoding="utf-8").read()
assert "XORICS-FEATURE: connector-geometry-count" not in src, "already applied"

anchor = r'''    m = re.search(r"\b(\d{1,2})\s*x\s*(\d{1,3})\b", query.lower())
    if not m:
        return None
    rows, per = int(m.group(1)), int(m.group(2))'''

repl = r'''    q = query.lower()
    m = re.search(r"\b(\d{1,2})\s*x\s*(\d{1,3})\b", q)
    if not m:
        # bare pin-count form: '2 pin', '2-pin', '10 position', '2 way' -> single-row 1xN header.
        # A bare count is single-row by convention; two-row needs the explicit NxM form above.
        # XORICS-FEATURE: connector-geometry-count
        c = re.search(r"\b(\d{1,3})\s*[- ]?\s*(?:pin|pins|pos|position|positions|way|ways|contact|contacts)\b", q)
        if c and int(c.group(1)) >= 1:
            return ("Connector_Generic", f"Conn_01x{int(c.group(1)):02d}")
        return None
    rows, per = int(m.group(1)), int(m.group(2))'''

n = src.count(anchor)
assert n == 1, f"anchor not unique (found {n}x) -- has _connector_geometry changed?"
src = src.replace(anchor, repl)
open(p, "w", encoding="utf-8").write(src)
print("patched pcb_tools.py (1 edit)")
PATCHEOF

say ""
say "verify:"
say "  connector-geometry-count : $(grep -c 'XORICS-FEATURE: connector-geometry-count' "$PCB")  (expect 1)"
say ""
say "syntax check:"
python3 -c "import ast; ast.parse(open('$PCB',encoding='utf-8').read()); print('  pcb_tools.py OK')"
say ""
say "DONE. Backup at $PCB.bak-$TS"
say "find_part('Header 2 Pin') -> Connector_Generic:Conn_01x02. Re-run the ATmega design."
