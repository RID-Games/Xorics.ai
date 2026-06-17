#!/usr/bin/env bash
# apply-license-headers.sh
#   XORICS-FEATURE: license-headers
#
#   Prepends the AGPL-3.0 notice + the section-7 output-exception pointer to every top-level
#   *.py source file in the Xorics dir. Idempotent (skips files already carrying the header),
#   shebang-aware (inserts AFTER a '#!' line so the file still runs), plan-by-default, backs up
#   each file it touches. Does NOT recurse, so venvs (venv/, skidl-venv/) are never touched.
#
#   Pair with:
#     - LICENSE            (verbatim AGPL-3.0 — fetch from gnu.org; see README)
#     - LICENSE-EXCEPTION  (the section-7 output exception this header points to)
#
# Plan-by-default:  bash apply-license-headers.sh        # preview which files get a header
# Apply:            bash apply-license-headers.sh go
set -euo pipefail

GO="${1:-}"
ROOT="${XORICS_ROOT:-$HOME/xorics-ai}"
TS="$(date +%Y%m%d-%H%M%S)"
MARK="This file is part of Xorics."
say(){ printf '%s\n' "$*"; }

shopt -s nullglob
pyfiles=("$ROOT"/*.py)
shopt -u nullglob
[ "${#pyfiles[@]}" -gt 0 ] || { say "No *.py files in $ROOT. Set XORICS_ROOT."; exit 1; }

# Partition into needs-header vs already-done.
todo=(); done_=()
for f in "${pyfiles[@]}"; do
  if grep -qF "$MARK" "$f"; then done_+=("$f"); else todo+=("$f"); fi
done

if [ "${#todo[@]}" -eq 0 ]; then
  say "Already applied (all ${#pyfiles[@]} files carry the header). Nothing to do."; exit 0
fi

if [ "$GO" != "go" ]; then
  say "PLAN (no changes will be made):"
  for f in "${todo[@]}"; do say "  + header -> $(basename "$f")"; done
  for f in "${done_[@]}"; do say "  . already has header (skip) -> $(basename "$f")"; done
  say ""
  say "Run again with:   bash apply-license-headers.sh go"
  exit 0
fi

HEADER_FILE="$(mktemp)"; trap 'rm -f "$HEADER_FILE"' EXIT
cat > "$HEADER_FILE" <<'HDR'
# Xorics — a self-hosted local AI assistant for embedded / PCB engineering.
# Copyright (C) 2026 Zawayix
#
# This file is part of Xorics. Xorics is free software: you can redistribute it
# and/or modify it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Xorics is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE. See the GNU Affero General Public License for details.
#
# You should have received a copy of the GNU Affero General Public License along
# with Xorics. If not, see <https://www.gnu.org/licenses/>.
#
# ADDITIONAL PERMISSION (AGPLv3 section 7): designs and files produced by RUNNING
# Xorics, and any fragments it embeds into that output, are NOT covered by the
# AGPL — you may license your generated designs as you wish. See LICENSE-EXCEPTION.
HDR

for f in "${todo[@]}"; do
  cp "$f" "$f.bak-$TS"
  python3 - "$f" "$HEADER_FILE" <<'PY'
import sys
target, hdrf = sys.argv[1], sys.argv[2]
src = open(target, encoding="utf-8").read()
hdr = open(hdrf, encoding="utf-8").read().rstrip("\n") + "\n"
assert "This file is part of Xorics." not in src, "already applied"
lines = src.splitlines(keepends=True)
if lines and lines[0].startswith("#!"):
    out = lines[0] + hdr + "\n" + "".join(lines[1:])   # keep shebang first
else:
    out = hdr + "\n" + src
open(target, "w", encoding="utf-8").write(out)
print(f"  headered {target}")
PY
  say "    backed up -> $(basename "$f").bak-$TS"
done

say ""
say "verify (each should print 1):"
for f in "${todo[@]}"; do
  say "  $(basename "$f"): $(grep -cF "$MARK" "$f")"
done
say ""
say "syntax check (headers are comments — must still parse):"
for f in "${todo[@]}"; do
  python3 -c "import ast; ast.parse(open('$f',encoding='utf-8').read()); print('  OK $(basename "$f")')"
done
say ""
say "DONE."
