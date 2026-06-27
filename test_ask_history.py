# Xorics — test the history-aware ask() change. Plain-assert, no pytest.
# Run from the repo root with the venv active:  python3 test_ask_history.py
#
# Imports the REAL xorics (full tool stack present on the box) and monkeypatches
# _agent_loop to CAPTURE the message list ask() builds — the one thing the edit
# touches. No model server needed: the loop is stubbed, so this is a fast unit check
# of message assembly. The live proof (a chat that actually remembers across turns)
# comes once the bridge wires this in — #lesson: a green unit test is necessary, not
# sufficient.

import sys

try:
    import xorics
except ImportError as e:
    raise SystemExit(f"can't import xorics ({e}) — run from ~/xorics-ai with the venv active")

_pass = 0
_fail = 0


def check(label, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  ok   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}")


_cap = {}


def _fake_loop(model, messages, tools, *, checkpoint, tag):
    _cap.clear()
    _cap["messages"] = [dict(m) for m in messages]
    _cap["model"] = model
    _cap["tag"] = tag
    return ("ok reply", messages, None, {})


xorics._agent_loop = _fake_loop


def roles(msgs):
    return [m["role"] for m in msgs]


try:
    xorics.BRAIN = xorics.MANAGER

    # 1. backward-compatible: no history == pre-memory single-shot
    xorics.ask("hello")
    m = _cap["messages"]
    check("no-history: exactly [system, user]", len(m) == 2 and roles(m) == ["system", "user"])
    check("no-history: user turn carries the message", m[1]["content"] == "hello")
    check("no-history: system mentions Xorics", "Xorics" in m[0]["content"])
    check("manager mode routes to the manager model", _cap["model"] == xorics.MANAGER)

    # 2. with history: spliced BETWEEN system and the new turn
    hist = [{"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"}]
    xorics.ask("third", history=hist)
    m = _cap["messages"]
    check("history: length is system + 2 prior + new = 4", len(m) == 4)
    check("history: order is [system, user, assistant, user]",
          roles(m) == ["system", "user", "assistant", "user"])
    check("history: system stays first", m[0]["role"] == "system")
    check("history: prior turns preserved in order",
          m[1]["content"] == "first" and m[2]["content"] == "second")
    check("history: the NEW turn is last, not buried", m[3]["content"] == "third" and m[3]["role"] == "user")

    # 3. empty / None history -> no splice
    xorics.ask("x", history=[])
    check("empty-list history -> [system, user]", len(_cap["messages"]) == 2)
    xorics.ask("y", history=None)
    check("None history -> [system, user]", len(_cap["messages"]) == 2)

    # 4. new turn appended exactly once
    long_hist = [{"role": "user", "content": f"u{i}"} for i in range(5)] + \
                [{"role": "assistant", "content": f"a{i}"} for i in range(5)]
    xorics.ask("now", history=long_hist)
    m = _cap["messages"]
    check("history: 10 prior + system + new = 12", len(m) == 12)
    check("history: 'now' appears exactly once and last",
          sum(1 for x in m if x["content"] == "now") == 1 and m[-1]["content"] == "now")

    # 5. coder mode behaves the same
    xorics.BRAIN = xorics.CODER
    xorics.ask("code this", history=[{"role": "user", "content": "earlier"}])
    m = _cap["messages"]
    check("coder mode: history still splices (system + 1 + new = 3)", len(m) == 3)
    check("coder mode: system is the coding-specialist prompt", "coding specialist" in m[0]["content"])
    check("coder mode: routes to the coder model", _cap["model"] == xorics.CODER)
    xorics.BRAIN = xorics.MANAGER

    # 6. return value still str-compatible and carries built_path
    out = xorics.ask("ping")
    check("ask() still returns a str-compatible result", str(out) == "ok reply")
    check("ask() result still carries built_path attr", hasattr(out, "built_path"))

finally:
    pass

print(f"\n{_pass} passed, {_fail} failed")
raise SystemExit(1 if _fail else 0)
