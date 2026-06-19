#!/usr/bin/env python3
"""Tests for the 'No pins found' rescue (_no_pin_faults / _lib_for_part / _no_pin_help).

The RAW string below is the real failure captured from a live run: an ESP32-C3 indexed
with the datasheet name 'GPIO0' when the KiCad symbol pin is 'IO0'. The subprocess pin
loader (_real_pins) is stubbed so the hint assembly is testable without skidl/KiCad.
"""
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


# Captured verbatim from the live run (the relevant lines).
RAW = (
    "WARNING: fp-lib-table file was not found. Component footprints are not available.\n"
    "ERROR: No pins found using ESP32-C3:U1[('GPIO0',)] @ [/home/zawayix/xorics-ai/<string>:1]\n"
    "Traceback (most recent call last):\n"
    "TypeError: unsupported operand type(s) for +=: 'NoneType' and 'Net'\n"
)
CODE = ("from skidl import *\n"
        "u = Part('MCU_Espressif','ESP32-C3')\n"
        "u['GPIO0'] += Net('X')\n"
        "ERC()\n")


# ---- 1) parse the ERROR line ---------------------------------------------
print("_no_pin_faults")
faults = pcb._no_pin_faults(RAW)
check("parses exactly one fault", len(faults) == 1)
name, ref, bad = faults[0]
check("  part name", name == "ESP32-C3")
check("  ref", ref == "U1")
check("  bad pin", bad == ["GPIO0"])
check("multiple bad pins in one index",
      pcb._no_pin_faults("ERROR: No pins found using X:U2[('A', 'B')]")[0][2] == ["A", "B"])
check("clean output -> no faults", pcb._no_pin_faults("CIRCUIT BUILT — all good") == [])
check("empty -> no faults", pcb._no_pin_faults("") == [])
print()

# ---- 2) recover the library from the script ------------------------------
print("_lib_for_part")
check("recovers library from the Part() call", pcb._lib_for_part(CODE, "ESP32-C3") == "MCU_Espressif")
check("part not in code -> None", pcb._lib_for_part(CODE, "AMS1117-3.3") is None)
check("handles double quotes / spacing",
      pcb._lib_for_part('x = Part( "Regulator_Linear" , "AMS1117-3.3" )', "AMS1117-3.3") == "Regulator_Linear")
print()

# ---- 3) assemble the hint (real-pin loader stubbed) ----------------------
print("_no_pin_help")
pcb._real_pins = lambda lib, nm: ["IO0", "IO1", "IO2", "TXD0", "RXD0", "VDD3P3", "GND"]
h = pcb._no_pin_help(CODE, RAW)
check("names the bad pin", "GPIO0" in h)
check("lists the real pin names", "IO0" in h and "TXD0" in h)
check("identifies the ref and part", "U1" in h and "ESP32-C3" in h)
check("tells the coder to reconnect by exact name", "EXACT" in h)
check("tells the coder NOT to re-search the part", "re-search" in h)

# fall back to a part_pins suggestion when the symbol can't be loaded
pcb._real_pins = lambda lib, nm: None
h2 = pcb._no_pin_help(CODE, RAW)
check("fallback points at part_pins with the right lib/name",
      "part_pins('MCU_Espressif','ESP32-C3')" in h2)

# fall back further when the library can't be recovered from the code
h3 = pcb._no_pin_help("(* no Part call here *)", RAW)
check("no library in code -> suggests find_part", "find_part('ESP32-C3')" in h3)

# nothing to do when there's no pin error
check("no pin error -> empty hint", pcb._no_pin_help(CODE, "ERC passed, netlist generated") == "")
print()

print(f"==== {_p} passed, {_f} failed ====")
raise SystemExit(1 if _f else 0)
