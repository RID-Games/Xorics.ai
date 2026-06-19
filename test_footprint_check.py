# Xorics — footprint pad/pin check tests. Plain-assert, no pytest.
# Run: python3 test_footprint_check.py   (needs pcb_tools.py + netlist_query.py)

import importlib.util
import os
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("pcbt", "pcb_tools.py")
pcb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pcb)
import netlist_query as nq

_p = _f = 0
def check(label, cond):
    global _p, _f
    if cond:
        _p += 1; print(f"  ok   {label}")
    else:
        _f += 1; print(f"  FAIL {label}")


# ---- 1) real .kicad_mod parsing + file resolution -------------------------
print("_footprint_pad_numbers (real file)")
tmp = tempfile.mkdtemp(prefix="xfp_")
pretty = Path(tmp) / "Package_SO.pretty"
pretty.mkdir()
soic8 = "\n".join(['(footprint "SOIC-8" (layer "F.Cu")'] +
                   [f'  (pad "{i}" smd roundrect (at 0 {i}) (size 1 1) (layers "F.Cu"))'
                    for i in range(1, 9)] +
                   ['  (pad "" np_thru_hole circle (at 0 0) (size 1 1) (layers "*.Cu"))', ')'])
(pretty / "SOIC-8_5.2x6.2mm_Pitch1.27mm.kicad_mod").write_text(soic8)
os.environ["KICAD_FOOTPRINT_DIR"] = tmp

pads = pcb._footprint_pad_numbers("Package_SO:SOIC-8_5.2x6.2mm_Pitch1.27mm")
check("parses 8 numbered pads", pads == {str(i) for i in range(1, 9)})
check("ignores the unnumbered mechanical pad", "" not in (pads or set()))
check("missing footprint file -> None", pcb._footprint_pad_numbers("Nope:DoesNotExist") is None)
check("no colon -> None", pcb._footprint_pad_numbers("bare") is None)
check("empty -> None", pcb._footprint_pad_numbers("") is None)


# ---- 2) fault / warning matrix (stub the file read for logic) -------------
print("_footprint_mismatches (logic)")
_real = pcb._footprint_pad_numbers
def stub(fp):
    if fp == "":            return None
    if "SOIC-8" in fp:      return {str(i) for i in range(1, 9)}   # 8 pads
    if "SHORT2" in fp:      return {"1", "2"}                       # too few
    if "UNREADABLE" in fp:  return None
    return {"1", "2"}                                              # default 2-pad passive
pcb._footprint_pad_numbers = stub
try:
    def board(parts):
        return {"nets": [], "parts": parts}

    # AMS1117 (3 pins) on an 8-pad SOIC-8 -> WARNING (extra pads), no fault
    ams = board([["U1", "AMS1117-3.3", "Regulator_Linear",
                  [["1", "GND"], ["2", "VO"], ["3", "VI"]], "Package_SO:SOIC-8"]])
    fa, wa = pcb._footprint_mismatches(ams)
    check("AMS1117->SOIC-8: no fault", fa == [])
    check("AMS1117->SOIC-8: warning raised", len(wa) == 1 and "U1" in wa[0])
    check("  warning lists the 5 extra pads", "4" in wa[0] and "8" in wa[0])

    # 3-pin part on a 2-pad footprint -> FAULT (pin 3 has no pad)
    short = board([["U2", "CHIP", "Lib", [["1", ""], ["2", ""], ["3", ""]], "Lib:SHORT2"]])
    fs, ws = pcb._footprint_mismatches(short)
    check("too-few-pads: fault raised", len(fs) == 1 and "U2" in fs[0])
    check("  fault names the missing pin 3", "[3]" in fs[0] or "'3'" in fs[0] or " 3" in fs[0])
    check("too-few-pads: no warning", ws == [])

    # exact match -> clean
    ok = board([["R1", "R", "Device", [["1", ""], ["2", ""]], "Resistor_SMD:R_0603"]])
    check("matching pads -> clean", pcb._footprint_mismatches(ok) == ([], []))

    # unverifiable footprint -> skipped entirely
    unk = board([["U3", "X", "Lib", [["1", ""], ["2", ""], ["3", ""]], "Lib:UNREADABLE"]])
    check("unverifiable footprint -> skipped", pcb._footprint_mismatches(unk) == ([], []))

    # no footprint -> skipped (that's the netlist-error gate's concern)
    nofp = board([["U4", "X", "Lib", [["1", ""], ["2", ""]], ""]])
    check("no footprint -> skipped here", pcb._footprint_mismatches(nofp) == ([], []))

    # ---- 3) _decide integration -------------------------------------------
    print("_decide integration")
    # AMS1117 wired correctly (VI/VO on separate rails) but on the wrong (8-pad) fp
    # -> BUILT, with a footprint WARNING (extra pads aren't fatal).
    AMS_BUILT = {
        "nets": [["VBUS", [["U1", "3", "VI"], ["C1", "1", ""]]],
                 ["V3", [["U1", "2", "VO"], ["C2", "1", ""]]],
                 ["GND", [["U1", "1", "GND"], ["C1", "2", ""], ["C2", "2", ""]]]],
        "parts": [["U1", "AMS1117-3.3", "Regulator_Linear",
                   [["1", "GND"], ["2", "VO"], ["3", "VI"]], "Package_SO:SOIC-8"],
                  ["C1", "C", "Device", [["1", ""], ["2", ""]], "Cap:0603"],
                  ["C2", "C", "Device", [["1", ""], ["2", ""]], "Cap:0603"]],
    }
    r = pcb._decide(0, True, AMS_BUILT, "", "", "")
    check("wrong-but-superset footprint -> BUILT", r.status == "built")
    check("  footprint warning surfaced on BUILT", "Footprint may be wrong" in r and "U1" in r)

    # A part whose footprint is missing a pad -> FAILED on a footprint fault.
    FP_FAIL = {
        "nets": [["P1", [["U2", "1", ""], ["C1", "1", ""]]],
                 ["P2", [["U2", "2", ""], ["C1", "2", ""]]]],
        "parts": [["U2", "CHIP", "Lib", [["1", ""], ["2", ""], ["3", ""]], "Lib:SHORT2"],
                  ["C1", "C", "Device", [["1", ""], ["2", ""]], "Resistor_SMD:R_0603"]],
    }
    r = pcb._decide(0, True, FP_FAIL, "", "", "")
    check("pin-with-no-pad -> FAILED", r.status == "failed")
    check("  flagged as a footprint fault", "Footprint faults" in r and "no pad" in r)
finally:
    pcb._footprint_pad_numbers = _real


# ---- 4) nq.footprints accessor + backward compatibility -------------------
print("netlist_query.footprints")
d5 = {"parts": [["U1", "X", "Lib", [["1", ""]], "Lib:FP1"]]}
check("reads the 5th field", nq.footprints(d5) == {"U1": "Lib:FP1"})
d4 = {"parts": [["U1", "X", "Lib", [["1", ""]]]]}     # OLD 4-field record
check("old 4-field record -> empty footprint", nq.footprints(d4) == {"U1": ""})
check("parts() still works on 5-field record",
      [r[0] for r in nq.parts(d5)] == ["U1"])

print()
print(f"==== {_p} passed, {_f} failed ====")
raise SystemExit(1 if _f else 0)
