#!/usr/bin/env python3
# Xorics — test: str_replace (surgical self-edit). Hermetic; stubs sandbox.run like
# test_write_file (the real runner is proven in test_sandbox — green mocks lie). Pins the
# snippet logic: a UNIQUE old_str is replaced and the rest of the file is preserved
# byte-for-byte, a missing / non-unique / empty old_str is refused, a no-op is caught,
# str_replace will NOT create a new file, paths can't escape the repo, and the live tree
# is NEVER mutated. XORICS-FEATURE: self-edit

import os
import sys
import shutil
import tempfile

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


_REPO_FILE = "notebook.py"
_REPO_BODY = "A = 1\nB = 2\nC = 3\n"


def _mk_repo():
    d = tempfile.mkdtemp(prefix="xorics-strreplace-repo-")
    with open(os.path.join(d, _REPO_FILE), "w") as f:
        f.write(_REPO_BODY)
    os.makedirs(os.path.join(d, "venv", "bin"))          # excluded from the stage copy
    with open(os.path.join(d, "venv", "bin", "python"), "w") as f:
        f.write("x")
    return d


def _fake_run_factory(rc=0, present=True, error=None, calls=None):
    def _run(repo_dir, cmd, *, artifacts=None, image=None, network=False, **kw):
        if calls is not None:
            calls.append({"cmd": cmd, "artifacts": list(artifacts or []), "network": network})
        arts = {a: present for a in (artifacts or [])}
        return sandbox.SandboxResult(
            exit_code=(None if error else rc), stdout="...\nSuites: 24 passed, 0 failed\n",
            stderr="", artifacts=arts, elapsed=1.0, image=image or "img",
            runtime="podman", error=error)
    return _run


class _Patch:
    """Point str_replace at a temp repo + temp workspace, and stub runtime/sandbox.run."""
    def __init__(self, run=None):
        self.run = run

    def __enter__(self):
        self.repo = _mk_repo()
        self.ws = tempfile.mkdtemp(prefix="xorics-strreplace-ws-")
        self._save = (xorics.REPO_ROOT, xorics._SELFEDIT_WORKSPACE,
                      sandbox.run, sandbox.container_runtime)
        xorics.REPO_ROOT = self.repo
        xorics._SELFEDIT_WORKSPACE = self.ws
        sandbox.container_runtime = lambda: "podman"
        if self.run is not None:
            sandbox.run = self.run
        return self

    def live(self, rel):
        with open(os.path.join(self.repo, rel)) as f:
            return f.read()

    def staged(self, rel):
        with open(os.path.join(self.ws, "work", rel)) as f:
            return f.read()

    def __exit__(self, *a):
        (xorics.REPO_ROOT, xorics._SELFEDIT_WORKSPACE,
         sandbox.run, sandbox.container_runtime) = self._save
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(self.ws, ignore_errors=True)


# --- 1: unique snippet -> VERIFIED, rest preserved, live untouched -------------
calls = []
with _Patch(run=_fake_run_factory(calls=calls)) as p:
    out = xorics.str_replace(_REPO_FILE, "B = 2", "B = 22")
    check("unique replace -> WRITE VERIFIED", out.startswith("WRITE VERIFIED"))
    check("staged file has exactly the replacement", p.staged(_REPO_FILE) == "A = 1\nB = 22\nC = 3\n")
    check("untouched lines preserved byte-for-byte",
          p.staged(_REPO_FILE).startswith("A = 1\n") and p.staged(_REPO_FILE).endswith("C = 3\n"))
    check("live tree NOT mutated", p.live(_REPO_FILE) == _REPO_BODY)
    check("sandbox.run invoked once, fixed cmd, hermetic",
          len(calls) == 1 and calls[0]["cmd"] == "./run_tests.sh" and calls[0]["network"] is False)
    check("edited file required as artifact", calls and calls[0]["artifacts"] == [_REPO_FILE])

# --- 2: old_str not found -> ERROR, sandbox never called -----------------------
calls = []
with _Patch(run=_fake_run_factory(calls=calls)) as p:
    out = xorics.str_replace(_REPO_FILE, "Z = 9", "Z = 10")
    check("missing old_str -> ERROR", out.startswith("str_replace ERROR") and "not found" in out)
    check("missing old_str -> sandbox never called", len(calls) == 0)
    check("missing old_str -> live untouched", p.live(_REPO_FILE) == _REPO_BODY)

# --- 3: non-unique old_str -> ERROR --------------------------------------------
with _Patch(run=_fake_run_factory()) as p:
    with open(os.path.join(p.repo, _REPO_FILE), "w") as f:
        f.write("dup\ndup\n")                            # 'dup' now appears twice
    out = xorics.str_replace(_REPO_FILE, "dup", "x")
    check("non-unique old_str -> ERROR", out.startswith("str_replace ERROR") and "unique" in out)

# --- 4: empty old_str -> ERROR -------------------------------------------------
with _Patch(run=_fake_run_factory()) as p:
    out = xorics.str_replace(_REPO_FILE, "", "x")
    check("empty old_str -> ERROR", out.startswith("str_replace ERROR") and "empty" in out)

# --- 5: file does not exist -> ERROR, points to write_file ---------------------
with _Patch(run=_fake_run_factory()) as p:
    out = xorics.str_replace("does_not_exist.py", "a", "b")
    check("missing file -> ERROR pointing to write_file",
          out.startswith("str_replace ERROR") and "write_file" in out)

# --- 6: no-op replacement -> NO CHANGE, sandbox never called -------------------
calls = []
with _Patch(run=_fake_run_factory(calls=calls)) as p:
    out = xorics.str_replace(_REPO_FILE, "B = 2", "B = 2")
    check("no-op replace -> NO CHANGE", out.startswith("NO CHANGE"))
    check("no-op replace -> sandbox never called", len(calls) == 0)

# --- 7: absolute path refused (inherits _selfedit_resolve) ---------------------
with _Patch(run=_fake_run_factory()) as p:
    out = xorics.str_replace("/etc/passwd", "root", "x")
    check("absolute path refused", "RELATIVE to the repo root" in out)

# --- 8: str_replace wired into the self-edit toolset & impls --------------------
check("str_replace in SELF_EDIT_TOOLS",
      "str_replace" in [t["function"]["name"] for t in xorics.SELF_EDIT_TOOLS])
check("str_replace in TOOL_IMPLS", xorics.TOOL_IMPLS.get("str_replace") is xorics.str_replace)


print(f"\n{PASS}/{PASS + FAIL} checks passed")
sys.exit(0 if FAIL == 0 else 1)
