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


# --- Group 4: _design_spec_targets basenames path-qualified names (one-liner #1) -
# A spec may name a file as `xorics-ai/capabilities.py` or `./capabilities.py`; the
# target resolver must reduce those to the repo basename so the gate can match them
# against what was read. Pre-fix it compared the full token to os.listdir() and so a
# path-qualified name silently resolved to {} (no target), letting an ungrounded spec
# pass the >=1-read floor.
_st = xorics._design_spec_targets
check("(4) bare 'capabilities.py' resolves to {capabilities.py}",
      _st("capabilities.py") == {"capabilities.py"})
check("(4) path-qualified 'xorics-ai/capabilities.py' resolves to basename",
      _st("xorics-ai/capabilities.py") == {"capabilities.py"})
check("(4) unknown .py dropped, real repo .py kept",
      _st("touch nonexistent_module.py and capabilities.py") == {"capabilities.py"})

# Gate-level regression: a path-qualified target that was NEVER read must BLOCK. This
# is the bug one-liner #1 fixes — pre-fix `named` was empty for the path-qualified
# token, so reading any unrelated file (skills.py) satisfied the floor and the spec
# slipped through. Post-fix the basename matches, `missing` is non-empty, gate blocks.
xorics._agent_loop = _stub_agent_loop_gate
try:
    _g_text[0] = ("PLAN: touch capabilities.py.\n" + _MARK
                  + "\nIn xorics-ai/capabilities.py, add the new binding.")
    _g_msgs[0] = [_amsg("skills.py")]
    out = xorics.run_design("g")
    check("(4) path-qualified spec BLOCKED when only an unrelated file was read",
          "SELF-EDIT SPEC BLOCKED" in out and _MARK not in out)

    _g_msgs[0] = [_amsg("/home/zawayix/xorics-ai/capabilities.py")]
    out = xorics.run_design("g")
    check("(4) same spec kept once the real target was read (basename match)",
          _MARK in out and "BLOCKED" not in out)
finally:
    xorics._agent_loop = _saved_agent_loop


# --- Group 5: _selfedit_incomplete flags named-but-unwritten targets (primary fix) -
# The canonical bug: a 2-file self-edit writes one file and reports clean success,
# silently dropping the other. The completeness gate names any in-repo .py target that
# was asked for but not staged. Guards: empty `pending` is a no-op (already visible, no
# flag); a task naming no repo .py can't be judged (no flag); basenames are compared so
# path-qualified names and ./-prefixed staged paths line up.
_si = xorics._selfedit_incomplete
check("(5) 2-file task, only 1 written -> the dropped file is flagged",
      _si("edit xorics.py and add a group to test_design.py", ["xorics.py"])
      == ["test_design.py"])
check("(5) 2-file task, both written -> nothing flagged",
      _si("edit xorics.py and add a group to test_design.py",
          ["xorics.py", "test_design.py"]) == [])
check("(5) 1-file task, that file written -> nothing flagged",
      _si("edit only xorics.py", ["xorics.py"]) == [])
check("(5) path-qualified named target, different file written -> basename flagged",
      _si("edit xorics-ai/capabilities.py", ["xorics.py"]) == ["capabilities.py"])
check("(5) nothing staged -> no flag (a total no-op is already visible)",
      _si("add a docstring to capabilities.py", []) == [])
check("(5) task names no repo .py -> no flag (completeness can't be judged)",
      _si("fix the typo in the startup banner", ["xorics.py"]) == [])
check("(5) staged path with ./ prefix is basename-compared, not flagged",
      _si("edit xorics.py", ["./xorics.py"]) == [])


# --- Group 6: xorics.py compiles with zero SyntaxWarning (one-liner #2) ----------
# The planning-guide docstring contained a bare regex `\w` in a non-raw string, which
# emits a SyntaxWarning at compile time on 3.12+. Raw-stringing it silences that. Force
# "always" so the warnings registry can't suppress a repeat, and assert none are raised.
import warnings as _warnings
_src = open(xorics.__file__).read()
with _warnings.catch_warnings(record=True) as _w:
    _warnings.simplefilter("always")
    compile(_src, xorics.__file__, "exec")
_syntaxwarns = [x for x in _w if issubclass(x.category, SyntaxWarning)]
check("(6) xorics.py compiles with zero SyntaxWarning", len(_syntaxwarns) == 0)


print(f"\n{PASS}/{PASS + FAIL} checks passed")
sys.exit(0 if FAIL == 0 else 1)