"""validate_circuit — the fence-handoff that gets a large SKiDL board onto disk and into the
validator WITHOUT the coder JSON-escaping it into a tool argument (the flagship serialization
blocker). The coder does not reliably co-emit the script with the tool call, so validate_circuit
sources the script from the coder's most recent SKiDL fence (this turn first, else a recent
earlier turn) and dedups by sha1 so identical bytes are never re-validated. Hermetic: stubs
xorics's heavy siblings, spies save_circuit / check_circuit_file. The real ERC build is the LIVE
probe's job (green mocks lie); this suite proves the WIRING. Runs anywhere:
    python test_validate_circuit.py        (or: pytest test_validate_circuit.py)
"""
import os
import sys
import types

# --- stub the import-time-heavy siblings so `import xorics` is dependency-light ----------
def _stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m

_noop = lambda *a, **k: ""
class _OpenAI:                       # construction must not connect (it doesn't)
    def __init__(self, *a, **k): pass
class _Notebook:
    def __init__(self, *a, **k): pass

# A CheckResult stand-in: reads as its text for the coder but carries a .status, like the real one.
class _FakeCheck(str):
    def __new__(cls, s, status):
        o = str.__new__(cls, s)
        o.status = status
        return o

# Spies: record exactly what the helper passed in, and let a test choose the build verdict.
_spy = {"saved_code": None, "saved_name": None, "checked_path": None}
_verdict = {"status": "built"}

def _spy_save_circuit(code, name="circuit"):
    _spy["saved_code"] = code
    _spy["saved_name"] = name
    return f"/tmp/circuits/{name}/{name}.py"        # mimic save_circuit's return (a path)

def _spy_check_circuit_file(path):
    _spy["checked_path"] = path
    return _FakeCheck(f"(validated {path})", _verdict["status"])

def _reset():
    _spy.update(saved_code=None, saved_name=None, checked_path=None)
    _verdict["status"] = "built"

_stub("openai", OpenAI=_OpenAI)
_stub("datasheet_rag", search_datasheets=_noop)
_stub("web_datasheets", fetch_datasheet=_noop)
_stub("firmware_tools", compile_check=_noop, save_sketch=_noop)
_stub("notebook", Notebook=_Notebook)
_stub("pcb_tools", check_circuit=_noop, check_circuit_file=_spy_check_circuit_file,
      find_part=_noop, find_footprint=_noop, part_pins=_noop, save_circuit=_spy_save_circuit)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xorics  # noqa: E402


# A flagship-class script: over the ~1 KB JSON-arg wall, with the exact characters that break
# tool-arg JSON escaping — embedded double/single quotes, backslashes, many newlines — and the
# SKiDL signals (`from skidl`, `generate_netlist`) the fence-scanner keys on.
SCRIPT = (
    "from skidl import *\n"
    "# windows-y path C:\\\\kicad and quoted pins \"VI\"/'VO' are what wreck JSON escaping\n"
    "reg = Part('Regulator_Linear','AP2112K-3.3', footprint='Package_TO_SOT_SMD:SOT-23-5')\n"
    "vbus, v3, gnd = Net('VBUS'), Net('3V3'), Net('GND')\n"
    + "\n".join(f"c{i} = Part('Device','C', value='0.1uF'); v3 += c{i}[1]; gnd += c{i}[2]"
                for i in range(60))
    + "\nERC()\ngenerate_netlist()\n"
)
assert len(SCRIPT) > 1500, "fixture must exceed the ~1KB wall to be a meaningful test"
EXPECTED = SCRIPT.strip()          # extract path strips the fenced block (ends only, never indentation)
TURN_WITH_FENCE = "Here's the ambient-light sensor board.\n\n```python\n" + SCRIPT + "```\n"

SYS = {"role": "system", "content": "you are the coder"}
USER = {"role": "user", "content": "Design a discreet flashlight-style ambient-light sensor board."}
def asst(content): return {"role": "assistant", "content": content}
def tool(content): return {"role": "tool", "content": content}


def test_coemit_current_turn_validates_that_script():
    _reset()
    seen = set()
    msgs = [SYS, USER, asst(TURN_WITH_FENCE)]            # script co-emitted with the validate call
    result, saved = xorics._validate_circuit_from_turn(msgs, "g2_ambient_sensor", seen)
    assert _spy["saved_code"] == EXPECTED, "saved code must be the fenced script verbatim (no truncation)"
    assert _spy["saved_name"] == "g2_ambient_sensor"
    assert saved and _spy["checked_path"] == saved, "validator must run on the path we just saved"
    assert getattr(result, "status", None) == "built"


def test_falls_back_to_recent_prior_fence_when_current_turn_is_empty():
    # the coder's actual rhythm: write the script one turn, then call validate_circuit on the next
    # turn with EMPTY content. The script must still be found (in the recent earlier assistant turn).
    _reset()
    seen = set()
    msgs = [SYS, USER, asst(TURN_WITH_FENCE), tool("Pins for ATtiny..."), asst("")]
    result, saved = xorics._validate_circuit_from_turn(msgs, "x", seen)
    assert _spy["saved_code"] == EXPECTED, "must reach back to the coder's most recent script"
    assert saved and getattr(result, "status", None) == "built"


def test_dedup_refuses_an_identical_script_the_second_time():
    _reset()
    seen = set()
    msgs = [SYS, USER, asst(TURN_WITH_FENCE)]
    _, s1 = xorics._validate_circuit_from_turn(msgs, "x", seen)
    assert s1 and _spy["saved_code"] == EXPECTED, "first time: the script is validated"
    _reset()                                            # clear the spy to detect a second save
    r2, s2 = xorics._validate_circuit_from_turn(msgs, "x", seen)
    assert s2 is None, "identical bytes must not be re-validated"
    assert _spy["saved_code"] is None, "save_circuit must NOT run on a repeat"
    assert "SAME script" in r2 or "already validated" in r2, "corrective must say it's a repeat"


def test_a_changed_script_after_a_repeat_is_validated():
    # dedup must block ONLY identical bytes — a real edit has to get through.
    _reset()
    seen = set()
    xorics._validate_circuit_from_turn([SYS, USER, asst(TURN_WITH_FENCE)], "x", seen)   # seed sha1(A)
    _reset()
    changed = TURN_WITH_FENCE.replace("AP2112K-3.3", "AMS1117-3.3")                     # different bytes
    _, saved = xorics._validate_circuit_from_turn([SYS, USER, asst(changed)], "x", seen)
    assert saved and _spy["saved_code"] is not None, "a changed script must validate, not dedup"
    assert "AMS1117-3.3" in _spy["saved_code"]


def test_no_skidl_script_anywhere_returns_corrective_and_writes_nothing():
    _reset()
    seen = set()
    msgs = [SYS, USER, asst("I'll use an ESP32-C3 and an AP2112K. No script yet.")]
    result, saved = xorics._validate_circuit_from_turn(msgs, "x", seen)
    assert saved is None and _spy["saved_code"] is None, "nothing to validate -> save nothing"
    assert "SKiDL script" in result


def test_a_non_skidl_block_is_not_mistaken_for_the_board():
    _reset()
    seen = set()
    msgs = [SYS, USER, asst("first:\n```bash\nls circuits/\n```\nthat's all for now")]
    result, saved = xorics._validate_circuit_from_turn(msgs, "x", seen)
    assert saved is None and _spy["saved_code"] is None, "a bash block is not a SKiDL board"


def test_skidl_block_is_found_even_beside_a_longer_noise_block():
    # a message can hold a longer non-SKiDL block (e.g. a pasted log) next to the real script;
    # the script must still be picked, not skipped because it isn't the longest block.
    _reset()
    seen = set()
    noise = "```\n" + ("LOG LINE bla bla\n" * 400) + "```\n"        # longer than the script, not SKiDL
    msgs = [SYS, USER, asst(noise + "and the board:\n```python\n" + SCRIPT + "```\n")]
    xorics._validate_circuit_from_turn(msgs, "x", seen)
    assert _spy["saved_code"] == EXPECTED, "the SKiDL block must win over a longer non-SKiDL block"


def test_most_recent_skidl_fence_wins_over_an_older_one():
    _reset()
    seen = set()
    newer_script = SCRIPT.replace("range(60)", "range(40)")          # a different, newer script
    newer = "revised board:\n```python\n" + newer_script + "```\n"
    msgs = [SYS, USER, asst(TURN_WITH_FENCE), tool("..."), asst(newer)]
    xorics._validate_circuit_from_turn(msgs, "x", seen)
    assert _spy["saved_code"] == newer_script.strip(), "must validate the NEWEST script"


def test_a_fence_in_tool_output_is_never_pulled():
    # only the coder's own (assistant) messages count — a script echoed in a TOOL result is not
    # the coder's design and must not be validated.
    _reset()
    seen = set()
    msgs = [SYS, USER, tool("here is a script:\n```python\n" + SCRIPT + "```\n"), asst("validating now")]
    result, saved = xorics._validate_circuit_from_turn(msgs, "x", seen)
    assert saved is None and _spy["saved_code"] is None, "a fence in tool output is not the coder's board"


def test_failed_verdict_passes_through_so_the_coder_can_iterate():
    _reset()
    seen = set()
    _verdict["status"] = "failed"
    result, saved = xorics._validate_circuit_from_turn([SYS, USER, asst(TURN_WITH_FENCE)], "x", seen)
    assert saved and getattr(result, "status", None) == "failed", "a failed build must not look built"


def test_empty_name_falls_back_to_a_default_slug():
    _reset()
    seen = set()
    xorics._validate_circuit_from_turn([SYS, USER, asst(TURN_WITH_FENCE)], "", seen)
    assert _spy["saved_name"] == "circuit", "an empty name must not crash; it defaults to 'circuit'"


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
