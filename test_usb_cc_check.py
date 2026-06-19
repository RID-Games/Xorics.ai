#!/usr/bin/env python3
"""Tests for the USB-C floating-CC check (_usb_cc_faults).

Plain asserts, no test framework — same style as test_footprint_check.py. A USB-C sink
with floating CC1/CC2 won't enumerate or pull power (no Rd to GND), but the generic
floating-pin check only WARNS on individual open pins. This check special-cases CC.
Default behavior is a warning; FAIL_ON_FLOATING_USB_CC promotes it to a hard fault.

The 'floating' notion comes from nq.floating_pins (the grader's shared connectivity
join), so leaving a CC pin off every net here is faithful to a real KiCad NC pin.
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


# Fake footprint lib that resolves NOWHERE, so the footprint checks stay neutral on
# both the sandbox and RIDGames and can't perturb these CC tests.
FP = "Zzz_FakeLib:fp"

def board(cc1=False, cc2=False):
    """USB-C receptacle J1 (VBUS/GND/CC1/CC2). VBUS and GND are wired so J1 isn't
    fully-floating; cc1/cc2=True wires that CC pin to a resistor (-> connected),
    otherwise the CC pin is left off every net (-> floating)."""
    j1_pins = [["A4", "VBUS"], ["A1", "GND"], ["A5", "CC1"], ["B5", "CC2"]]
    nets  = [["VBUS", [["J1", "A4", "VBUS"], ["R0", "1", ""]]],
             ["GND",  [["J1", "A1", "GND"],  ["R0", "2", ""]]]]
    parts = [["J1", "USB_C_Receptacle_USB2.0_14P", "Connector", j1_pins, FP],
             ["R0", "R", "Device", [["1", ""], ["2", ""]], FP]]
    if cc1:
        nets.append(["CC1_RD", [["J1", "A5", "CC1"], ["R1", "1", ""]]])
        parts.append(["R1", "R", "Device", [["1", ""], ["2", ""]], FP])
    if cc2:
        nets.append(["CC2_RD", [["J1", "B5", "CC2"], ["R2", "1", ""]]])
        parts.append(["R2", "R", "Device", [["1", ""], ["2", ""]], FP])
    return {"nets": nets, "parts": parts}


# ---- 1) detection --------------------------------------------------------
print("_is_usb_c_part")
check("named USB_C_Receptacle -> detected",
      pcb._is_usb_c_part("USB_C_Receptacle_USB2.0_14P", "Connector", []))
check("CC pins, generic name -> detected (renamed/other USB-C connector)",
      pcb._is_usb_c_part("J", "Connector", [["A5", "CC1"], ["B5", "CC2"]]))
check("plain header, no CC pins -> not USB-C",
      not pcb._is_usb_c_part("Conn_01x04", "Connector", [["1", ""], ["2", ""]]))
print()

# ---- 2) _usb_cc_faults (default = warning) -------------------------------
print("_usb_cc_faults (default: warning)")
fa, wa = pcb._usb_cc_faults(board(cc1=False, cc2=False))
check("both CC floating -> 0 faults by default", fa == [])
check("both CC floating -> 1 warning naming both pins",
      len(wa) == 1 and "CC1" in wa[0] and "CC2" in wa[0])
check("  warning is on J1", "J1" in wa[0])
check("  warning explains the Rd requirement", "5.1k" in wa[0])

fa, wa = pcb._usb_cc_faults(board(cc1=True, cc2=False))
check("CC1 wired, CC2 floating -> 1 warning (CC2 only)",
      fa == [] and len(wa) == 1 and "CC2" in wa[0] and "CC1" not in wa[0])

check("both CC wired -> clean", pcb._usb_cc_faults(board(cc1=True, cc2=True)) == ([], []))

# a non-USB part with a floating pin must NOT be flagged by the CC check
nonusb = {"nets": [["N", [["U9", "1", ""], ["R9", "1", ""]]]],
          "parts": [["U9", "ATmega328P", "MCU", [["1", ""], ["2", "PD0"]], FP],
                    ["R9", "R", "Device", [["1", ""], ["2", ""]], FP]]}
check("non-USB floating pin -> not a CC hit", pcb._usb_cc_faults(nonusb) == ([], []))
print()

# ---- 3) promote to a hard fault via the toggle ---------------------------
print("_usb_cc_faults (FAIL_ON_FLOATING_USB_CC = True)")
pcb.FAIL_ON_FLOATING_USB_CC = True
fa, wa = pcb._usb_cc_faults(board(cc1=False, cc2=False))
check("toggle on: CC floating -> 1 fault naming both, 0 warnings",
      len(fa) == 1 and "CC1" in fa[0] and "CC2" in fa[0] and wa == [])
pcb.FAIL_ON_FLOATING_USB_CC = False
print()

# ---- 4) _decide integration ----------------------------------------------
print("_decide (USB-C floating CC)")
USB = board(cc1=False, cc2=False)
r = pcb._decide(0, True, USB, "", "", "")
check("default (warn): floating CC -> still BUILT", r.status == "built")
check("  CC warning surfaced on BUILT", ("CC1" in r and "CC2" in r))

# wired CC -> BUILT with no CC warning
ok = board(cc1=True, cc2=True)
r = pcb._decide(0, True, ok, "", "", "")
check("wired CC -> BUILT, no CC warning", r.status == "built" and "enumerate" not in r)

# flip to fault: the same floating-CC board now FAILS
pcb.FAIL_ON_FLOATING_USB_CC = True
r = pcb._decide(0, True, USB, "", "", "")
check("toggle on: floating CC -> FAILED", r.status == "failed")
check("  reported under electrical faults", "Electrical faults" in r and "enumerate" in r)
pcb.FAIL_ON_FLOATING_USB_CC = False
print()

print(f"==== {_p} passed, {_f} failed ====")
raise SystemExit(1 if _f else 0)
