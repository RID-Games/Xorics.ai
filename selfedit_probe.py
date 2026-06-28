#!/usr/bin/env python3
# Xorics — LIVE probe: write_file through real podman + the baked image + the real suite.
"""Run on RIDGames (needs podman + localhost/xorics-sandbox:latest built):
    XORICS_SANDBOX_IMAGE=localhost/xorics-sandbox:latest python3 selfedit_probe.py

Model-free on purpose: it drives write_file directly so the proof is deterministic
(the LLM-driven `/selfedit ...` moment is a separate, interactive smoke). It proves
the things hermetic mocks can't — that a REAL edit runs the REAL suite in a REAL
container and the gate holds:
  A) a harmless edit that keeps the suite green  -> WRITE VERIFIED, live tree untouched
  B) an edit that breaks the suite               -> WRITE REJECTED, live tree untouched
  C) a no-op write                               -> NO CHANGE (suite never runs)
The live tree is never mutated in any case — that is the whole safety claim.
XORICS-FEATURE: self-edit
"""
import os
import sys

import sandbox
import xorics

TARGET = "notebook.py"          # a real repo module: valid Python, imported by xorics
PASS = FAIL = 0


def check(label, cond):
    global PASS, FAIL
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    PASS += bool(cond); FAIL += (not cond)


def live_bytes(rel):
    with open(os.path.join(xorics.REPO_ROOT, rel), "rb") as f:
        return f.read()


print(f"repo   : {xorics.REPO_ROOT}")
print(f"runtime: {sandbox.container_runtime()}")
print(f"image  : {xorics._selfedit_image()}")
if sandbox.container_runtime() is None:
    print("\nNo podman/docker on PATH — this probe must run on RIDGames. Aborting.")
    sys.exit(2)

baseline = live_bytes(TARGET)
original = baseline.decode("utf-8", "replace")

# A) harmless real edit -> the real suite stays green -> VERIFIED ----------------
print("\n[A] harmless edit (append a comment) — expect WRITE VERIFIED, real suite green")
xorics._selfedit_reset()
outA = xorics.write_file(TARGET, original + "\n# selfedit live probe: harmless trailing comment\n")
print("    ->", " ".join(outA.split())[:200])
check("A: WRITE VERIFIED", outA.startswith("WRITE VERIFIED"))
check("A: live tree byte-for-byte unchanged", live_bytes(TARGET) == baseline)

# B) breaking edit -> the real suite fails -> REJECTED --------------------------
print("\n[B] breaking edit (syntax error) — expect WRITE REJECTED, real suite red")
xorics._selfedit_reset()
outB = xorics.write_file(TARGET, "def broken(:\n    pass\n")
print("    ->", " ".join(outB.split())[:200])
check("B: WRITE REJECTED", outB.startswith("WRITE REJECTED"))
check("B: live tree byte-for-byte unchanged", live_bytes(TARGET) == baseline)

# C) no-op -> NO CHANGE, suite never runs ---------------------------------------
print("\n[C] no-op write (identical content) — expect NO CHANGE")
xorics._selfedit_reset()
outC = xorics.write_file(TARGET, original)
print("    ->", " ".join(outC.split())[:200])
check("C: NO CHANGE", outC.startswith("NO CHANGE"))
check("C: live tree byte-for-byte unchanged", live_bytes(TARGET) == baseline)

print(f"\n{PASS}/{PASS + FAIL} live checks passed")
sys.exit(0 if FAIL == 0 else 1)
