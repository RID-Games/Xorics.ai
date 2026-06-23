"""Phase 0 — honesty gate. Hermetic: stubs xorics's heavy siblings so the gate logic is tested
in isolation (no GPU/servers), like test_grader_decision tests _decide. Runs anywhere:
    python test_honesty_gate.py        (or: pytest test_honesty_gate.py)
"""
import os
import sys
import types
import tempfile

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

_stub("openai", OpenAI=_OpenAI)
_stub("datasheet_rag", search_datasheets=_noop)
_stub("web_datasheets", fetch_datasheet=_noop)
_stub("firmware_tools", compile_check=_noop, save_sketch=_noop)
_stub("notebook", Notebook=_Notebook)
_stub("pcb_tools", check_circuit=_noop, check_circuit_file=_noop, find_part=_noop,
      find_footprint=_noop, part_pins=_noop, save_circuit=_noop)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xorics  # noqa: E402


def _isolate():
    """Point the manifest at a clean temp dir and return a real on-disk file path to claim."""
    d = tempfile.mkdtemp(prefix="xorics_hg_")
    xorics._STATE_DIR = d                 # _deliverables_path() recomputes from this each call
    xorics._clear_deliverables()
    real = os.path.join(d, "circuit.py")
    open(real, "w").write("# a real saved deliverable\n")
    return d, real


# --- finalize_design --------------------------------------------------------------------
def test_finalize_empty_manifest_is_unverified():
    _isolate()
    r = xorics.finalize_design([])
    assert r.status == "unverified" and "no BUILT verdict" in r, r

def test_finalize_verified_when_built_and_file_exists():
    _, real = _isolate()
    xorics._record_deliverable(real, "check_circuit_file")
    r = xorics.finalize_design([real])
    assert r.status == "verified" and "VERIFIED" in r, r

def test_finalize_rejects_nonexistent_claimed_path():
    _, real = _isolate()
    xorics._record_deliverable(real, "check_circuit_file")            # a real build is on record
    r = xorics.finalize_design(["/tmp/does_not_exist_xyz.sch"])       # ...but this claim is fabricated
    assert r.status == "unverified" and "do not exist" in r, r

def test_finalize_rejects_real_but_unvalidated_file():
    d, real = _isolate()
    xorics._record_deliverable(real, "check_circuit_file")            # something built
    sneaky = os.path.join(d, "hand_written.ino")                     # exists but never validated
    open(sneaky, "w").write("void setup(){}")
    r = xorics.finalize_design([sneaky])
    assert r.status == "unverified" and "never passed a validator" in r, r

def test_finalize_no_paths_finalizes_against_manifest():
    _, real = _isolate()
    xorics._record_deliverable(real, "check_circuit")
    r = xorics.finalize_design()                                      # no explicit claims
    assert r.status == "verified", r


# --- machine footer ---------------------------------------------------------------------
def test_footer_silent_on_plain_chat():
    _isolate()
    out = xorics._append_manifest_footer("here is the ESP32 pinout", {"design_attempt": False}, 0)
    assert out == "here is the ESP32 pinout"

def test_footer_flags_unverified_when_design_ran_but_nothing_built():
    _isolate()
    before = len(xorics._load_deliverables())                        # 0
    out = xorics._append_manifest_footer("The design has been completed successfully.",
                                         {"design_attempt": True}, before)
    assert "\u26a0 UNVERIFIED" in out, out

def test_footer_confirms_verified_when_a_build_landed_this_turn():
    _, real = _isolate()
    before = len(xorics._load_deliverables())                        # snapshot BEFORE the build
    xorics._record_deliverable(real, "check_circuit_file")           # build lands during the turn
    out = xorics._append_manifest_footer("Done.", {"design_attempt": True}, before)
    assert "\u2713 VERIFIED" in out and "circuit.py" in out, out

def test_footer_ignores_prior_turn_deliverables():
    _, real = _isolate()
    xorics._record_deliverable(real, "check_circuit_file")           # verified on an EARLIER turn
    before = len(xorics._load_deliverables())                        # this turn starts after it
    out = xorics._append_manifest_footer("All set, complete!", {"design_attempt": True}, before)
    assert "\u26a0 UNVERIFIED" in out, out                           # nothing NEW built this turn


# --- manifest plumbing + wiring ---------------------------------------------------------
def test_clear_deliverables_empties_manifest():
    _, real = _isolate()
    xorics._record_deliverable(real, "check_circuit")
    assert len(xorics._load_deliverables()) == 1
    xorics._clear_deliverables()
    assert xorics._load_deliverables() == []

def test_finalize_design_is_registered_manager_tool():
    assert "finalize_design" in xorics.TOOL_IMPLS
    assert any(t["function"]["name"] == "finalize_design" for t in xorics.MANAGER_TOOLS)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
