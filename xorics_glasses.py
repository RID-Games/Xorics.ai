#!/usr/bin/env python3
# Xorics — plugin-facing helper for the G2 glasses bus.
# Copyright (C) 2026 Zawayix
#
# This file is part of Xorics, free software under the GNU AGPL v3 or later.
# See <https://www.gnu.org/licenses/>. Designs produced by RUNNING Xorics are
# exempt per LICENSE-EXCEPTION.

"""
The one import a Xorics plugin needs to use the glasses. Stdlib only (urllib),
zero new dependencies:

    import xorics_glasses as glasses
    cmd_id = glasses.display("Hello from Xorics")   # text on the lens
    glasses.wait_ack(cmd_id)                        # {'ok': True, 'detail': 'playing', ...}
    glasses.status()                                # phone polling? queue depth?
    glasses.events(after=0)                         # acks + raw BLE frames (M1 evidence)

Also a CLI (this doubles as the Milestone-2 one-liner on RIDGames):
    python3 xorics_glasses.py "Hello from Xorics"

Env:
    XORICS_BRIDGE_URL    default http://127.0.0.1:8090 (plugins run on the bridge box)
    XORICS_BRIDGE_TOKEN  bearer; any value accepted when the server has none set
"""

import json
import os
import time
import urllib.request

BASE = os.environ.get("XORICS_BRIDGE_URL", "http://127.0.0.1:8090").rstrip("/")
TOKEN = os.environ.get("XORICS_BRIDGE_TOKEN", "xorics-plugin")


def _req(method, path, body=None, timeout=10.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status == 204:
            return None
        return json.loads(r.read().decode("utf-8") or "{}")


def command(payload):
    """Enqueue a raw command dict; returns its id."""
    return _req("POST", "/glasses/command", payload)["id"]


def display(text):
    """Put text on the lens via the proven teleprompter script. Returns cmd id."""
    return command({"type": "display_text", "text": text})


def selftest():
    """Run the on-device protocol + display oracles. Returns cmd id."""
    return command({"type": "selftest"})


def ping():
    return command({"type": "ping"})


def status():
    return _req("GET", "/glasses/status")


def events(after=0, limit=50):
    return _req("GET", "/glasses/events?after=%d&limit=%d" % (after, limit))


def wait_ack(cmd_id, timeout=20.0, poll=0.5):
    """Block until the phone acks cmd_id (or timeout). Returns the ack event or None."""
    deadline = time.time() + timeout
    seen = 0
    while time.time() < deadline:
        batch = events(after=seen) or {}
        for e in batch.get("events", []):
            seen = max(seen, e.get("id", 0))
            if e.get("type") == "ack" and e.get("cmd_id") == cmd_id:
                return e
        time.sleep(poll)
    return None


if __name__ == "__main__":
    import sys
    text = " ".join(sys.argv[1:]) or "Hello from Xorics!\nBase system online."
    st = status()
    print("bus status:", st)
    if not st.get("phone_polling"):
        print("WARNING: phone is not polling — start the glasses service in the Xorics G2 app")
    cid = display(text)
    print("queued display_text as command #%d; waiting for phone ack..." % cid)
    ack = wait_ack(cid)
    print("ack:", ack if ack else "TIMEOUT — check the app notification / logcat -s XoricsG2")
