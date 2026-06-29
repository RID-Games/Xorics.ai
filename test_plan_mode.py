#!/usr/bin/env python3
# Xorics — test: /plan mode swaps ask()'s system prompt to the planner guide and
# restricts to read-only tools, WITHOUT disturbing normal manager chat. Hermetic;
# stubs xorics._agent_loop to capture what ask() hands the loop, and neutralises the
# honesty-gate footer so the test is isolated to prompt + tool selection.

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


# --- Group 1: the plan-mode surface exists and is read-only by construction ----
check("(1) PLAN_MODE exists and defaults to False", xorics.PLAN_MODE is False)
check("(1) _PLANNER_GUIDE is a non-trivial str", isinstance(xorics._PLANNER_GUIDE, str)
      and len(xorics._PLANNER_GUIDE) > 200)
check("(1) planner guide declares read_file as its tool", "read_file" in xorics._PLANNER_GUIDE)
check("(1) planner guide forbids write_file", "NOT write_file" in xorics._PLANNER_GUIDE
      or "not write_file" in xorics._PLANNER_GUIDE.lower())
check("(1) PLAN_TOOLS is read-only", [t["function"]["name"] for t in xorics.PLAN_TOOLS] == ["read_file"])


# --- Group 2: ask() honours PLAN_MODE for both the system prompt and the tools -
_captured = {}
_saved_loop = xorics._agent_loop
_saved_footer = xorics._append_manifest_footer
_saved_deliv = xorics._load_deliverables
_saved_plan = xorics.PLAN_MODE
_saved_brain = xorics.BRAIN


def _stub_loop(brain, messages, tools, *, checkpoint, tag):
    """Capture the system prompt and toolset ask() built, and short-circuit the loop."""
    _captured["system"] = messages[0]["content"]
    _captured["tools"] = tools
    _captured["history_len"] = len(messages) - 1            # minus the system turn
    return ("PLANNED", messages, None, None)


xorics._agent_loop = _stub_loop
xorics._append_manifest_footer = lambda text, outcome, before: text   # isolate from honesty gate
xorics._load_deliverables = lambda: []

try:
    # ---- plan mode (on the local manager brain) ----
    xorics.BRAIN = xorics.MANAGER
    xorics.PLAN_MODE = True
    out = xorics.ask("add a big feature X")
    check("(2) plan mode: system prompt is the planner guide",
          xorics._PLANNER_GUIDE[:80] in _captured["system"])
    check("(2) plan mode: manager routing is NOT in the prompt",
          xorics._MANAGER_ROUTING not in _captured["system"])
    check("(2) plan mode: tools restricted to PLAN_TOOLS (read-only)",
          _captured["tools"] == xorics.PLAN_TOOLS)
    check("(2) plan mode returned a str-compatible result", isinstance(str(out), str))

    # ---- plan mode carries conversation history (it is the chat loop, not /design) ----
    _hist = [{"role": "user", "content": "earlier"},
             {"role": "assistant", "content": "noted"}]
    xorics.ask("now break it down", history=_hist)
    check("(2) plan mode threads prior history into the turn", _captured["history_len"] == 3)

    # ---- normal manager chat is untouched when PLAN_MODE is off ----
    xorics.PLAN_MODE = False
    xorics.ask("hello there")
    check("(2) normal mode: manager routing IS in the prompt",
          xorics._MANAGER_ROUTING in _captured["system"])
    check("(2) normal mode: planner guide is NOT in the prompt",
          xorics._PLANNER_GUIDE[:80] not in _captured["system"])
    check("(2) normal mode: tools are the full manager toolset",
          _captured["tools"] == xorics.active_tools())
finally:
    xorics._agent_loop = _saved_loop
    xorics._append_manifest_footer = _saved_footer
    xorics._load_deliverables = _saved_deliv
    xorics.PLAN_MODE = _saved_plan
    xorics.BRAIN = _saved_brain


print(f"\n{PASS}/{PASS + FAIL} checks passed")
sys.exit(0 if FAIL == 0 else 1)
