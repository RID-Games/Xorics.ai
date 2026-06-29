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


print(f"\n{PASS}/{PASS + FAIL} checks passed")
sys.exit(0 if FAIL == 0 else 1)