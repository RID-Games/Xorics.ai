#!/usr/bin/env python3
# Xorics — test: self-edit Brick C (review / promote / discard). Hermetic.
"""Run: python3 test_promote.py

Uses a REAL throwaway git repo (so `git add`/`commit` are genuinely exercised) but stubs
sandbox.run for the re-verify (test_sandbox + selfedit_probe prove the real container path;
the END-TO-END promote-into-a-real-repo proof is promote_probe.py on RIDGames). Pins the
gate: promotion re-verifies in the sandbox and only writes the live tree on a CURRENT green,
a red re-verify leaves live untouched, .git is never promoted, and `git add` is scoped to the
changed files (a pre-existing dirty file is NOT swept into the commit). XORICS-FEATURE: self-edit
"""
import os
import sys
import shutil
import subprocess
import tempfile

import sandbox
import xorics

PASS = FAIL = 0

# The sandbox image (python:3.12-slim) has no git, but write_file/promote run this whole
# suite INSIDE that container — so without this guard test_promote would fail in-container
# and break self-edit. The host (RIDGames) has git and runs it fully; the slim container
# skips it and the in-container suite stays green. (Bake git into the image to run it there
# too — optional.) XORICS-FEATURE: self-edit
if shutil.which("git") is None:
    print("  skip test_promote.py — git not on PATH (host runs it fully; slim sandbox skips it)\n0/0 passed")
    sys.exit(0)


def check(label, cond):
    global PASS, FAIL
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    PASS += bool(cond); FAIL += (not cond)


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


def _fake_run(rc=0, error=None):
    def _run(repo_dir, cmd, *, artifacts=None, image=None, network=False, **kw):
        _run.last = {"repo_dir": repo_dir, "cmd": cmd, "image": image, "network": network}
        return sandbox.SandboxResult(
            exit_code=(None if error else rc), stdout="...\nSuites: 19 passed, 0 failed\n",
            stderr="", artifacts={}, elapsed=1.0, image=image or "img",
            runtime="podman", error=error)
    return _run


class Repo:
    """A real temp git repo, with xorics pointed at it and the runtime stubbed."""
    def __init__(self, run=None, runtime="podman"):
        self.run = run
        self.runtime = runtime

    def __enter__(self):
        self.repo = tempfile.mkdtemp(prefix="xorics-promote-repo-")
        self.ws = tempfile.mkdtemp(prefix="xorics-promote-ws-")
        shutil.rmtree(self.ws)               # let stage create it
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "commit.gpgsign", "false")
        self.write("notebook.py", "LIVE = 1\n")
        git(self.repo, "add", "notebook.py")
        git(self.repo, "commit", "-q", "-m", "baseline")
        self._save = (xorics.REPO_ROOT, xorics._SELFEDIT_WORKSPACE, xorics._SELFEDIT_TASK_FILE,
                      sandbox.run, sandbox.container_runtime)
        xorics.REPO_ROOT = self.repo
        xorics._SELFEDIT_WORKSPACE = self.ws
        xorics._SELFEDIT_TASK_FILE = os.path.join(self.ws, "TASK.txt")
        sandbox.container_runtime = lambda: self.runtime
        if self.run is not None:
            sandbox.run = self.run
        return self

    def write(self, rel, content):
        with open(os.path.join(self.repo, rel), "w") as f:
            f.write(content)

    def live(self, rel):
        p = os.path.join(self.repo, rel)
        return open(p).read() if os.path.exists(p) else None

    def stage_edit(self, rel, content, task="rename a var"):
        """Mimic a WRITE VERIFIED: stage a copy of the live repo, apply an edit in the copy,
        and record the task — exactly the on-disk state write_file leaves behind."""
        os.makedirs(self.ws, exist_ok=True)
        open(xorics._SELFEDIT_TASK_FILE, "w").write(task)
        work = xorics._selfedit_stage()
        with open(os.path.join(work, rel), "w") as f:
            f.write(content)

    def head(self):
        return git(self.repo, "rev-parse", "--short", "HEAD").stdout.strip()

    def log_subjects(self):
        return git(self.repo, "log", "--format=%s").stdout.split("\n")

    def __exit__(self, *a):
        (xorics.REPO_ROOT, xorics._SELFEDIT_WORKSPACE, xorics._SELFEDIT_TASK_FILE,
         sandbox.run, sandbox.container_runtime) = self._save
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(self.ws, ignore_errors=True)


# --- 1: changed-files detection skips .git even when live's git has moved -------
with Repo() as r:
    r.stage_edit("notebook.py", "LIVE = 2\n")
    git(r.repo, "commit", "-q", "--allow-empty", "-m", "advance HEAD (stale workspace .git)")
    rels = xorics._selfedit_changed_files()
    check("changed-files = exactly the edited file", rels == ["notebook.py"])
    check("changed-files NEVER includes .git internals", not any(x.startswith(".git") for x in rels))

# --- 2: review surfaces the change + the task ----------------------------------
with Repo() as r:
    r.stage_edit("notebook.py", "LIVE = 2\n", task="bump notebook constant")
    rels, diff, task = xorics.review_self_edit()
    check("review lists the changed file", rels == ["notebook.py"])
    check("review diff shows the new content", "LIVE = 2" in diff and "LIVE = 1" in diff)
    check("review carries the task text", task == "bump notebook constant")

# --- 3: promote (re-verify GREEN) applies to live + commits, scoped -------------
fr = _fake_run(rc=0)
with Repo(run=fr) as r:
    r.write("unrelated.txt", "dirty pre-existing\n")     # must NOT be swept into the commit
    h0 = r.head()
    r.stage_edit("notebook.py", "LIVE = 2\n", task="bump notebook constant")
    out = xorics.promote_self_edit()
    check("green re-verify -> PROMOTED", out.startswith("PROMOTED"))
    check("live file now holds the verified content", r.live("notebook.py") == "LIVE = 2\n")
    check("a new commit landed", r.head() != h0)
    check("commit message carries the task", r.log_subjects()[0] == "xorics self-edit: bump notebook constant")
    show = git(r.repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    check("commit contains ONLY the changed file (git add scoped, not -A)", show == ["notebook.py"])
    check("pre-existing dirty file left untracked", r.live("unrelated.txt") == "dirty pre-existing\n"
          and "unrelated.txt" not in show)
    check("re-verify used the FIXED verify cmd", fr.last["cmd"] == "./run_tests.sh")
    check("re-verify ran against the workspace copy", fr.last["repo_dir"].endswith("work"))
    check("workspace cleared after promote", xorics._selfedit_changed_files() == [])

# --- 4: promote with RED re-verify aborts, live untouched, no commit ------------
with Repo(run=_fake_run(rc=1)) as r:
    h0 = r.head()
    r.stage_edit("notebook.py", "LIVE = 9\n")
    out = xorics.promote_self_edit()
    check("red re-verify -> PROMOTE ABORTED", out.startswith("PROMOTE ABORTED"))
    check("live file UNCHANGED after aborted promote", r.live("notebook.py") == "LIVE = 1\n")
    check("no new commit on abort", r.head() == h0)

# --- 5: nothing pending -> graceful, no write/commit ---------------------------
with Repo(run=_fake_run(rc=0)) as r:
    h0 = r.head()
    out = xorics.promote_self_edit()
    check("nothing pending -> PROMOTE: nothing to promote", out.startswith("PROMOTE: nothing"))
    check("nothing pending: no commit", r.head() == h0)

# --- 6: no runtime -> refuses to touch live ------------------------------------
with Repo(run=_fake_run(rc=0), runtime=None) as r:
    h0 = r.head()
    r.stage_edit("notebook.py", "LIVE = 2\n")
    out = xorics.promote_self_edit()
    check("no runtime -> PROMOTE ERROR (won't write live unverified)", out.startswith("PROMOTE ERROR"))
    check("no runtime: live unchanged", r.live("notebook.py") == "LIVE = 1\n")
    check("no runtime: no commit", r.head() == h0)

# --- 7: discard clears the pending change, live untouched ----------------------
with Repo() as r:
    h0 = r.head()
    r.stage_edit("notebook.py", "LIVE = 2\n")
    out = xorics.discard_self_edit()
    check("discard -> DISCARDED", out.startswith("DISCARDED"))
    check("discard left live untouched", r.live("notebook.py") == "LIVE = 1\n")
    check("discard cleared the pending change", xorics._selfedit_changed_files() == [])
    check("discard with nothing pending is graceful", xorics.discard_self_edit().startswith("Nothing"))
    check("discard: no commit", r.head() == h0)

# --- 8: promotion is NOT a model tool (the coder/manager can't self-promote) ----
tool_names = {t["function"]["name"] for t in xorics.TOOLS}
check("no promote tool in TOOLS", not any("promote" in n for n in tool_names))
check("no promote impl is dispatchable", not any("promote" in n for n in xorics.TOOL_IMPLS))

print(f"\n{PASS}/{PASS + FAIL} passed")
sys.exit(0 if FAIL == 0 else 1)
