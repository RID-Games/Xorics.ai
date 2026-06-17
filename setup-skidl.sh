#!/usr/bin/env bash
# setup-skidl.sh — recreate the SKiDL venv that pcb_tools.py shells out to.
#
# Builds ~/xorics-ai/skidl-venv (SKiDL 2.2.3) and wires the KiCad symbol-library
# paths into the venv's activate so find_part / check_circuit can load symbols.
# Idempotent: safe to re-run. Verifies by instantiating Device:R at the end.
set -euo pipefail

ROOT="$HOME/xorics-ai"
VENV="$ROOT/skidl-venv"
SYM="/usr/share/kicad/symbols"

if [ ! -d "$SYM" ]; then
  echo "ERROR: KiCad symbols not found at $SYM. Install kicad first." >&2
  exit 1
fi

echo "creating $VENV"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip >/dev/null
echo "installing SKiDL 2.2.3"
"$VENV/bin/pip" install "skidl==2.2.3"

# point SKiDL at the stock KiCad symbol libraries on every activate
act="$VENV/bin/activate"
if ! grep -q KICAD6_SYMBOL_DIR "$act"; then
  cat >> "$act" << 'ENV'

# --- Xorics: KiCad symbol libraries for SKiDL ---
export KICAD6_SYMBOL_DIR="/usr/share/kicad/symbols"
export KICAD7_SYMBOL_DIR="/usr/share/kicad/symbols"
export KICAD8_SYMBOL_DIR="/usr/share/kicad/symbols"
export KICAD9_SYMBOL_DIR="/usr/share/kicad/symbols"
ENV
  echo "wired KICAD*_SYMBOL_DIR into $act"
fi

echo "verifying SKiDL import + symbol access"
KICAD8_SYMBOL_DIR="$SYM" "$VENV/bin/python" - << 'PY'
import skidl
from skidl import Part
Part("Device", "R", dest="TEMPLATE")
print("SKiDL", skidl.__version__, "OK - loaded Device:R")
PY

echo "DONE. skidl-venv rebuilt at $VENV"
