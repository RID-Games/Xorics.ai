#!/usr/bin/env python3
# Xorics — throwaway-container code execution (the honesty gate, generalized).
"""sandbox.py — run a command in a disposable container against a COPY of the tree.

The honesty gate, one step broader. finalize_design lets a board count as done
only if it BUILT and the file exists on disk; this enforces the same shape for
arbitrary code: a run is `ok` only if it actually RAN, exited 0, AND every
required artifact exists afterward — exit 0 alone never counts. It is the
substrate the self-edit cycle stands on: a proposed change is applied to a COPY
of the working tree, the suite runs inside a --rm container, and the LIVE repo
is never mutated. Generic on purpose — the same run() drives ./run_tests.sh
today and a Gradle build later.

XORICS-FEATURE: sandbox
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass

DEFAULT_IMAGE = os.environ.get("XORICS_SANDBOX_IMAGE", "docker.io/library/python:3.12-slim")
DEFAULT_TIMEOUT = int(os.environ.get("XORICS_SANDBOX_TIMEOUT", "600"))
# Optional userns flag for the runtime (e.g. "keep-id" on some rootless setups).
# Empty by default: rootless podman maps container-root -> the host user already,
# so the simplest invocation usually leaves artifacts host-owned and removable.
# Set XORICS_SANDBOX_USERNS=keep-id if a live run shows root-owned leftovers.
_USERNS = os.environ.get("XORICS_SANDBOX_USERNS", "").strip()
# Host-specific / huge / useless-in-a-fresh-image paths. A host venv is NOT
# portable into another image; drop it and let in-container python3 run the
# hermetic suites (run_tests.sh already falls back venv -> python3). .git is
# dropped for speed — the unit suites don't need it (promotion/approval, which
# does, is a later brick and passes its own `ignore`).
_DEFAULT_IGNORE = ("__pycache__", "venv", "skidl-venv", ".venv", ".git",
                   ".mypy_cache", ".pytest_cache", "node_modules", "*.pyc")


@dataclass
class SandboxResult:
    exit_code: int | None       # None when it never ran (no runtime / timeout / copy error)
    stdout: str
    stderr: str
    artifacts: dict             # relpath -> bool (existed in the tree after the run)
    elapsed: float
    image: str
    runtime: str | None = None
    error: str | None = None    # plumbing failure (no runtime, timeout, copy/pull error)

    @property
    def ok(self) -> bool:
        """Green ONLY if it ran, exited 0, and every required artifact exists.
        This is the generalized honesty gate — exit 0 by itself is never enough."""
        return (self.error is None and self.exit_code == 0
                and all(self.artifacts.values()))

    def summary(self) -> str:
        if self.error:
            return f"sandbox ERROR: {self.error} ({self.elapsed:.1f}s)"
        miss = [a for a, present in self.artifacts.items() if not present]
        tail = "" if not miss else f"  missing artifacts: {', '.join(miss)}"
        return (f"sandbox {'OK' if self.ok else 'FAIL'} "
                f"exit={self.exit_code} ({self.elapsed:.1f}s){tail}")


def container_runtime() -> str | None:
    """Return the container runtime to use ('podman', else 'docker'), or None."""
    for rt in ("podman", "docker"):
        if shutil.which(rt):
            return rt
    return None


# Readable alias used elsewhere / in probes.
def podman_available() -> str | None:
    return container_runtime()


def _err(artifacts, elapsed, image, runtime, msg) -> SandboxResult:
    return SandboxResult(None, "", "", {a: False for a in artifacts}, elapsed,
                         image, runtime=runtime, error=msg)


def run(repo_dir, cmd, *, artifacts=None, image=DEFAULT_IMAGE,
        timeout=DEFAULT_TIMEOUT, network=False, ignore=_DEFAULT_IGNORE,
        runtime=None, extra_run_args=None) -> SandboxResult:
    """Run `cmd` in a throwaway container against a COPY of `repo_dir`.

    repo_dir : tree copied into the container at /work (the working tree, maybe
               with a proposed edit already applied by the caller). NEVER mutated.
    cmd      : str (run via /bin/sh -lc) or list[str]; CWD is /work.
    artifacts: relpaths that MUST exist after the run for ok=True.
    network  : False -> --network=none (hermetic default). True for runs that
               must fetch deps (e.g. a future Gradle build).
    """
    t0 = time.time()
    artifacts = list(artifacts or [])
    rt = runtime or container_runtime()
    if rt is None:
        return _err(artifacts, time.time() - t0, image, None,
                    "no container runtime found (need podman or docker on PATH)")

    repo_dir = os.path.abspath(os.path.expanduser(str(repo_dir)))
    if not os.path.isdir(repo_dir):
        return _err(artifacts, time.time() - t0, image, rt,
                    f"repo_dir is not a directory: {repo_dir}")

    shell_cmd = ["/bin/sh", "-lc", cmd] if isinstance(cmd, str) else list(cmd)
    name = f"xorics-sbx-{uuid.uuid4().hex[:12]}"
    work = tempfile.mkdtemp(prefix="xorics-sbx-")
    tree = os.path.join(work, "work")
    try:
        try:
            shutil.copytree(repo_dir, tree,
                            ignore=shutil.ignore_patterns(*ignore),
                            symlinks=True)
        except Exception as e:
            return _err(artifacts, time.time() - t0, image, rt, f"copy failed: {e}")

        run_args = [rt, "run", "--rm", "--name", name,
                    "-v", f"{tree}:/work", "-w", "/work"]
        if not network:
            run_args.append("--network=none")
        if _USERNS:
            run_args.append(f"--userns={_USERNS}")
        run_args += list(extra_run_args or [])
        run_args.append(image)
        run_args += shell_cmd

        exit_code, out, err, error = None, "", "", None
        try:
            proc = subprocess.run(run_args, capture_output=True, text=True,
                                  timeout=timeout)
            exit_code, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as e:
            out = (e.stdout.decode() if isinstance(e.stdout, bytes) else e.stdout) or ""
            err = (e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr) or ""
            error = f"timed out after {timeout}s"
            try:  # the client SIGKILL may orphan a container; best-effort reap
                subprocess.run([rt, "rm", "-f", name], capture_output=True,
                               text=True, timeout=30)
            except Exception:
                pass
        except FileNotFoundError:
            error = f"runtime '{rt}' not found on PATH"
        except Exception as e:
            error = f"runtime error: {e}"

        present = {a: os.path.exists(os.path.join(tree, a)) for a in artifacts}
        return SandboxResult(exit_code, out, err, present, time.time() - t0,
                             image, runtime=rt, error=error)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":  # tiny manual smoke (needs a real runtime)
    import json
    import sys
    repo = sys.argv[1] if len(sys.argv) > 1 else "."
    command = sys.argv[2] if len(sys.argv) > 2 else "python3 -c 'print(1+1)'"
    r = run(repo, command)
    print(r.summary())
    print(json.dumps({"exit_code": r.exit_code, "ok": r.ok,
                      "error": r.error, "runtime": r.runtime}, indent=2))
    print("--- stdout (tail) ---\n" + r.stdout[-2000:])
    if r.stderr.strip():
        print("--- stderr (tail) ---\n" + r.stderr[-2000:])
    sys.exit(0 if r.ok else 1)
