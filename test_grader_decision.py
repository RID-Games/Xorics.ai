# Xorics — grader decision tests. Plain-assert, no pytest.
# Run: python3 test_grader_decision.py   (needs pcb_tools.py + netlist_query.py present)
#
# Exercises the REAL grading decision (_decide) on real-shaped power_data, so the
# BUILT/FAILED verdict is proven — not a mock of a checker. Covers both false-BUILT
# classes we found (floating crystal via clean lib; footprint errors) plus the
# blank-name floating part that only Layer-1 catches.

import importlib.util

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

def decide(power_data, raw="", rc=0, net=True, erc="", log=""):
    return pcb._decide(rc, net, power_data, raw, erc, log)

# ---- fixtures (parts pins = [num,name], the patched inspector shape) ----
CLEAN = {
    "nets": [["VCC", [["R1", "1", ""], ["U1", "7", "VCC"]]],
             ["GND", [["R1", "2", ""], ["U1", "8", "GND"]]]],
    "parts": [["R1", "R", "Device", [["1", ""], ["2", ""]]],
              ["U1", "ATmega328P-P", "MCU_Microchip_ATmega", [["7", "VCC"], ["8", "GND"]]]],
}
# CLEAN + a never-wired crystal with NAMED pins and a CLEAN lib (the #6 board,
# once the lib dump is fixed): must FAIL on the floating axis.
FLOATING_XTAL = {
    "nets": CLEAN["nets"],
    "parts": CLEAN["parts"] + [["Y1", "Crystal", "Device", [["1", "1"], ["2", "2"]]]],
}
# CLEAN + a never-wired resistor with BLANK pin names — the class the old name-only
# checker dropped; Layer-1 must catch it.
FLOATING_BLANK_R = {
    "nets": CLEAN["nets"],
    "parts": CLEAN["parts"] + [["R9", "R", "Device", [["1", ""], ["2", ""]]]],
}
# Regulator with input and output shorted onto one net.
REG_SHORT = {
    "nets": [["VBUS", [["U1", "3", "VI"], ["U1", "2", "VO"], ["C1", "1", ""]]],
             ["GND", [["U1", "1", "GND"], ["C1", "2", ""]]]],
    "parts": [["U1", "AMS1117-3.3", "Regulator_Linear", [["3", "VI"], ["2", "VO"], ["1", "GND"]]],
              ["C1", "C", "Device", [["1", ""], ["2", ""]]]],
}
# CLEAN + one floating pin on an otherwise-wired part -> warning, not fault.
PARTIAL = {
    "nets": CLEAN["nets"],
    "parts": [["R1", "R", "Device", [["1", ""], ["2", ""]]],
              ["U1", "ATmega328P-P", "MCU", [["7", "VCC"], ["8", "GND"], ["20", "AREF"]]]],
}

ATMEGA_NETLIST = """WARNING: Missing tag on ATmega328P-P instantiated at /x/c.py:8.
WARNING: Random tag djLKKf8sXj generated for ATmega328P-P.
ERROR: No footprint for Conn_02x14_Odd_Even/J1 added at /x/c.py:41.
ERROR: No footprint for ATmega328P-P/U1 added at /x/c.py:8.
ERROR: No footprint for Crystal/Y1 added at /x/c.py:17.
INFO: 21 warnings found while generating netlist.
INFO: 7 errors found while generating netlist."""

WARNINGS_ONLY = """WARNING: Missing tag on C instantiated at /x/c.py:18.
WARNING: Random tag abc generated for C.
INFO: 21 warnings found while generating netlist.
INFO: 0 errors found while generating netlist."""

# ===========================================================================
print("_netlist_errors")
c, lines = pcb._netlist_errors(ATMEGA_NETLIST)
check("counts the 7 footprint errors", c == 7)
check("collects ERROR lines, not warnings", lines and all(l.startswith("ERROR:") for l in lines))
check("ignores the tag warnings", not any("Missing tag" in l for l in lines))
c0, l0 = pcb._netlist_errors(WARNINGS_ONLY)
check("warnings-only -> 0 errors", c0 == 0 and l0 == [])
check("empty -> 0 errors", pcb._netlist_errors("") == (0, []))

print("_decide — FAIL_ON_NETLIST_ERRORS = True (default)")
check("FAIL_ON_NETLIST_ERRORS defaults True", pcb.FAIL_ON_NETLIST_ERRORS is True)

r = decide(CLEAN)
check("clean board -> BUILT", r.status == "built")

r = decide(FLOATING_XTAL)
check("floating crystal (clean lib) -> FAILED", r.status == "failed")
check("  message names Y1", "Y1" in r)
check("  flagged as electrical, not netlist", "Electrical faults" in r)

r = decide(FLOATING_BLANK_R)
check("blank-name floating resistor -> FAILED (Layer-1 catch)", r.status == "failed")
check("  message names R9", "R9" in r)

r = decide(REG_SHORT)
check("regulator in==out short -> FAILED", r.status == "failed")
check("  message names the regulator U1", "U1" in r and "shorted" in r.lower())

r = decide(CLEAN, raw=ATMEGA_NETLIST)
check("clean electrical + 7 footprint errors -> FAILED", r.status == "failed")
check("  flagged as netlist errors", "Netlist errors" in r)
check("  mentions footprint", "footprint" in r.lower())

r = decide(PARTIAL)
check("one floating pin -> BUILT (warning, not fault)", r.status == "built")
check("  AREF surfaced as warning", "AREF" in r and "Unconnected pins" in r)

r = decide(CLEAN, rc=1)
check("nonzero exit -> FAILED", r.status == "failed")
r = decide(CLEAN, net=False)
check("no netlist file -> FAILED", r.status == "failed")

print("_decide — FAIL_ON_NETLIST_ERRORS = False (toggle)")
pcb.FAIL_ON_NETLIST_ERRORS = False
try:
    r = decide(CLEAN, raw=ATMEGA_NETLIST)
    check("footprint errors -> BUILT when toggled off", r.status == "built")
    check("  but surfaced as a warning", "Netlist reported 7 error" in r)
    # electrical faults still fail regardless of the toggle
    r = decide(FLOATING_XTAL, raw=ATMEGA_NETLIST)
    check("electrical fault still FAILS with toggle off", r.status == "failed")
finally:
    pcb.FAIL_ON_NETLIST_ERRORS = True

print()
print(f"==== {_p} passed, {_f} failed ====")
raise SystemExit(1 if _f else 0)
