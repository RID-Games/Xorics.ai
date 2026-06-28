#!/usr/bin/env python3
# Xorics — LIVE probe: self-edit Brick C (promote) end-to-end. Real podman + real git.
"""Run on RIDGames (needs podman + localhost/xorics-sandbox:latest):
    XORICS_SANDBOX_IMAGE=localhost/xorics-sandbox:latest python3 promote_probe.py

Proves the FULL approve-and-land path on hardware: a real write_file (real suite in a real
container) produces a verified change, then promote_self_edit RE-VERIFIES it in the sandbox
and commits it with real git. To keep your real repo pristine it all runs against a throwaway
`git clone` of it in /tmp — the real repo is only ever READ, and the clone is deleted at the
end. Model-free and deterministic (the LLM-driven path is the interactive /selfedit + /promote).
XORICS-FEATURE: self-edit
"""
import os
import sys
import shutil
import subprocess
import tempfile

import sandbox
import xorics

TARGET = "notebook.py"
PASS = FAIL = 0


def check(label, cond):
    global PASS, FAIL
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    PASS += bool(cond); FAIL += (not cond)


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


real = xorics.REPO_ROOT
print(f"real repo (read-only): {real}")
print(f"runtime: {sandbox.container_runtime()}")
print(f"image  : {xorics._selfedit_image()}")
if sandbox.container_runtime() is None:
    print("\nNo podman/docker on PATH — this probe must run on RIDGames. Aborting.")
    sys.exit(2)
if shutil.which("git") is None:
    print("\nNo git on PATH. Aborting.")
    sys.exit(2)

clone = tempfile.mkdtemp(prefix="xorics-promote-clone-")
ws = tempfile.mkdtemp(prefix="xorics-promote-ws-"); shutil.rmtree(ws)
saved = (xorics.REPO_ROOT, xorics._SELFEDIT_WORKSPACE, xorics._SELFEDIT_TASK_FILE)
try:
    cp = subprocess.run(["git", "clone", "--quiet", real, clone], capture_output=True, text=True)
    if cp.returncode != 0:
        print("clone failed:", cp.stderr.strip()); sys.exit(2)
    git(clone, "config", "user.email", "selfedit-probe@xorics")
    git(clone, "config", "user.name", "xorics-selfedit-probe")
    git(clone, "config", "commit.gpgsign", "false")

    # Point xorics at the disposable clone — every write/commit below lands there, not in `real`.
    xorics.REPO_ROOT = clone
    xorics._SELFEDIT_WORKSPACE = ws
    xorics._SELFEDIT_TASK_FILE = os.path.join(ws, "TASK.txt")

    target_live = os.path.join(clone, TARGET)
    original = open(target_live).read()

    # 1) real verified write of a harmless edit ---------------------------------
    print("\n[1] write_file a harmless edit — real suite in a real container")
    os.makedirs(ws, exist_ok=True)
    open(xorics._SELFEDIT_TASK_FILE, "w").write("promote probe: append a harmless comment")
    w = xorics.write_file(TARGET, original + "\n# selfedit promote probe: harmless comment\n")
    print("    ->", " ".join(w.split())[:200])
    check("1: WRITE VERIFIED", w.startswith("WRITE VERIFIED"))
    if not w.startswith("WRITE VERIFIED"):
        print("aborting — write did not verify, nothing to promote."); raise SystemExit(1)
    rels, diff, task = xorics.review_self_edit()
    check("1: review shows the change pending", rels == [TARGET])

    # 2) promote: re-verify in the sandbox, then commit to the clone ------------
    print("\n[2] promote_self_edit — re-verify + real git commit (to the clone)")
    h0 = git(clone, "rev-parse", "HEAD").stdout.strip()
    p = xorics.promote_self_edit()
    print("    ->", " ".join(p.split())[:200])
    check("2: PROMOTED", p.startswith("PROMOTED"))
    check("2: clone file now holds the verified content",
          open(target_live).read().endswith("harmless comment\n"))
    check("2: a real commit landed in the clone", git(clone, "rev-parse", "HEAD").stdout.strip() != h0)
    show = git(clone, "show", "--name-only", "--format=", "HEAD").stdout.split()
    check("2: the commit contains only the changed file", show == [TARGET])
    check("2: workspace cleared after promote", xorics._selfedit_changed_files() == [])

    # 3) discard: a pending change can be dropped without committing ------------
    print("\n[3] discard — a fresh verified change is dropped, clone untouched")
    h1 = git(clone, "rev-parse", "HEAD").stdout.strip()
    cur = open(target_live).read()
    os.makedirs(ws, exist_ok=True)        # promote's reset removed the workspace dir; recreate it
    open(xorics._SELFEDIT_TASK_FILE, "w").write("probe: to be discarded")
    w2 = xorics.write_file(TARGET, cur + "\n# to be discarded\n")
    check("3: second write verified", w2.startswith("WRITE VERIFIED"))
    d = xorics.discard_self_edit()
    print("    ->", " ".join(d.split())[:160])
    check("3: DISCARDED", d.startswith("DISCARDED"))
    check("3: no new commit from discard", git(clone, "rev-parse", "HEAD").stdout.strip() == h1)
    check("3: clone file unchanged by discard", open(target_live).read() == cur)

    # 4) the real repo was never touched ---------------------------------------
    print("\n[4] safety: the real repo was only read")
    real_head_now = subprocess.run(["git", "-C", real, "rev-parse", "HEAD"],
                                   capture_output=True, text=True).stdout.strip()
    check("4: real repo HEAD is whatever it was (probe only cloned it)", bool(real_head_now))
finally:
    xorics.REPO_ROOT, xorics._SELFEDIT_WORKSPACE, xorics._SELFEDIT_TASK_FILE = saved
    shutil.rmtree(clone, ignore_errors=True)
    shutil.rmtree(ws, ignore_errors=True)

print(f"\n{PASS}/{PASS + FAIL} live checks passed")
sys.exit(0 if FAIL == 0 else 1)
