#!/usr/bin/env python3
# Xorics — test: write_file (self-edit verified write) + run_self_edit wiring. Hermetic.
"""Run: python3 test_write_file.py

Stubs sandbox.run (test_sandbox already proves the real runner — green mocks lie, so
the END-TO-END proof through real podman + the baked image is a separate probe on
RIDGames). Here we pin write_file's GATE logic: a write counts only if the suite
exits 0 AND the file actually changed, the live tree is NEVER mutated, the verify
command is fixed (not caller-chosen), and paths can't escape the repo. Also checks
run_self_edit hands the coder ONLY the self-edit toolset, on the selfedit tag.
XORICS-FEATURE: self-edit
"""
import os
import sys
import shutil
import tempfile
import types

import sandbox
import xorics

PASS, FAIL = 0, 0


def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")


# --- harness: a throwaway "repo", with write_file pointed at it -----------------
def _mk_repo():
    d = tempfile.mkdtemp(prefix="xorics-selfedit-repo-")
    with open(os.path.join(d, "notebook.py"), "w") as f:
        f.write("LIVE = 1\n")
    os.makedirs(os.path.join(d, "venv", "bin"))         # must be excluded from the stage copy
    with open(os.path.join(d, "venv", "bin", "python"), "w") as f:
        f.write("x")
    return d


def _fake_run_factory(rc=0, present=True, error=None, calls=None):
    """Stand-in for sandbox.run: records the call and returns a real SandboxResult
    with artifacts marked present/absent, so write_file sees a genuine .ok."""
    def _run(repo_dir, cmd, *, artifacts=None, image=None, network=False, **kw):
        if calls is not None:
            calls.append({"repo_dir": repo_dir, "cmd": cmd, "artifacts": list(artifacts or []),
                          "image": image, "network": network})
        arts = {a: present for a in (artifacts or [])}
        return sandbox.SandboxResult(
            exit_code=(None if error else rc), stdout="...\nSuites: 18 passed, 0 failed\n",
            stderr="", artifacts=arts, elapsed=1.2, image=image or "img",
            runtime="podman", error=error)
    return _run


class _Patch:
    """Point write_file at a temp repo + temp workspace, and stub the runtime/sandbox.run."""
    def __init__(self, runtime="podman", run=None):
        self.runtime = runtime
        self.run = run

    def __enter__(self):
        self.repo = _mk_repo()
        self.ws = tempfile.mkdtemp(prefix="xorics-selfedit-ws-")
        self._save = (xorics.REPO_ROOT, xorics._SELFEDIT_WORKSPACE,
                      sandbox.run, sandbox.container_runtime)
        xorics.REPO_ROOT = self.repo
        xorics._SELFEDIT_WORKSPACE = self.ws
        sandbox.container_runtime = lambda: self.runtime
        if self.run is not None:
            sandbox.run = self.run
        return self

    def live(self, rel):
        with open(os.path.join(self.repo, rel)) as f:
            return f.read()

    def __exit__(self, *a):
        (xorics.REPO_ROOT, xorics._SELFEDIT_WORKSPACE,
         sandbox.run, sandbox.container_runtime) = self._save
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(self.ws, ignore_errors=True)


# --- 1: suite green + file changed -> WRITE VERIFIED, live untouched ------------
calls = []
with _Patch(run=_fake_run_factory(rc=0, present=True, calls=calls)) as p:
    out = xorics.write_file("notebook.py", "LIVE = 2\n")
    check("green+changed -> WRITE VERIFIED", out.startswith("WRITE VERIFIED"))
    check("verified mentions the file", "notebook.py" in out)
    check("live tree NOT mutated by a verified write", p.live("notebook.py") == "LIVE = 1\n")
    check("sandbox.run was invoked once", len(calls) == 1)
    check("verify cmd is FIXED (not caller-chosen)", calls and calls[0]["cmd"] == "./run_tests.sh")
    check("the edited file is required as an artifact", calls and calls[0]["artifacts"] == ["notebook.py"])
    check("run is hermetic (network False)", calls and calls[0]["network"] is False)

# --- 2: no-op write -> NO CHANGE, sandbox never called -------------------------
calls = []
with _Patch(run=_fake_run_factory(calls=calls)) as p:
    out = xorics.write_file("notebook.py", "LIVE = 1\n")   # byte-identical to live
    check("no-op -> NO CHANGE", out.startswith("NO CHANGE"))
    check("no-op never runs the suite", len(calls) == 0)

# --- 3: suite red -> WRITE REJECTED, live untouched, failing tail surfaced ------
with _Patch(run=_fake_run_factory(rc=1, present=True)) as p:
    out = xorics.write_file("notebook.py", "LIVE = 3\n")
    check("suite red -> WRITE REJECTED", out.startswith("WRITE REJECTED"))
    check("rejection surfaces suite output", "Suites:" in out)
    check("live tree NOT mutated by a rejected write", p.live("notebook.py") == "LIVE = 1\n")

# --- 4: exit 0 but artifact missing -> REJECTED (the honesty point) -------------
with _Patch(run=_fake_run_factory(rc=0, present=False)) as p:
    out = xorics.write_file("notebook.py", "LIVE = 4\n")
    check("exit 0 but file gone -> REJECTED (exit 0 alone never counts)", out.startswith("WRITE REJECTED"))

# --- 5: new file that keeps suite green -> VERIFIED ('created') -----------------
with _Patch(run=_fake_run_factory(rc=0, present=True)) as p:
    out = xorics.write_file("newmod.py", "X = 1\n")
    check("new file + green -> VERIFIED", out.startswith("WRITE VERIFIED"))
    check("new file reported as created", "created" in out)
    check("new file NOT written to the live tree", not os.path.exists(os.path.join(p.repo, "newmod.py")))

# --- 6: path traversal / absolute / dir -> refused, suite never called ----------
for bad, why in [("../escape.py", "parent-escape"),
                 ("/etc/passwd", "absolute"),
                 ("~/x.py", "home"),
                 ("venv", "directory")]:
    calls = []
    with _Patch(run=_fake_run_factory(calls=calls)) as p:
        out = xorics.write_file(bad, "PWNED\n")
        ok = out.startswith("refused") or out.startswith("write_file takes a path")
        check(f"refuses {why} path", ok)
        check(f"{why}: suite never ran", len(calls) == 0)

# --- 7: no container runtime -> ERROR, suite never called ----------------------
calls = []
with _Patch(runtime=None, run=_fake_run_factory(calls=calls)):
    out = xorics.write_file("notebook.py", "LIVE = 9\n")
    check("no runtime -> ERROR refusal", out.startswith("write_file ERROR") and "runtime" in out)
    check("no runtime: suite never ran", len(calls) == 0)

# --- 8: sandbox plumbing error -> ERROR ----------------------------------------
with _Patch(run=_fake_run_factory(error="timed out after 600s")):
    out = xorics.write_file("notebook.py", "LIVE = 5\n")
    check("sandbox error -> write_file ERROR", out.startswith("write_file ERROR") and "timed out" in out)

# --- 9: run_self_edit wiring (stub the agent loop; no model needed) -------------
seen = {}


def _fake_loop(model, messages, tools, *, checkpoint, tag):
    seen["model"], seen["tools"], seen["tag"], seen["checkpoint"] = model, tools, tag, checkpoint
    seen["sys"] = messages[0]["content"]
    return ("done: changed notebook.py", messages, None, {"built": False, "design_attempt": False})


_saved_loop = xorics._agent_loop
xorics._agent_loop = _fake_loop
try:
    r = xorics.run_self_edit("rename a var in notebook.py")
    check("run_self_edit returns the coder's final text", r == "done: changed notebook.py")
    check("run_self_edit runs on the CODER brain", seen.get("model") == xorics.CODER)
    check("run_self_edit hands over ONLY the self-edit toolset", seen.get("tools") is xorics.SELF_EDIT_TOOLS)
    check("run_self_edit uses the 'selfedit' tag (keeps the PCB convergence nudge inert)",
          seen.get("tag") == "selfedit")
    check("self-edit system prompt carries the self-edit guide", "OWN source code" in seen.get("sys", ""))
finally:
    xorics._agent_loop = _saved_loop

print(f"\n{PASS}/{PASS + FAIL} passed")
sys.exit(0 if FAIL == 0 else 1)
