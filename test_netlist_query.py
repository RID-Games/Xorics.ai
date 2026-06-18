# Xorics — Layer-1 netlist_query tests. Plain-assert, no pytest dependency.
# Run: python3 test_netlist_query.py
#
# These fixtures mirror the inspector's output SHAPE. They prove the LOGIC is
# correct given that shape. They do NOT prove the real inspector emits this shape
# (esp. blank pin NAMES on passives) — capture_real_powerdata.py does that on the
# box. Keeping those two separate is the #6 lesson: a green fixture is not reality.

import netlist_query as nq

_fail = 0
_pass = 0


def check(label, cond):
    global _fail, _pass
    if cond:
        _pass += 1
        print(f"  ok   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}")


# --- A faithful reproduction of the OLD, buggy, name-ONLY floating check, so we
#     can prove the new num-keyed logic catches what it silently missed (#6). ---
def old_name_only_fully_floating(data):
    nets = data.get("nets", []) or []
    parts = data.get("parts", []) or []
    connected = {}
    for nm, nodes in nets:
        if len(nodes) < 2:
            continue
        for ref, num, pname in nodes:
            if pname:                                   # <-- blank name => pin dropped
                connected.setdefault(ref, set()).add(pname)
    hard = []
    for ref, name, lib, pnames in parts:
        pins = [p for p in pnames if p]                 # <-- blank name => pin dropped
        if not pins:
            continue
        conn = connected.get(ref, set())
        floating = [p for p in pins if p not in conn]
        if len(pins) >= 2 and len(floating) == len(pins):
            hard.append(ref)
    return hard


# ===========================================================================
# Fixtures
# ===========================================================================

# #6 EXACT CASE: a crystal whose pins carry NUMs but BLANK names, wired to nothing.
# nets: only the (auto-named) 1-node stubs + an unrelated 2-node rail so the netlist
# is non-trivial. The crystal Y1 touches no >=2-node net.
FLOATING_BLANK_XTAL = {
    "nets": [
        ["VCC", [["U1", "1", "VCC"], ["C1", "1", "1"]]],     # a real 2-node net
        ["N$1", [["Y1", "1", ""]]],                          # crystal pin 1, alone
        ["N$2", [["Y1", "2", ""]]],                          # crystal pin 2, alone
    ],
    "parts": [
        ["Y1", "Crystal", "Device", [["1", ""], ["2", ""]]],  # blank NAMES, num only
        ["U1", "MCU", "MCU_Module", [["1", "VCC"], ["2", "GND"]]],
        ["C1", "C", "Device", [["1", "1"], ["2", "2"]]],
    ],
}

# The SAME physical board as the OLD inspector emitted it: part pins were bare
# name STRINGS, so a blank-name crystal arrived as ["", ""]. This is the exact
# data the old name-only checker received — and silently skipped.
FLOATING_BLANK_XTAL_OLD = {
    "nets": [
        ["VCC", [["U1", "1", "VCC"], ["C1", "1", "1"]]],
        ["N$1", [["Y1", "1", ""]]],
        ["N$2", [["Y1", "2", ""]]],
    ],
    "parts": [
        ["Y1", "Crystal", "Device", ["", ""]],   # blank-name pins as bare strings
        ["U1", "MCU", "MCU_Module", ["VCC", "GND"]],
        ["C1", "C", "Device", ["1", "1"]],
    ],
}

# Half-wired crystal: pin 1 wired into a 2-node net, pin 2 floating.
HALF_WIRED_XTAL = {
    "nets": [
        ["XIN", [["Y1", "1", ""], ["U1", "5", "XTAL_IN"]]],   # pin 1 connected
        ["N$1", [["Y1", "2", ""]]],                            # pin 2 alone
    ],
    "parts": [
        ["Y1", "Crystal", "Device", [["1", ""], ["2", ""]]],
        ["U1", "MCU", "MCU_Module", [["5", "XTAL_IN"], ["6", "XTAL_OUT"]]],
    ],
}

# Properly wired crystal: both pins on 2-node nets.
WIRED_XTAL = {
    "nets": [
        ["XIN", [["Y1", "1", ""], ["U1", "5", "XTAL_IN"]]],
        ["XOUT", [["Y1", "2", ""], ["U1", "6", "XTAL_OUT"]]],
    ],
    "parts": [
        ["Y1", "Crystal", "Device", [["1", ""], ["2", ""]]],
        ["U1", "MCU", "MCU_Module", [["5", "XTAL_IN"], ["6", "XTAL_OUT"]]],
    ],
}

# Same-name collapse guard: an IC with TWO pins both named "GND" (nums 4 & 9).
# Pin 4 is wired; pin 9 is floating. Name-keying collapses both to "GND" and
# wrongly treats 9 as connected. Num-keying keeps them distinct.
SAME_NAME_PINS = {
    "nets": [
        ["GND", [["U1", "4", "GND"], ["C1", "2", "2"]]],   # pin 4 connected
        ["VCC", [["U1", "1", "VCC"], ["C1", "1", "1"]]],
        ["N$1", [["U1", "9", "GND"]]],                     # pin 9 (also "GND") alone
    ],
    "parts": [
        ["U1", "IC", "Lib", [["1", "VCC"], ["4", "GND"], ["9", "GND"]]],
        ["C1", "C", "Device", [["1", "1"], ["2", "2"]]],
    ],
}

# Regulator with input and output shorted onto the SAME net.
REG_SHORTED = {
    "nets": [
        ["VBUS", [["U1", "3", "VI"], ["U1", "2", "VO"], ["C1", "1", "1"]]],
    ],
    "parts": [
        ["U1", "AMS1117-3.3", "Regulator_Linear", [["3", "VI"], ["2", "VO"], ["1", "GND"]]],
        ["C1", "C", "Device", [["1", "1"], ["2", "2"]]],
    ],
}

# Regulator wired correctly: input and output on different nets.
REG_OK = {
    "nets": [
        ["VBUS", [["U1", "3", "VI"], ["C1", "1", "1"]]],
        ["+3V3", [["U1", "2", "VO"], ["C2", "1", "1"]]],
    ],
    "parts": [
        ["U1", "AMS1117-3.3", "Regulator_Linear", [["3", "VI"], ["2", "VO"], ["1", "GND"]]],
        ["C1", "C", "Device", [["1", "1"], ["2", "2"]]],
        ["C2", "C", "Device", [["1", "1"], ["2", "2"]]],
    ],
}

# OLD part-pin shape (bare name strings) — rollout-compat: a floating 2-pin part.
OLD_SHAPE_FLOATING = {
    "nets": [["VCC", [["U1", "1", "VCC"], ["C1", "1", "1"]]]],
    "parts": [
        ["Y1", "Crystal", "Device", ["1", "2"]],   # bare strings, not [num,name]
        ["U1", "MCU", "MCU_Module", ["VCC", "GND"]],
        ["C1", "C", "Device", ["1", "2"]],
    ],
}

# Non-electrical part (mounting hole) with everything "floating" — must be ignored.
MOUNTING_HOLE = {
    "nets": [["VCC", [["U1", "1", "VCC"], ["C1", "1", "1"]]]],
    "parts": [
        ["H1", "MountingHole", "Mechanical", [["1", "1"], ["2", "2"]]],
        ["U1", "MCU", "MCU_Module", [["1", "VCC"], ["2", "GND"]]],
        ["C1", "C", "Device", [["1", "1"], ["2", "2"]]],
    ],
}


# ===========================================================================
# Tests
# ===========================================================================
print("pin identity")
check("pin_key prefers num", nq.pin_key("3", "") == "3")
check("pin_key falls back to name", nq.pin_key("", "VCC") == "VCC")
check("pin_key blank both -> ''", nq.pin_key("", "") == "")
check("pin_label prefers name", nq.pin_label("3", "VCC") == "VCC")
check("pin_label falls back to num", nq.pin_label("3", "") == "3")

print("#6 — blank-name fully-floating crystal")
ff = nq.fully_floating_parts(FLOATING_BLANK_XTAL)
check("Y1 flagged fully-floating", any(r == "Y1" for r, _n, _c in ff))
check("only Y1 flagged (U1/C1 are wired)", [r for r, _n, _c in ff] == ["Y1"])
# the whole point: the OLD logic missed exactly this
check("OLD name-only logic MISSED Y1 (proves the bug)",
      old_name_only_fully_floating(FLOATING_BLANK_XTAL_OLD) == [])

print("half-wired crystal -> warning, not fault")
check("not fully floating", [r for r, _n, _c in nq.fully_floating_parts(HALF_WIRED_XTAL)] == [])
pf = nq.partially_floating_parts(HALF_WIRED_XTAL)
check("Y1 reported partial", any(r == "Y1" for r, _n, _p in pf))
check("the floating pin is labeled '2'",
      any(r == "Y1" and labels == ["2"] for r, _n, labels in pf))

print("properly wired crystal -> clean")
check("nothing fully floating", nq.fully_floating_parts(WIRED_XTAL) == [])
check("nothing partially floating", nq.partially_floating_parts(WIRED_XTAL) == [])
check("Y1 pin 1 reads connected", nq.is_connected(WIRED_XTAL, "Y1", num="1"))

print("same-name pin collapse guard")
pf2 = nq.partially_floating_parts(SAME_NAME_PINS)
check("U1 pin 9 (a second 'GND') flagged floating",
      any(r == "U1" and "9" in labels for r, _n, labels in pf2)
      or any(r == "U1" and "GND" in labels for r, _n, labels in pf2))
check("num-key keeps pin 9 distinct (NOT connected)",
      not nq.is_connected(SAME_NAME_PINS, "U1", num="9"))
check("num-key: pin 4 IS connected", nq.is_connected(SAME_NAME_PINS, "U1", num="4"))

print("regulator I/O")
regs = nq.regulators(REG_SHORTED)
check("regulator detected", any(r == "U1" for r, _n, _i, _o in regs))
r0 = regs[0]
in_net, out_net = nq.regulator_io_nets(REG_SHORTED, r0[0], r0[2], r0[3])
check("shorted reg: in_net == out_net", in_net is not None and in_net == out_net)
regs_ok = nq.regulators(REG_OK)
ro = regs_ok[0]
in2, out2 = nq.regulator_io_nets(REG_OK, ro[0], ro[2], ro[3])
check("ok reg: in_net != out_net", in2 and out2 and in2 != out2)

print("rollout: OLD bare-string part-pin shape")
ffo = nq.fully_floating_parts(OLD_SHAPE_FLOATING)
check("Y1 still flagged with old shape", any(r == "Y1" for r, _n, _c in ffo))

print("non-electrical parts ignored")
check("mounting hole NOT flagged",
      not any(r == "H1" for r, _n, _c in nq.fully_floating_parts(MOUNTING_HOLE)))

print("robustness on degenerate input")
check("None data -> no parts", nq.parts(None) == [])
check("None data -> no fully-floating", nq.fully_floating_parts(None) == [])
check("empty dict -> safe", nq.fully_floating_parts({}) == [])
check("malformed part record tolerated",
      nq.fully_floating_parts({"parts": [["X1"]], "nets": []}) == [])

print("voltage token parser parity")
check("3V3 -> 3.3", nq.net_voltage_tokens("3V3") == {3.3})
check("+3V3 -> 3.3", nq.net_voltage_tokens("+3V3") == {3.3})
check("5V -> 5.0", nq.net_voltage_tokens("5V") == {5.0})
check("VBUS -> 5.0", nq.net_voltage_tokens("VBUS") == {5.0})
check("GND -> nothing", nq.net_voltage_tokens("GND") == set())
check("noise 'GPIO5' -> nothing", nq.net_voltage_tokens("GPIO5") == set())

print()
print(f"==== {_pass} passed, {_fail} failed ====")
raise SystemExit(1 if _fail else 0)
