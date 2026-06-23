#!/usr/bin/env bash
# Xorics — local verification. Run from ~/xorics-ai.
#   ./run_tests.sh          # tree state + every hermetic unit suite (fast: no GPU, no toolchain)
#   ./run_tests.sh --probe  # the above, then the LIVE end-to-end probe (slow; needs llama-swap up)
#
# Hermetic suites prove the WIRING. The --probe run proves it end-to-end on the real models +
# the real grader/compiler — the only thing that actually counts as "proven" (green mocks lie).
set -u
cd "$(dirname "$0")" || exit 1
export GIT_PAGER=cat
PY=venv/bin/python
[ -x "$PY" ] || PY=python3

bar() { printf '%s\n' "────────────────────────────────────────────────────────"; }

bar; echo "TREE STATE"; bar
echo "branch : $(git branch --show-current 2>/dev/null)"
echo "HEAD   : $(git log --oneline -1 2>/dev/null)"
echo "dirty  : $(git status --porcelain 2>/dev/null | wc -l | tr -d ' ') uncommitted path(s)"
echo
echo "honesty gate  : finalize_design=$(grep -c 'def finalize_design' xorics.py) footer=$(grep -c '_append_manifest_footer' xorics.py)  (want 1 / 1)"
echo "firmware gate : CompileResult=$(grep -c 'class CompileResult' firmware_tools.py)  (want 1)"
echo "grader flag   : $(grep -n 'FAIL_ON_NETLIST_ERRORS *=' pcb_tools.py | head -1)"
echo

bar; echo "UNIT SUITES (hermetic — no GPU, no toolchain)"; bar
pass=0; fail=0
for t in test_*.py; do
  [ -e "$t" ] || continue
  out="$("$PY" "$t" 2>&1)"; rc=$?
  summ="$(printf '%s\n' "$out" | grep -Eo '([0-9]+/[0-9]+ passed)|([0-9]+ passed, [0-9]+ failed)' | tail -1)"
  if [ "$rc" -eq 0 ]; then
    printf '  PASS  %-30s %s\n' "$t" "${summ:-(exit 0)}"; pass=$((pass + 1))
  else
    printf '  FAIL  %-30s %s\n' "$t" "${summ:-(exit $rc)}"; fail=$((fail + 1))
    printf '%s\n' "$out" | tail -6 | sed 's/^/          | /'
  fi
done
echo
echo "Suites: ${pass} passed, ${fail} failed"

if [ "${1:-}" = "--probe" ]; then
  echo
  bar; echo "LIVE PROBE (real models — minutes; needs llama-swap on :9090, in tmux)"; bar
  if ! command -v arduino-cli >/dev/null 2>&1; then
    echo "  [warn] arduino-cli missing — CHECK 3 (firmware accept-path) cannot compile."
  fi
  "$PY" probe_honesty_gate.py --pass
fi
