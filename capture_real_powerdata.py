# Xorics — capture_real_powerdata.py
#
# Grounds the Layer-1 fixtures against REALITY. Builds a real, deliberately
# FLOATING Device:Crystal (both pins wired to nothing) plus one wired resistor,
# runs ERC + generate_netlist, then runs the PATCHED inspector epilogue (the one
# that emits [num, name] per part pin) and prints the raw topology line.
#
# Run on the box (no patch to pcb_tools.py required to run this):
#     ~/xorics-ai/skidl-venv/bin/python capture_real_powerdata.py
#
# WHAT TO READ in the output:
#   * If you see XORICS_POWER_ERR:  -> the inspector's circuit walk threw (== H1).
#       The strong grader is silently skipped whenever this happens. Paste the
#       error.
#   * Find the "Y1"/"XTAL" entry in "parts".  If it is ABSENT             -> H2
#       (SKiDL culled the unwired part before the inspector saw it).
#   * If present, look at its pin pairs:
#       [["1",""],["2",""]]   -> blank NAMES confirmed. num-fallback is justified,
#                                the fixtures are grounded, ship Layer-1.
#       [["1","1"],["2","2"]] -> names == nums; the #6 miss was NOT blank-name.
#                                Re-open: re-check it's FULLY floating (a half-
#                                wired crystal is only a warning by design).
#
# The point of this file is to refuse to trust the green test suite as a stand-in
# for one real SKiDL run. That conflation is what hid #6.

import os
import tempfile

# KiCad symbol libs must be findable BEFORE skidl is imported (it reads these at
# import time). Override with XORICS_KICAD_SYMBOL_DIR if your install differs.
_SYMDIR = os.environ.get("XORICS_KICAD_SYMBOL_DIR", "/usr/share/kicad/symbols")
for _v in ("KICAD6_SYMBOL_DIR", "KICAD7_SYMBOL_DIR",
           "KICAD8_SYMBOL_DIR", "KICAD9_SYMBOL_DIR"):
    os.environ.setdefault(_v, _SYMDIR)

os.chdir(tempfile.mkdtemp(prefix="xorics_capture_"))

from skidl import Part, Net, ERC, generate_netlist   # noqa: E402

# --- the board under test: a floating crystal + a trivially-wired resistor ---
xtal = Part("Device", "Crystal")     # BOTH pins left unconnected on purpose
r1 = Part("Device", "R")
vcc, gnd = Net("VCC"), Net("GND")
r1[1] += vcc
r1[2] += gnd

try:
    ERC()
except Exception as _e:
    print("ERC raised (continuing):", repr(_e))
generate_netlist()

# ===== PATCHED inspector epilogue (emits [num, name] per part pin) =====
# Kept byte-for-byte in sync with the _POWER_INSPECTOR in pcb_tools.py AFTER the
# apply-script's one-line patch, so this capture reflects exactly what the grader
# will see.
try:
    import json as _xj
    import skidl as _xsk
    _xc = None
    try:
        from skidl import default_circuit as _xc
    except Exception:
        _xc = (getattr(_xsk, "default_circuit", None)
               or getattr(getattr(_xsk, "circuit", None), "default_circuit", None))
    if _xc is None:
        _xc = _xsk.Net().circuit
    _xnets = []
    for _n in _xc.nets:
        _nm = str(getattr(_n, "name", "") or "")
        _nd = []
        for _pp in _n.pins:
            _nd.append([str(getattr(getattr(_pp, "part", None), "ref", "?")),
                        str(getattr(_pp, "num", "")), str(getattr(_pp, "name", ""))])
        _xnets.append([_nm, _nd])
    _xparts = []
    for _p in _xc.parts:
        _xparts.append([str(getattr(_p, "ref", "?")), str(getattr(_p, "name", "")),
                        str(getattr(_p, "lib", "")),
                        [[str(getattr(_pp, "num", "")), str(getattr(_pp, "name", ""))]
                         for _pp in _p.pins]])
    _payload = {"nets": _xnets, "parts": _xparts}
    print("XORICS_POWER_JSON:" + _xj.dumps(_payload))
    print()
    print("===== readable =====")
    for _ref, _name, _lib, _pins in _xparts:
        print(f"  {_ref}  {_lib}:{_name}  pins(num,name)={_pins}")
except Exception as _xe:
    print("XORICS_POWER_ERR:" + repr(_xe))
