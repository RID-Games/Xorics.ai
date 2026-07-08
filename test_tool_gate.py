#!/usr/bin/env python3
"""XORICS-FEATURE: tool-permissions — hermetic tests for the privileged-call gate."""
import sys
import inspect
import xorics


PASS = 0
FAIL = 0


def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")


# Start clean — no grants carry over from a prior run.
xorics._TOOL_GRANTS.clear()


# 1. write_file is privileged + ungranted -> refused with /grant hint.
msg = xorics._gate_privileged_call("write_file", "chat")
check("write_file ungranted refuses",
      isinstance(msg, str) and "PERMISSION REQUIRED" in msg and "/grant write_file" in msg)

# 2. str_replace is privileged + ungranted -> refused with /grant hint.
msg = xorics._gate_privileged_call("str_replace", "chat")
check("str_replace ungranted refuses",
      isinstance(msg, str) and "PERMISSION REQUIRED" in msg and "/grant str_replace" in msg)

# 3. read_file is non-privileged -> None regardless of grants.
check("read_file non-privileged untouched",
      xorics._gate_privileged_call("read_file", "chat") is None)

# 4. After /grant, the gate lets it through.
xorics._grant_tool("write_file")
check("write_file granted -> None",
      xorics._gate_privileged_call("write_file", "chat") is None)

# 5. selfedit tag is exempt even without a grant.
xorics._TOOL_GRANTS.discard("str_replace")
check("selfedit tag exempt (no grant needed)",
      xorics._gate_privileged_call("str_replace", "selfedit") is None)

# 6. The gate call is actually wired into the agent loop chokepoint.
src = inspect.getsource(xorics._agent_loop)
check("_gate_privileged_call(name, tag) wired into _agent_loop",
      "_gate_privileged_call(name, tag)" in src)

# 7. *.log now in the selfedit staging ignore tuple.
check("*.log in _SELFEDIT_STAGE_IGNORE",
      "*.log" in xorics._SELFEDIT_STAGE_IGNORE)


print(f"\n{PASS}/{PASS + FAIL} checks passed")
sys.exit(0 if FAIL == 0 else 1)
