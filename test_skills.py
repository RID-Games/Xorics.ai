#!/usr/bin/env python3
# Xorics — test: skill memory (store + recall + honesty gate). Hermetic; tmp DB.
"""Run: python3 test_skills.py   (sets its own XORICS_DATA_DIR; no live services)."""

import os
import sys
import tempfile

# Point storage at a throwaway dir BEFORE first use (store/skills read env every call).
os.environ["XORICS_DATA_DIR"] = tempfile.mkdtemp(prefix="xorics-skills-test-")

import store
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


store.init()
skills.init()

# 1) A verified skill saves, mirrors to the file store, and round-trips.
s = skills.save_skill(
    title="compile-esp32-sketch",
    trigger="building an Arduino/ESP32 firmware sketch that must actually compile",
    body="1. Write the .ino. 2. Run compile_check (arduino-cli, esp32:esp32). "
         "3. Read the real error, fix, repeat until SKETCH COMPILED.",
    validator="compile_check",
    domain="firmware",
    tags="esp32 arduino ino compile firmware",
)
check("verified skill returns an id", bool(s.get("id")))
check("body mirrored to file store (file_id set)", bool(s.get("file_id")))
check("skill is retrievable by id", skills.get_skill(s["id"]) is not None)
check("markdown appears under /skills in the file store",
      any(f["name"] == "compile-esp32-sketch.md"
          for f in store.list_files(folder="/skills")))

# 2) Honesty gate: no validator -> refused.
try:
    skills.save_skill("x", "y", "z", validator="")
    check("save with no validator is refused", False)
except skills.UnverifiedSkill:
    check("save with no validator is refused", True)

# 3) Honesty gate: source_path that doesn't exist -> refused.
try:
    skills.save_skill("x", "y", "z", validator="check_circuit",
                      source_path="/no/such/artifact.kicad_sch")
    check("save with missing source artifact is refused", False)
except skills.UnverifiedSkill:
    check("save with missing source artifact is refused", True)

# 3b) Honesty gate: source_path that DOES exist -> allowed.
real = os.path.join(os.environ["XORICS_DATA_DIR"], "real_artifact.ino")
open(real, "w").write("void setup(){} void loop(){}")
s2 = skills.save_skill("blink", "a minimal blink sketch", "use setup/loop",
                       validator="compile_check", domain="firmware",
                       source_path=real)
check("save with existing source artifact is allowed", bool(s2.get("id")))

# 4) Recall: a matching task surfaces the right skill, ranked.
hits = skills.search_skills("help me get my esp32 firmware sketch to compile")
check("recall returns at least one hit for a matching task", len(hits) >= 1)
check("recall ranks the esp32 compile skill first",
      hits and hits[0]["title"] == "compile-esp32-sketch")
check("recall hit carries a score", hits and "score" in hits[0])

# 5) Recall is sharp: an unrelated task returns nothing.
check("recall returns nothing for an unrelated task",
      skills.search_skills("what is the capital of France") == [])

# 6) Domain filter narrows the set.
check("domain filter returns only firmware skills",
      all(s["domain"] == "firmware" for s in skills.list_skills(domain="firmware")))

# 7) format_for_prompt produces an injectable block (and truncates long bodies).
block = skills.format_for_prompt(hits)
check("format_for_prompt yields a non-empty injectable block",
      "Recalled skills" in block and "compile-esp32-sketch" in block)
check("format_for_prompt of nothing is empty", skills.format_for_prompt([]) == "")

print(f"\n{PASS}/{PASS + FAIL} checks passed")
sys.exit(0 if FAIL == 0 else 1)
