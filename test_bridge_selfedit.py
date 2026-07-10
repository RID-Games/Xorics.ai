# Xorics — tests for the three /v1/selfedit routes in bridge.py (APP-B3).
# Mirrors test_bridge_permissions.py's house pattern: pop XORICS_BRIDGE_TOKEN before
# importing bridge (its module-level _TOKEN is read at import time), _pass/_fail
# counters with a check(label, cond) helper, a try/finally, and the footer that
# prints "{_pass} passed, {_fail} failed" then raises SystemExit(1 if _fail else 0).
#
# Two safety rails specific to THIS suite:
#   1. XORICS_SELFEDIT_WORKSPACE is pointed at a throwaway tmpdir BEFORE xorics is
#      imported (xorics reads it into _SELFEDIT_WORKSPACE at import time), so this
#      test can never see or touch the real ~/.cache/xorics/selfedit workspace.
#   2. sandbox.container_runtime is stubbed to return None around the promote call.
#      promote_self_edit checks it BEFORE anything else touches the live tree, so
#      the stub makes the refusal path deterministic — on the HOST (where podman is
#      real) an un-stubbed promote would actually re-verify, copy files into the
#      live tree and git commit, which a unit test must never risk. The finally
#      puts the real function back. Actual promotion is proven live, on hardware.

import os
import shutil
import tempfile

os.environ.pop("XORICS_BRIDGE_TOKEN", None)
_TMP = tempfile.mkdtemp(prefix="xorics-selfedit-test-")
os.environ["XORICS_SELFEDIT_WORKSPACE"] = _TMP

from fastapi.testclient import TestClient
import bridge
import sandbox
import xorics

c = TestClient(bridge.app)

_pass = _fail = 0
def check(label, cond):
    global _pass, _fail
    if cond: _pass += 1; print(f"  ok   {label}")
    else:    _fail += 1; print(f"  FAIL {label}")

_real_runtime = sandbox.container_runtime
PROBE = "APP-B3-TEST-PROBE.txt"              # never exists in the live tree

try:
    # ---- GET /v1/selfedit with nothing staged --------------------------------
    r = c.get("/v1/selfedit")
    check("GET empty: 200", r.status_code == 200)
    body = r.json()
    check("GET empty: no files", body.get("files") == [])
    check("GET empty: no diff", body.get("diff") == "")
    check("GET empty: no task", body.get("task") == "")

    # ---- stage a fake pending edit straight into the throwaway workspace -----
    work = os.path.join(_TMP, "work")
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, PROBE), "w") as f:
        f.write("selfedit route test\n")
    with open(os.path.join(_TMP, "TASK.txt"), "w") as f:
        f.write("test task text")

    r = c.get("/v1/selfedit")
    body = r.json()
    check("GET staged: file listed", body.get("files") == [PROBE])
    check("GET staged: diff names the file", PROBE in body.get("diff", ""))
    check("GET staged: task round-trips", body.get("task") == "test task text")

    # ---- POST promote, container runtime stubbed away ------------------------
    sandbox.container_runtime = lambda: None
    r = c.post("/v1/selfedit/promote", json={"push": False})
    check("POST promote: 200", r.status_code == 200)
    body = r.json()
    st = body.get("status", "")
    check("POST promote: refusal surfaced", "PROMOTE ERROR" in st and "container runtime" in st)
    check("POST promote: edit still pending", body.get("files") == [PROBE])
    check("POST promote: live tree untouched",
          not os.path.exists(os.path.join(xorics.REPO_ROOT, PROBE)))
    sandbox.container_runtime = _real_runtime

    # ---- POST discard ---------------------------------------------------------
    r = c.post("/v1/selfedit/discard", json={})
    check("POST discard: 200", r.status_code == 200)
    body = r.json()
    check("POST discard: reports DISCARDED", "DISCARDED" in body.get("status", ""))
    check("POST discard: nothing pending", body.get("files") == [])
    check("POST discard: workspace gone", not os.path.isdir(work))

    # ---- discard again: idempotent --------------------------------------------
    r = c.post("/v1/selfedit/discard", json={})
    check("POST discard twice: nothing-pending message",
          "Nothing pending" in r.json().get("status", ""))

    # ---- stage a BINARY file (NUL bytes): review must never crash -------------
    # The current request re-creates the throwaway workspace from the discard above,
    # so we recreate `work/` here too.
    os.makedirs(work, exist_ok=True)
    bin_name = "BINARY-PROBE.bin"
    with open(os.path.join(work, bin_name), "wb") as f:
        f.write(b"\x00\x01\x02NUL-MID\x00\xff\xfe")
    try:
        r = c.get("/v1/selfedit")
        body = r.json() if r.status_code == 200 else {}
        # Either filtered out, or rendered as the safe placeholder — never a 500,
        # never an unhandled traceback. Binary bytes must NOT leak into the diff.
        diff_text = body.get("diff", "")
        files_text = ",".join(body.get("files") or [])
        check("GET binary: 200 (no crash)",     r.status_code == 200)
        check("GET binary: filtered or marked",
              bin_name not in files_text or "binary" in diff_text.lower())
        check("GET binary: no raw NUL in diff",  b"\x00" not in diff_text.encode("utf-8", "replace"))
    finally:
        # Always clean the binary probe so the test is hermetic on re-runs.
        try: os.remove(os.path.join(work, bin_name))
        except OSError: pass

finally:
    sandbox.container_runtime = _real_runtime
    shutil.rmtree(_TMP, ignore_errors=True)

# ---- _selfedit_verify_cmd: android/ branch adds an offline Kotlin compile ------
# The verify stays code-chosen, never model-chosen. The python suite can't exercise
# Kotlin, so when the changed set touches android/ the sandbox also compiles it
# offline (deps baked into the image). We monkey-patch _selfedit_changed_files (the
# only input the helper reads) so we don't have to stage real files.
_real_changed_files = xorics._selfedit_changed_files
try:
    xorics._selfedit_changed_files = lambda: ["android/app/x.kt"]
    cmd_android = xorics._selfedit_verify_cmd()
    check("verify_cmd: android/ adds compileDebugKotlin",
          "compileDebugKotlin" in cmd_android)
    check("verify_cmd: android/ keeps the python suite first",
          cmd_android.startswith(xorics._SELFEDIT_VERIFY_CMD))

    xorics._selfedit_changed_files = lambda: ["xorics.py"]
    cmd_py = xorics._selfedit_verify_cmd()
    check("verify_cmd: non-android stays at the python suite",
          cmd_py == xorics._SELFEDIT_VERIFY_CMD)
    check("verify_cmd: non-android does NOT add compileDebugKotlin",
          "compileDebugKotlin" not in cmd_py)
finally:
    xorics._selfedit_changed_files = _real_changed_files

# ---- _SELFEDIT_STAGE_IGNORE contents -----------------------------------------
# android Kotlin sources must stage into the workspace, but build outputs, gradle
# caches, and binary archives stay excluded; android.bak-* is still ignored.
_ignore = xorics._SELFEDIT_STAGE_IGNORE
check("ignore: 'android' is no longer excluded",
      "android" not in _ignore)
check("ignore: 'android.bak-*' is still excluded",
      "android.bak-*" in _ignore)
check("ignore: 'build' is now excluded",
      "build" in _ignore)
check("ignore: '.gradle' is now excluded",
      ".gradle" in _ignore)
check("ignore: '*.apk' is now excluded",
      "*.apk" in _ignore)
check("ignore: '*.jar' is now excluded",
      "*.jar" in _ignore)

print(f"\n{_pass} passed, {_fail} failed")
raise SystemExit(1 if _fail else 0)
