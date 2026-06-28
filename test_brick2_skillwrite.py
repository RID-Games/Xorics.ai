#!/usr/bin/env python3
# Xorics — test: brick 2 (skill-write-on-success). Drives the REAL
# xorics._record_skill_from_success by stubbing the heavy/native deps so xorics.py
# imports without skidl/arduino/openai. Hermetic tmp DB; no live services.
"""Run: python3 test_brick2_skillwrite.py"""

import os
import sys
import types
import tempfile

os.environ["XORICS_DATA_DIR"] = tempfile.mkdtemp(prefix="xorics-brick2-test-")

# Stub the heavy deps xorics.py imports at module load, so we can import the real thing.
for _n in ["openai", "datasheet_rag", "web_datasheets", "firmware_tools", "notebook", "pcb_tools"]:
    sys.modules[_n] = types.ModuleType(_n)
sys.modules["openai"].OpenAI = lambda *a, **k: object()
setattr(sys.modules["datasheet_rag"], "search_datasheets", lambda *a, **k: None)
setattr(sys.modules["web_datasheets"], "fetch_datasheet", lambda *a, **k: None)
for _n in ["compile_check", "save_sketch"]:
    setattr(sys.modules["firmware_tools"], _n, lambda *a, **k: None)
setattr(sys.modules["notebook"], "Notebook", type("Notebook", (), {}))
for _n in ["check_circuit", "check_circuit_file", "find_part", "find_footprint", "part_pins", "save_circuit"]:
    setattr(sys.modules["pcb_tools"], _n, lambda *a, **k: None)

import store
import skills
import xorics

store.init()
skills.init()

PASS, FAIL = 0, 0


def check(label, cond):
    global PASS, FAIL
    ok = bool(cond)
    PASS += ok
    FAIL += (not ok)
    print(("  ok   " if ok else "  FAIL ") + label)


def _touch(name, content="x"):
    p = os.path.join(os.environ["XORICS_DATA_DIR"], name)
    open(p, "w").write(content)
    return p


# --- title helper ---
check("title collapses whitespace + truncates to <=70",
      xorics._skill_title("build   an  ESP32   thing " + "x" * 100).startswith("build an ESP32 thing")
      and len(xorics._skill_title("a " * 100)) <= 70)
check("empty task -> 'skill'", xorics._skill_title("   ") == "skill")

# --- a firmware success writes ONE verified skill, mirrored to /skills ---
ino = _touch("blink.ino", "void setup(){}")
xorics._record_skill_from_success(
    "Make an ESP32 LED blink sketch",
    "compile_check returned COMPILE OK\n\n```cpp\nvoid setup(){}\n```",
    "compile_check", ino)
fw = skills.list_skills("firmware")
check("firmware success recorded exactly one skill", len(fw) == 1)
check("skill carries the compile_check validator", fw and fw[0]["validator"] == "compile_check")
check("skill domain is firmware", fw and fw[0]["domain"] == "firmware")
check("skill mirrored to /skills (file_id set)", fw and bool(fw[0]["file_id"]))
check("recall finds the new firmware skill",
      len(skills.search_skills("esp32 blink sketch")) >= 1)

# --- repeating the SAME task dedups: count stays, use counter bumps ---
xorics._record_skill_from_success(
    "Make an ESP32 LED blink sketch",
    "compile_check returned COMPILE OK\n\n```cpp\nvoid setup(){}\n```",
    "compile_check", ino)
fw2 = skills.list_skills("firmware")
check("repeat task does NOT add a duplicate", len(fw2) == 1)
check("repeat task bumps times_used", fw2 and fw2[0]["times_used"] == 1)

# --- a pcb/circuit success records under the pcb domain ---
circ = _touch("sensor.py", "from skidl import *")
xorics._record_skill_from_success(
    "Design an ambient-light sensor module",
    "check_circuit returned BUILT\n\n```python\n# circuit\n```",
    "check_circuit_file", circ)
check("pcb success recorded under pcb domain", len(skills.list_skills("pcb")) == 1)

# --- safety net: a skill-write failure can NEVER break run_coder's success return ---
before = len(skills.list_skills())
_orig = skills.save_skill
skills.save_skill = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
raised = False
try:
    xorics._record_skill_from_success("A brand new task", "result", "compile_check", _touch("new.ino"))
except Exception:
    raised = True
finally:
    skills.save_skill = _orig
check("skill-write failure does not propagate into the loop", not raised)
check("failed skill-write adds nothing", len(skills.list_skills()) == before)

print(f"\n{PASS}/{PASS + FAIL} checks passed")
sys.exit(0 if FAIL == 0 else 1)
