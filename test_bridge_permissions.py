# Xorics — tests for the three /v1/permissions routes in bridge.py.
# Mirrors test_api.py's house pattern: _pass/_fail counters with a check(label, cond) helper,
# a try/finally, and the footer that prints "{_pass} passed, {_fail} failed" then raises
# SystemExit(1 if _fail else 0) so a failing test actually fails the runner.
#
# One import-order quirk worth pinning down: bridge.py reads XORICS_BRIDGE_TOKEN into a
# module-level _TOKEN at IMPORT time (see the line `_TOKEN = os.environ.get(...)` near the
# top of bridge.py). If the developer's shell happens to have that env var set when this
# test runs, _TOKEN would be a string and every request below would 401 in _auth before it
# ever reached a permission handler. So we pop the env var BEFORE importing bridge — once
# bridge.py is loaded, _TOKEN stays None for the rest of the process and _auth is a no-op.
#
# We also snapshot xorics._TOOL_GRANTS (the shared module-level set) at the start and
# restore it in the finally. The live bridge, a follow-up test, or a real xorics.ask()
# call could all be reading this set; we must not leave str_replace granted because of
# this test. clear() then update(old) preserves identity for the module reference.

import os
os.environ.pop("XORICS_BRIDGE_TOKEN", None)

from fastapi.testclient import TestClient
import bridge
import xorics

c = TestClient(bridge.app)

_pass = _fail = 0
def check(label, cond):
    global _pass, _fail
    if cond: _pass += 1; print(f"  ok   {label}")
    else:    _fail += 1; print(f"  FAIL {label}")

# Snapshot the live grant set so the finally can put it back exactly as it was.
old = set(xorics._TOOL_GRANTS)

try:
    # Start clean: nothing granted. bridge's GET returns sorted copies of the sets.
    xorics._TOOL_GRANTS.clear()

    # ---- GET /v1/permissions ----------------------------------------------
    r = c.get("/v1/permissions")
    check("GET /v1/permissions -> 200", r.status_code == 200)
    body = r.json()
    check("GET: privileged == ['android_deploy', 'str_replace', 'write_file']",
          body["privileged"] == ["android_deploy", "str_replace", "write_file"])
    check("GET: granted == [] (fresh start)", body["granted"] == [])

    # ---- POST /v1/permissions/grant ---------------------------------------
    rg = c.post("/v1/permissions/grant", json={"tool": "str_replace"})
    check("POST grant {\"tool\":\"str_replace\"} -> 200", rg.status_code == 200)
    check("grant response: granted contains 'str_replace'",
          "str_replace" in rg.json()["granted"])

    # A second GET must still show it granted — state lives in xorics._TOOL_GRANTS.
    r2 = c.get("/v1/permissions")
    check("GET after grant still shows str_replace granted",
          "str_replace" in r2.json()["granted"])

    # read_file is NOT in xorics._PRIVILEGED_TOOLS, so _grant_tool returns False -> 400.
    check("POST grant {\"tool\":\"read_file\"} (not privileged) -> 400",
          c.post("/v1/permissions/grant", json={"tool": "read_file"}).status_code == 400)

    # Non-string tool: the route's isinstance(name, str) guard short-circuits to 400.
    check("POST grant {\"tool\": 5} (non-string) -> 400",
          c.post("/v1/permissions/grant", json={"tool": 5}).status_code == 400)

    # ---- POST /v1/permissions/revoke --------------------------------------
    rr = c.post("/v1/permissions/revoke", json={"tool": "str_replace"})
    check("POST revoke {\"tool\":\"str_replace\"} -> 200", rr.status_code == 200)
    check("revoke response: granted == []", rr.json()["granted"] == [])

    # Idempotent: _is_privileged('str_replace') is still True, so the route accepts
    # a second revoke and returns 200 again (no special-case for "already revoked").
    check("POST revoke str_replace AGAIN -> 200 (idempotent)",
          c.post("/v1/permissions/revoke", json={"tool": "str_replace"}).status_code == 200)

    # Revoking a non-privileged tool -> 400 (same _is_privileged gate as grant).
    check("POST revoke {\"tool\":\"read_file\"} (not privileged) -> 400",
          c.post("/v1/permissions/revoke", json={"tool": "read_file"}).status_code == 400)

finally:
    # Restore the live grant set to exactly what it was before this test ran.
    xorics._TOOL_GRANTS.clear()
    xorics._TOOL_GRANTS.update(old)

print(f"\n{_pass} passed, {_fail} failed")
raise SystemExit(1 if _fail else 0)