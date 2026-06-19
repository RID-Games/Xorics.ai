#!/usr/bin/env python3
"""Tests for find_footprint — the footprint twin of find_part.

Builds a synthetic footprint library in a temp dir (KICAD_FOOTPRINT_DIR) with a unique
marker token ('zqxmark') in every name, so results are deterministic even on a box that
ALSO has the real KiCad footprint set installed — no real footprint can collide with the
marker. (Whether a NATURAL query like 'SOT-223' ranks the canonical footprint first is a
ranking-quality question, best confirmed on a live run against the real footprint set.)
"""
import os, tempfile, importlib.util

tmp = tempfile.mkdtemp()
LIB = os.path.join(tmp, "Zzz_TestKit.pretty")
os.makedirs(LIB)

def mod(name, pad_nums):
    pads = " ".join(f'(pad "{n}" smd roundrect (at 0 0) (size 1 1) (layers "F.Cu"))'
                    for n in pad_nums)
    with open(os.path.join(LIB, name + ".kicad_mod"), "w") as fh:
        fh.write(f'(footprint "{name}" (layer "F.Cu") {pads})\n')

mod("SOT223_zqxmark", ["1", "2", "3", "4"])              # 4 pads
mod("R0603_zqxmark",  ["1", "2"])                        # 2 pads
mod("R0402_zqxmark",  ["1", "2"])                        # 2 pads
mod("USBC_zqxmark",   [str(i) for i in range(1, 17)])    # 16 pads

os.environ["KICAD_FOOTPRINT_DIR"] = tmp

spec = importlib.util.spec_from_file_location("pcbt", "pcb_tools.py")
pcb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pcb)

_p = _f = 0
def check(label, cond):
    global _p, _f
    if cond:
        _p += 1; print(f"  ok   {label}")
    else:
        _f += 1; print(f"  FAIL {label}")


# ---- 1) enumeration ------------------------------------------------------
print("_all_footprints")
allfp = pcb._all_footprints()
check("enumerates the synthetic lib", ("Zzz_TestKit", "SOT223_zqxmark") in allfp)
check("enumerates exactly the 4 synthetic footprints",
      sum(1 for (l, _n) in allfp if l == "Zzz_TestKit") == 4)
print()

# ---- 2) basic lookup -----------------------------------------------------
print("find_footprint")
r = pcb.find_footprint("SOT223_zqxmark")
check("finds the SOT223 footprint as Lib:Name", "Zzz_TestKit:SOT223_zqxmark" in r)
check("  annotates its pad count (4 pads)", "4 pads" in r)
r = pcb.find_footprint("R0603_zqxmark")
check("finds the 0603 footprint", "Zzz_TestKit:R0603_zqxmark" in r and "2 pads" in r)
check("no search term -> asks for one", "needs a search term" in pcb.find_footprint(""))
check("no match -> helpful message, no crash",
      "No footprint matched" in pcb.find_footprint("qwxyz_nomatch_plugh"))
print()

# ---- 3) pad-count filter (pins=) -----------------------------------------
print("find_footprint(pins=N)")
r = pcb.find_footprint("zqxmark", pins=2)        # all 4 share the marker; only two have 2 pads
check("need 2 pads -> header says so", "need 2 pads" in r)
check("need 2 pads -> lists R0603", "Zzz_TestKit:R0603_zqxmark" in r)
check("need 2 pads -> lists R0402", "Zzz_TestKit:R0402_zqxmark" in r)
r4 = pcb.find_footprint("SOT223_zqxmark", pins=4)
check("need 4 pads -> SOT223 qualifies", "Zzz_TestKit:SOT223_zqxmark" in r4 and "need 4 pads" in r4)
print()

# ---- 4) every returned id actually resolves ------------------------------
print("returned ids resolve")
r = pcb.find_footprint("USBC_zqxmark")
check("USBC footprint present with 16 pads",
      "Zzz_TestKit:USBC_zqxmark" in r and "16 pads" in r)
check("  and _resolve_footprint agrees it's loadable",
      pcb._resolve_footprint("Zzz_TestKit:USBC_zqxmark")[0] == "ok")
print()

print(f"==== {_p} passed, {_f} failed ====")
raise SystemExit(1 if _f else 0)
