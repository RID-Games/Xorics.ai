"""validate_circuit — the fence-handoff that gets a large SKiDL board onto disk and into the
grader WITHOUT the coder JSON-escaping it into a tool argument (the flagship serialization
blocker). Hermetic: stubs xorics's heavy siblings, and spies save_circuit / check_circuit_file
so we can assert exactly what the helper hands them. The real ERC build is the LIVE probe's job
(green mocks lie); this suite proves the WIRING. Runs anywhere:
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


# A flagship-class script: well over the ~1 KB JSON-arg wall, full of the exact characters that
# break tool-arg JSON escaping — embedded double/single quotes, backslashes, and many newlines.
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
TURN_WITH_FENCE = "Here's the ambient-light sensor board.\n\n```python\n" + SCRIPT + "```\n"
# extract_code strips the fenced block (harmless for SKiDL — only the ends, never indentation),
# so the bytes that reach save_circuit are the script with leading/trailing whitespace removed.
EXPECTED = SCRIPT.strip()


def test_pulls_fence_saves_exact_code_and_validates_by_that_path():
    _reset()
    result, saved = xorics._validate_circuit_from_turn(TURN_WITH_FENCE, "g2_ambient_sensor")
    assert _spy["saved_code"] == EXPECTED, "saved code must be the fenced script verbatim (no truncation)"
    assert _spy["saved_name"] == "g2_ambient_sensor", "the name arg must become the saved slug"
    assert saved and _spy["checked_path"] == saved, "validator must run on the path we just saved"
    assert getattr(result, "status", None) == "built", "built status must pass through"


def test_no_fence_returns_corrective_and_writes_nothing():
    _reset()
    result, saved = xorics._validate_circuit_from_turn(
        "I'll use an ESP32-C3 and an AP2112K. No script yet.", "g2_ambient_sensor")
    assert saved is None, "no fence -> nothing saved"
    assert _spy["saved_code"] is None, "save_circuit must NOT be called without a fence"
    assert "validate_circuit" in result and "```python" in result, "must tell the coder to emit a fence"


def test_failed_verdict_passes_through_so_the_coder_can_iterate():
    _reset()
    _verdict["status"] = "failed"
    result, saved = xorics._validate_circuit_from_turn(TURN_WITH_FENCE, "x")
    assert saved and getattr(result, "status", None) == "failed", "a failed build must not look built"


def test_longest_block_wins_so_a_small_noise_block_is_not_validated():
    _reset()
    turn = "first, a quick check:\n```bash\nls circuits/\n```\n\nnow the board:\n```python\n" + SCRIPT + "```\n"
    xorics._validate_circuit_from_turn(turn, "x")
    assert _spy["saved_code"] == EXPECTED, "must validate the python board, not the tiny bash block"


def test_empty_name_falls_back_to_a_default_slug():
    _reset()
    xorics._validate_circuit_from_turn(TURN_WITH_FENCE, "")
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
