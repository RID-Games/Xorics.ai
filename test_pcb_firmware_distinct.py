"""PCB / firmware distinction — closes the off-ramp where a firmware compile faked a VERIFIED board.
A board/schematic/BOM task can only be satisfied by a CIRCUIT validation; a `compile_check` (.ino)
BUILT must not finalize it (and a real failure stays honestly UNVERIFIED). This suite tests the task
CLASSIFIER (the part that would be fragile if wrong), the soft-research set the guard drops, and the
self-prompt directives. The end-to-end gate behaviour is the LIVE probe's job. Runs anywhere:
    python test_pcb_firmware_distinct.py        (or: pytest test_pcb_firmware_distinct.py)
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

SYS = {"role": "system", "content": "you are the coder"}
def task(text): return [SYS, {"role": "user", "content": text}]


def test_pcb_tasks_want_a_circuit():
    for t in ("Create a KiCad schematic and BOM for a sensor beacon",
              "design a PCB for an ambient-light module",
              "build the circuit and generate a netlist",
              "lay out the SKiDL board with the right footprint",
              "make a schematic for the flashlight sensor board"):
        assert xorics._task_wants_circuit(task(t)), f"should require a circuit: {t!r}"


def test_firmware_tasks_do_not_want_a_circuit():
    # the actual CHECK 3 prompt and friends — pure firmware, no circuit keywords
    for t in ("Write an Arduino sketch for an ESP32-C3 that blinks the onboard LED on GPIO8, then "
              "validate it with compile_check and give me the final sketch.",
              "write firmware that reads a sensor and prints over serial",
              "blink an LED every 500ms on the dev board"):
        assert not xorics._task_wants_circuit(task(t)), f"firmware-only, must NOT require a circuit: {t!r}"


def test_ambiguous_board_word_alone_does_not_trip():
    # 'board' is too ambiguous (dev board / onboard / keyboard), so it is intentionally NOT a trigger —
    # a real PCB task always carries a strong signal (schematic/pcb/kicad/...). This is what keeps
    # firmware tasks that merely mention a board from being wrongly rejected.
    assert not xorics._task_wants_circuit(task("blink the onboard LED"))
    assert not xorics._task_wants_circuit(task("flash the dev board"))
    assert not xorics._task_wants_circuit(task("read the keyboard"))
    assert xorics._task_wants_circuit(task("design the sensor board PCB")), "a strong keyword still triggers"


def test_empty_or_missing_task_does_not_crash_and_wants_no_circuit():
    assert not xorics._task_wants_circuit([SYS])              # no task message
    assert not xorics._task_wants_circuit([SYS, {"role": "user", "content": ""}])
    assert not xorics._task_wants_circuit([SYS, {"role": "user"}])   # content missing entirely


def test_soft_research_is_a_subset_of_research():
    # the guard's teeth drop SOFT research; those must all be real research tools, and the productive
    # look-ups (find_part/find_footprint/part_pins) must NOT be dropped.
    assert xorics._SOFT_RESEARCH_TOOLS <= xorics._RESEARCH_TOOLS, "soft research must be a subset of research"
    for keep in ("find_part", "find_footprint", "part_pins"):
        assert keep not in xorics._SOFT_RESEARCH_TOOLS, f"{keep} is productive and must not be dropped"
    for drop in ("web_search", "search_datasheets", "fetch_datasheet"):
        assert drop in xorics._SOFT_RESEARCH_TOOLS, f"{drop} is open-ended web research and should be dropped"


def test_firmware_reject_directive_points_back_to_the_circuit():
    d = xorics._FIRMWARE_NOT_A_BOARD
    assert "validate_circuit" in d and "skidl" in d.lower(), "must redirect to writing + validating a circuit"
    assert "firmware" in d.lower(), "must name what it's rejecting"


def test_failed_fix_directive_keeps_it_in_skidl():
    d = xorics._FAILED_FIX_DIRECTIVE
    assert "part_pins" in d, "should point at part_pins for a rejected pin name"
    assert "validate_circuit" in d, "should tell it to re-validate"
    assert "firmware" in d.lower(), "should warn against switching to firmware"


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
