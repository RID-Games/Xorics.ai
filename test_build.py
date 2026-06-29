#!/usr/bin/env python3
"""Hermetic test for the /build command (run_build): no model, no network.

run_build must hand the last /design spec to /selfedit. When no spec is on hand
(_LAST_DESIGN_SPEC is None), it returns a one-line guidance string starting with
"Nothing to build". When a spec IS on hand, it forwards (spec, brain=...) to
run_self_edit and returns whatever that returns. The test verifies both paths
by monkey-patching run_self_edit to a recorder (so we never touch a model or
sandbox) and asserts (a) the recorder saw the saved spec, (b) it saw the brain
we passed, and (c) the value run_build returned equals the recorder's return.

Also asserts the module-level _LAST_DESIGN_SPEC slot exists so a future
accidental rename would fail loudly here, not at the REPL.
"""
import sys
import xorics


def main() -> int:
    passed = 0
    failed = 0

    def check(label, cond):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  PASS  {label}")
        else:
            failed += 1
            print(f"  FAIL  {label}")

    # 0. The slot must exist on the module so a future rename fails loudly here.
    check("_LAST_DESIGN_SPEC slot exists on xorics module",
          hasattr(xorics, "_LAST_DESIGN_SPEC"))

    # 1. Empty-spec path: with no spec on hand, run_build() must hand back the
    #    one-liner so the user knows to /design first.
    xorics._LAST_DESIGN_SPEC = None
    out_empty = xorics.run_build()
    check("run_build() with no spec returns the 'Nothing to build' one-liner",
          isinstance(out_empty, str) and out_empty.startswith("Nothing to build"))

    # 2. Spec-on-hand path: monkey-patch run_self_edit with a recorder so we
    #    can verify (task, brain) forwarding WITHOUT touching the model, the
    #    sandbox, or the live repo. Save the real one first so we restore it.
    recorded = {}

    def fake_run_self_edit(task, brain=None):
        recorded["task"] = task
        recorded["brain"] = brain
        return "STAGED"

    real_run_self_edit = xorics.run_self_edit
    xorics.run_self_edit = fake_run_self_edit
    try:
        xorics._LAST_DESIGN_SPEC = "the spec"
        out_full = xorics.run_build(brain="M")
    finally:
        xorics.run_self_edit = real_run_self_edit

    check("run_build(brain='M') returns the value run_self_edit returned",
          out_full == "STAGED")
    check("run_self_edit received the saved _LAST_DESIGN_SPEC",
          recorded.get("task") == "the spec")
    check("run_self_edit received the brain kwarg passed through",
          recorded.get("brain") == "M")

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())