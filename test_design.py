#!/usr/bin/env python3
# Xorics — test: /design mode is READ-ONLY and run_design never stages a self-edit.
# Hermetic; stubs xorics._agent_loop, xorics.write_file, xorics._selfedit_reset,
# xorics._selfedit_changed_files so no model is called and nothing is written.

import sys

import xorics

PASS, FAIL = 0, 0


def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")


# --- Group 1: PLAN_TOOLS is read-only -----------------------------------------
names = [t["function"]["name"] for t in xorics.PLAN_TOOLS]
check("(1) PLAN_TOOLS == ['read_file']", names == ["read_file"])
check('(1) "write_file" not in PLAN_TOOLS', "write_file" not in names)


# --- Group 2: run_design plans without staging or writing ---------------------
_saved_agent_loop = xorics._agent_loop
_saved_write_file = xorics.write_file
_saved_selfedit_reset = xorics._selfedit_reset
_saved_selfedit_changed_files = xorics._selfedit_changed_files


_write_calls = 0
_reset_calls = 0
_changed_calls = 0


def _stub_agent_loop(model, messages, tools, *, checkpoint, tag):
    """Fake _agent_loop returning the 4-tuple run_design expects."""
    return ("FAKE PLAN", [], None, None)


def _spy_write(*_args, **_kwargs):
    global _write_calls
    _write_calls += 1
    return "spy-write: not called"


def _spy_reset():
    global _reset_calls
    _reset_calls += 1


def _spy_changed_files():
    global _changed_calls
    _changed_calls += 1
    return []


xorics._agent_loop = _stub_agent_loop
xorics.write_file = _spy_write
xorics._selfedit_reset = _spy_reset
xorics._selfedit_changed_files = _spy_changed_files

try:
    out = xorics.run_design("simulated goal")
    check("(2) run_design(...) returned a str", isinstance(out, str))
    check("(2) write_file was called 0 times", _write_calls == 0)
    check("(2) _selfedit_reset was called 0 times", _reset_calls == 0)
    check("(2) _selfedit_changed_files was called 0 times", _changed_calls == 0)
finally:
    xorics._agent_loop = _saved_agent_loop
    xorics.write_file = _saved_write_file
    xorics._selfedit_reset = _saved_selfedit_reset
    xorics._selfedit_changed_files = _saved_selfedit_changed_files


# --- Group 3: read-gate — no self-edit spec unless the target was read --------
_MARK = xorics._DESIGN_SPEC_MARKER


def _amsg(path):
    """An assistant turn that recorded a read_file call on `path` (the shape _agent_loop logs)."""
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": "x", "type": "function",
                            "function": {"name": "read_file",
                                         "arguments": '{"path": "%s"}' % path}}]}


_g_text = [""]      # what the stubbed planner "returns" as its final text
_g_msgs = [[]]      # the transcript the stubbed planner produced


def _stub_agent_loop_gate(model, messages, tools, *, checkpoint, tag):
    return (_g_text[0], _g_msgs[0], None, None)


xorics._agent_loop = _stub_agent_loop_gate
try:
    _plan = "PLAN: add the CAPABILITIES_BY_DOMAIN binding to capabilities.py.\n"
    _spec = _MARK + "\nIn capabilities.py, add the CAPABILITIES_BY_DOMAIN binding."
    _g_text[0] = _plan + _spec

    _g_msgs[0] = [_amsg("/home/zawayix/xorics-ai/capabilities.py")]
    out = xorics.run_design("g")
    check("(3) spec kept when named target (capabilities.py) was read", _MARK in out and "BLOCKED" not in out)

    _g_msgs[0] = []
    out = xorics.run_design("g")
    check("(3) spec blocked + plan kept when nothing was read",
          "SELF-EDIT SPEC BLOCKED" in out and _MARK not in out and out.startswith("PLAN:"))

    _g_msgs[0] = [_amsg("skills.py")]
    out = xorics.run_design("g")
    check("(3) spec blocked when only a non-target file was read", "BLOCKED" in out and _MARK not in out)

    _g_text[0] = "PLAN: reword the planner guide.\n" + _MARK + "\nTighten the planner guide wording."
    _g_msgs[0] = [_amsg("xorics.py")]
    out = xorics.run_design("g")
    check("(3) spec kept when it names no repo file and a read happened", _MARK in out and "BLOCKED" not in out)
finally:
    xorics._agent_loop = _saved_agent_loop


print(f"\n{PASS}/{PASS + FAIL} checks passed")
sys.exit(0 if FAIL == 0 else 1)