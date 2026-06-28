#!/usr/bin/env python3
# Xorics — test: sandbox runner (throwaway-container execution). Hermetic.
"""Run: python3 test_sandbox.py
Stubs the container runtime (subprocess.run) so it needs NO podman/docker. The
LIVE proof — running ./run_tests.sh inside a real container — is a separate step
on RIDGames (green mocks lie; this only proves the wiring)."""

import os
import sys
import shutil
import tempfile
import types
import subprocess as _sp

import sandbox

PASS, FAIL = 0, 0


def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")


def _mk_repo():
    """A throwaway stand-in repo with a sentinel + a venv that must be ignored."""
    d = tempfile.mkdtemp(prefix="xorics-sbx-repo-")
    with open(os.path.join(d, "sentinel.txt"), "w") as f:
        f.write("original\n")
    os.makedirs(os.path.join(d, "venv", "bin"))
    with open(os.path.join(d, "venv", "bin", "python"), "w") as f:
        f.write("x")
    return d


def _fake_run(rc=0, writes=()):
    """Stand-in for subprocess.run that simulates the container: it writes
    `writes` (relpaths) into the bind-mount source, then returns exit code rc."""
    captured = {}

    def _run(args, capture_output=True, text=True, timeout=None):
        captured["args"] = args
        src = None
        for i, a in enumerate(args):
            if a == "-v" and i + 1 < len(args):
                src = args[i + 1].split(":")[0]
        for rel in writes:
            sub = os.path.dirname(rel)
            if sub:
                os.makedirs(os.path.join(src, sub), exist_ok=True)
            with open(os.path.join(src, rel), "w") as f:
                f.write("artifact\n")
        return types.SimpleNamespace(returncode=rc, stdout="out", stderr="err")

    _run.captured = captured
    return _run


# ---- 1) ok-semantics matrix (pure) -------------------------------------------
r_ok = sandbox.SandboxResult(0, "", "", {"a": True}, 0.0, "img")
r_badcode = sandbox.SandboxResult(1, "", "", {"a": True}, 0.0, "img")
r_nomiss = sandbox.SandboxResult(0, "", "", {"a": False}, 0.0, "img")
r_err = sandbox.SandboxResult(None, "", "", {}, 0.0, "img", error="boom")
check("ok: exit 0 + artifact present -> ok", r_ok.ok is True)
check("ok: exit 1 -> not ok", r_badcode.ok is False)
check("ok: exit 0 but missing artifact -> not ok (exit 0 is not enough)", r_nomiss.ok is False)
check("ok: plumbing error -> not ok", r_err.ok is False)
check("summary names the missing artifact", "missing" in r_nomiss.summary())

# ---- 2) run(): exit 0 + artifact written -> ok, and live repo untouched ------
repo = _mk_repo()
before = sorted(os.listdir(repo))
fr = _fake_run(rc=0, writes=("out.bin",))
sandbox.subprocess.run = fr
res = sandbox.run(repo, "./run_tests.sh", artifacts=["out.bin"], runtime="podman")
check("run: exit_code propagated (0)", res.exit_code == 0)
check("run: required artifact detected present", res.artifacts.get("out.bin") is True)
check("run: result ok", res.ok is True)
check("run: stdout/stderr captured", res.stdout == "out" and res.stderr == "err")
check("run: LIVE repo never mutated (listing unchanged)", sorted(os.listdir(repo)) == before)
check("run: artifact did NOT land in the live repo", not os.path.exists(os.path.join(repo, "out.bin")))

# ---- 3) command assembly -----------------------------------------------------
args = fr.captured["args"]
check("cmd: throwaway --rm present", "--rm" in args)
check("cmd: hermetic --network=none by default", "--network=none" in args)
check("cmd: str command wrapped in /bin/sh -lc", "/bin/sh" in args and "-lc" in args)

# ---- 4) exit 0 but artifact NOT written -> not ok (the honesty point) --------
sandbox.subprocess.run = _fake_run(rc=0, writes=())
res2 = sandbox.run(repo, "true", artifacts=["out.bin"], runtime="podman")
check("run: exit 0 with no artifact -> not ok", res2.ok is False and res2.exit_code == 0)

# ---- 5) failing command -> not ok --------------------------------------------
sandbox.subprocess.run = _fake_run(rc=1, writes=())
res3 = sandbox.run(repo, "false", runtime="podman")
check("run: nonzero exit -> not ok", res3.ok is False and res3.exit_code == 1)

# ---- 6) network=True opens the network ---------------------------------------
fr4 = _fake_run(rc=0, writes=())
sandbox.subprocess.run = fr4
sandbox.run(repo, "true", runtime="podman", network=True)
check("cmd: network=True drops --network=none", "--network=none" not in fr4.captured["args"])

# ---- 7) no runtime -> clean error result, not a crash ------------------------
orig_rt = sandbox.container_runtime
sandbox.container_runtime = lambda: None
res5 = sandbox.run(repo, "true", artifacts=["x"])
sandbox.container_runtime = orig_rt
check("run: missing runtime -> error result (no crash)", res5.error is not None and res5.exit_code is None)
check("run: missing runtime -> ok is False", res5.ok is False)

# ---- 8) timeout -> error set, exit_code None ---------------------------------
def _timeout_run(args, **kw):
    raise _sp.TimeoutExpired(cmd=args, timeout=1)
sandbox.subprocess.run = _timeout_run
res6 = sandbox.run(repo, "sleep 9999", runtime="podman", timeout=1)
check("run: timeout -> error set, exit_code None", res6.error is not None and res6.exit_code is None)

shutil.rmtree(repo, ignore_errors=True)
print(f"\n{PASS}/{PASS + FAIL} checks passed")
sys.exit(0 if FAIL == 0 else 1)
