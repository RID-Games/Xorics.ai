#!/usr/bin/env python3
# Xorics — test: brick 3 (skill-recall). Drives the REAL xorics._recall_for via
# stubbed heavy deps, plus the write->recall round trip. Hermetic tmp DB.
"""Run: python3 test_brick3_recall.py"""

import os
import sys
import types
import tempfile

os.environ["XORICS_DATA_DIR"] = tempfile.mkdtemp(prefix="xorics-brick3-test-")

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


# Empty store: recall returns nothing (and the system prompt would be untouched).
check("recall on empty store returns ''", xorics._recall_for("anything at all") == "")

# Record a verified skill the way run_coder would (the brick-2 path).
ino = _touch("blink.ino", "void setup(){}")
xorics._record_skill_from_success(
    "Make an ESP32 LED blink sketch",
    "compile_check returned COMPILE OK\n\n```cpp\nvoid setup(){}\n```",
    "compile_check", ino)

# A matching delegation task now recalls it, as an injectable block.
block = xorics._recall_for("get my esp32 sketch to compile and blink an LED")
check("matching task recalls a non-empty block", bool(block))
check("recalled block is the injectable 'Recalled skills' format", "Recalled skills" in block)
check("recalled block names the skill", "Make an ESP32 LED blink sketch" in block)
check("recalled block carries the working code", "void setup()" in block)

# Recall is sharp: an unrelated task injects nothing.
check("unrelated task recalls ''", xorics._recall_for("write me a haiku about rain") == "")

# Simulate the actual injection run_coder does, and prove it only grows the prompt on a hit.
base = "You are the Xorics coding specialist. "
sys_prompt = base
r = xorics._recall_for("esp32 blink compile")
if r:
    sys_prompt += "\n\n" + r
check("injection appends recall onto the coder brief", sys_prompt.startswith(base) and len(sys_prompt) > len(base))

# Best-effort: a recall failure can NEVER break a delegation (returns '', no raise).
_orig = skills.search_skills
skills.search_skills = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
raised = False
try:
    out = xorics._recall_for("esp32 blink compile")
except Exception:
    raised = True
finally:
    skills.search_skills = _orig
check("recall failure does not propagate", not raised)
check("recall failure yields '' (prompt left untouched)", out == "")

print(f"\n{PASS}/{PASS + FAIL} checks passed")
sys.exit(0 if FAIL == 0 else 1)
