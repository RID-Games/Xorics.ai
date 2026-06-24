"""Convergence guard — the coder researches a hard board forever (and overflows its 8K context)
instead of committing to a SKiDL script. The guard counts look-ups since the last validated circuit
and, past a threshold, forces a WRITE; it re-nudges if the coder keeps stalling and resets the moment
a script is validated. Hermetic: stubs xorics's heavy siblings and tests the firing RULE + tool
classification in isolation. The end-to-end "does the coder actually write after the nudge" is the
LIVE probe's job (green mocks lie). Runs anywhere:
    python test_convergence_guard.py        (or: pytest test_convergence_guard.py)
"""
import os
import sys
import types

def _stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m

_noop = lambda *a, **k: ""
class _OpenAI:
    def __init__(self, *a, **k): pass
class _Notebook:
    def __init__(self, *a, **k): pass

_stub("openai", OpenAI=_OpenAI)
_stub("datasheet_rag", search_datasheets=_noop)
_stub("web_datasheets", fetch_datasheet=_noop)
_stub("firmware_tools", compile_check=_noop, save_sketch=_noop)
_stub("notebook", Notebook=_Notebook)
_stub("pcb_tools", check_circuit=_noop, check_circuit_file=_noop, find_part=_noop,
      find_footprint=_noop, part_pins=_noop, save_circuit=_noop)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xorics  # noqa: E402

AT = xorics.RESEARCH_NUDGE_AT
EVERY = xorics.RESEARCH_NUDGE_EVERY


def test_does_not_nudge_below_threshold():
    assert not xorics._should_nudge(AT - 1, 0), "must not nudge before the threshold is reached"
    assert not xorics._should_nudge(0, 0)


def test_nudges_exactly_at_threshold_when_never_nudged():
    assert xorics._should_nudge(AT, 0), "first nudge must fire when look-ups reach the threshold"


def test_does_not_re_nudge_immediately_after_a_nudge():
    # just nudged at AT; one more look-up should not fire again until EVERY more have passed
    assert not xorics._should_nudge(AT + 1, AT), "must not re-nudge on the very next look-up"
    assert not xorics._should_nudge(AT + EVERY - 1, AT), "must not re-nudge before EVERY further look-ups"


def test_re_nudges_after_every_further_lookups():
    assert xorics._should_nudge(AT + EVERY, AT), "must re-nudge after EVERY further look-ups (escalation)"


def test_resets_re_arm_the_first_nudge():
    # after a validated circuit the loop sets both counters to 0; the guard must re-arm
    assert xorics._should_nudge(AT, 0), "after a reset, reaching the threshold again must nudge"


def test_full_schedule_simulation_matches_the_loop():
    # mirror the loop's bookkeeping: +1 per research call, nudge per _should_nudge, record nudged_at
    streak = nudged_at = 0
    fires = []
    for _ in range(AT + EVERY + 2):              # enough look-ups to trip two nudges
        streak += 1
        if xorics._should_nudge(streak, nudged_at):
            fires.append(streak)
            nudged_at = streak
    assert fires[0] == AT, f"first nudge should be at {AT}, got {fires}"
    assert fires[1] == AT + EVERY, f"second nudge should be at {AT + EVERY}, got {fires}"
    # the coder validates a circuit -> the loop resets both counters; the guard re-arms
    streak = nudged_at = 0
    for _ in range(AT):
        streak += 1
    assert xorics._should_nudge(streak, nudged_at), "after a validated circuit, the guard must re-arm"


def test_lookup_tools_are_classified_as_research():
    for t in ("web_search", "search_datasheets", "fetch_datasheet",
              "find_part", "find_footprint", "part_pins", "read_file"):
        assert t in xorics._RESEARCH_TOOLS, f"{t} should count toward the research streak"


def test_validators_are_not_research_so_they_can_reset():
    # validators must NOT count as look-ups, otherwise engaging one would push the streak UP instead
    # of resetting it. (validate_circuit resets in its own branch; the others reset in the dispatch.)
    for t in ("validate_circuit", "check_circuit", "check_circuit_file", "compile_check"):
        assert t not in xorics._RESEARCH_TOOLS, f"{t} is a validator and must not count as research"


def test_nudge_text_names_the_write_action_and_takes_the_count():
    msg = xorics._CONVERGENCE_NUDGE.format(n=17)
    assert "17" in msg, "the nudge should report how many look-ups happened"
    assert "validate_circuit" in msg and "skidl" in msg.lower(), "the nudge must point at writing + validating"


def _run():
    tests = sorted((n, f) for n, f in globals().items()
                   if n.startswith("test_") and callable(f))
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed.append((name, str(e) or "assertion failed"))
        except Exception as e:
            failed.append((name, f"{type(e).__name__}: {e}"))
    if failed:
        for name, msg in failed:
            print(f"  FAIL {name}: {msg}")
        print(f"{passed} passed, {len(failed)} failed")
        sys.exit(1)
    print(f"{passed}/{len(tests)} passed")


if __name__ == "__main__":
    _run()
