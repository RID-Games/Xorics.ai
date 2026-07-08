#!/usr/bin/env python3
# Hermetic test for the tool-permissions primitive (Brick B1) in xorics.py.
# Imports xorics, isolates the in-memory grant set, and checks the deny-all default
# + grant/revoke behavior against the privileged-tools set. No I/O, no sandbox.
import sys
import xorics

PASS = 0
FAIL = 0


def check(label, cond):
    """House-style check: print ok/FAIL per line, increment counters."""
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")


# Hermetic isolation — start each run from a known empty grant set, regardless of
# what state an earlier test run (or the live process) left behind.
xorics._TOOL_GRANTS.clear()

print("--- _PRIVILEGED_TOOLS membership ---")
check("_PRIVILEGED_TOOLS contains write_file",
      "write_file" in xorics._PRIVILEGED_TOOLS)
check("_PRIVILEGED_TOOLS contains str_replace",
      "str_replace" in xorics._PRIVILEGED_TOOLS)

print("--- default deny ---")
check("_is_granted('write_file') is False by default",
      xorics._is_granted("write_file") is False)
check("_is_granted('str_replace') is False by default",
      xorics._is_granted("str_replace") is False)

print("--- grant then revoke ---")
check("_grant_tool('write_file') returns True",
      xorics._grant_tool("write_file") is True)
check("after grant, _is_granted('write_file') flips True",
      xorics._is_granted("write_file") is True)
check("_revoke_tool('write_file') returns True (was granted)",
      xorics._revoke_tool("write_file") is True)
check("after revoke, _is_granted('write_file') flips back False",
      xorics._is_granted("write_file") is False)
check("a second _revoke_tool('write_file') returns False (already revoked)",
      xorics._revoke_tool("write_file") is False)

print("--- non-privileged names are refused ---")
check("_grant_tool('read_file') returns False (non-privileged)",
      xorics._grant_tool("read_file") is False)
check("_is_granted('read_file') stays False after the refused grant",
      xorics._is_granted("read_file") is False)

print(f"\n{PASS}/{PASS + FAIL} checks passed")
sys.exit(0 if FAIL == 0 else 1)