#!/usr/bin/env python3
"""Hermetic check for active_tools() — XORICS-FEATURE: tool-permissions.

The manager schema must EXPOSE write_file + str_replace so the model can see them,
but the chokepoint gate (_gate_privileged_call) still DENIES the actual call until
/grant. This test verifies both halves without touching the live repo:

  - MANAGER side: write_file and str_replace are in active_tools() names, and the
    resulting schema has no duplicate tool names.
  - CODER side: active_tools() returns exactly the CODER_TOOLS name list (unchanged).

Saves xorics.BRAIN, restores it in a `finally` so a failure can't leave the REPL in
a different mode than the user left it. House check pattern with PASS/FAIL counters;
the trailing print + sys.exit makes this a runnable CLI check too.

If pytest ever discovers this file, the `if __name__ == "__main__":` guard keeps it
from running at import time (no SystemExit on collection); running it directly
prints the summary and exits 0 on green / 1 on red.
"""
import sys

import xorics

PASS = 0
FAIL = 0


def check(label, ok, detail=""):
    """Increment the right counter, print PASS/FAIL with the label and optional detail."""
    global PASS, FAIL
    tag = "PASS" if ok else "FAIL"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{tag}] {label}{suffix}")


def main():
    try:
        old = xorics.BRAIN

        # ---- MANAGER side: schema must expose the privileged tools ----
        xorics.BRAIN = xorics.MANAGER
        names = [t["function"]["name"] for t in xorics.active_tools()]

        check("write_file is exposed in the MANAGER schema",
              "write_file" in names,
              f"names={names}")
        check("str_replace is exposed in the MANAGER schema",
              "str_replace" in names,
              f"names={names}")
        check("MANAGER schema has no duplicate tool names",
              len(names) == len(set(names)),
              f"len={len(names)}, unique={len(set(names))}")

        # ---- CODER side: schema must be unchanged — exactly the CODER_TOOLS name list ----
        xorics.BRAIN = xorics.CODER
        check("CODER active_tools() equals the CODER_TOOLS name list",
              [t["function"]["name"] for t in xorics.active_tools()]
              == [t["function"]["name"] for t in xorics.CODER_TOOLS])
    finally:
        # Restore no matter what — a failure mustn't leave BRAIN in MANAGER mode
        # if the user started in CODER (manual /code) mode.
        xorics.BRAIN = old

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())