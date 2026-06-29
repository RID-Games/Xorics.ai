#!/usr/bin/env python3
# Xorics — test: overclaim gate in capabilities.self_knowledge(). Hermetic; stubs skills.
"""Run: python3 test_capabilities.py   (no live services; stubs skills.list_skills)."""

import sys

import capabilities
import skills

PASS, FAIL = 0, 0


def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")


# --- Helpers ----------------------------------------------------------------

_saved_list_skills = skills.list_skills
_saved_init = skills.init


def _stub_counts(firmware=0, pcb=0, android=0):
    """Stub skills.list_skills to return a list of length n for the given domain."""
    table = {"firmware": firmware, "pcb": pcb, "android": android}

    def fake_list_skills(domain=None):
        n = table.get(domain, 0)
        return [object()] * n

    skills.list_skills = fake_list_skills
    skills.init = lambda: None


def _restore():
    skills.list_skills = _saved_list_skills
    skills.init = _saved_init


def _line(text, domain):
    """Pull the line that belongs to the given domain from a rendered self-model."""
    for line in text.split("\n"):
        if line.startswith(capabilities.CAPABILITIES_BY_DOMAIN[domain]):
            return line
    return ""


# --- Tests ------------------------------------------------------------------

# 1) With every count 0, self_knowledge() is a non-empty string carrying all
#    three capability labels.
_stub_counts(firmware=0, pcb=0, android=0)
text = capabilities.self_knowledge()
check("(1) self_knowledge() is a non-empty string", isinstance(text, str) and bool(text))
check('(1) mentions "Embedded firmware (C / Arduino / ESP32)"',
      "Embedded firmware (C / Arduino / ESP32)" in text)
check('(1) mentions "PCB / circuit design (SKiDL → KiCad)"',
      "PCB / circuit design (SKiDL → KiCad)" in text)
check('(1) mentions "Android app development"',
      "Android app development" in text)

# 2) Firmware count 0 -> no "yes", does say "treat as unproven".
_stub_counts(firmware=0)
fw_line = _line(capabilities.self_knowledge(), "firmware")
check('(2) firmware line with 0 does NOT contain "yes"', "yes" not in fw_line)
check('(2) firmware line with 0 contains "treat as unproven"',
      "treat as unproven" in fw_line)

# 3) Firmware count 3 -> "yes", "3 verified skills on file", no "treat as unproven".
_stub_counts(firmware=3)
fw_line = _line(capabilities.self_knowledge(), "firmware")
check('(3) firmware line with 3 contains "yes"', "yes" in fw_line)
check('(3) firmware line with 3 contains "3 verified skills on file"',
      "3 verified skills on file" in fw_line)
check('(3) firmware line with 3 does NOT contain "treat as unproven"',
      "treat as unproven" not in fw_line)

# 4) The PCB line always contains "partial" and names the gap "verified KiCad netlist".
_stub_counts(firmware=0, pcb=0)
pcb_line = _line(capabilities.self_knowledge(), "pcb")
check('(4) pcb line contains "partial"', "partial" in pcb_line)
check('(4) pcb line names the gap "verified KiCad netlist"',
      "verified KiCad netlist" in pcb_line)

# 5) The Android line contains "no".
_stub_counts(android=0)
and_line = _line(capabilities.self_knowledge(), "android")
check('(5) android line contains "no"', "no" in and_line)

# 6) Singular form: count == 1 -> "1 verified skill on file" (no "s").
_stub_counts(firmware=1)
fw_line = _line(capabilities.self_knowledge(), "firmware")
check('(6) firmware line with 1 contains "1 verified skill on file"',
      "1 verified skill on file" in fw_line)
check('(6) firmware line with 1 does NOT say "1 verified skills on file"',
      "1 verified skills on file" not in fw_line)

# 7) If skills.list_skills raises, self_knowledge() still returns a string.
def _boom(_domain=None):
    raise RuntimeError("simulated skills DB failure")

skills.list_skills = _boom
try:
    out = capabilities.self_knowledge()
    check("(7) self_knowledge() survives a raising list_skills",
          isinstance(out, str) and bool(out))
finally:
    _restore()

# 8) self_knowledge_domain(domain): per-domain single-line lookup.
_stub_counts(firmware=2, pcb=0, android=0)
unknown = capabilities.self_knowledge_domain("quantum-telekinesis")
check("(8) self_knowledge_domain unknown domain returns None", unknown is None)
firmware_line = capabilities.self_knowledge_domain("firmware")
check("(8) self_knowledge_domain firmware returns a non-empty string",
      isinstance(firmware_line, str) and bool(firmware_line))
check('(8) firmware line starts with the firmware label',
      firmware_line.startswith(capabilities.CAPABILITIES_BY_DOMAIN["firmware"]))
check('(8) firmware line contains "2 verified skills on file"',
      "2 verified skills on file" in firmware_line)

print(f"\n{PASS}/{PASS + FAIL} checks passed")
sys.exit(0 if FAIL == 0 else 1)