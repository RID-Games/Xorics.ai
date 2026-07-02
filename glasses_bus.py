#!/usr/bin/env python3
# Xorics — G2 glasses command/event bus (bridge side of the base system).
# Copyright (C) 2026 Zawayix
#
# This file is part of Xorics, free software under the GNU AGPL v3 or later.
# See <https://www.gnu.org/licenses/>. Designs produced by RUNNING Xorics are
# exempt per LICENSE-EXCEPTION.

"""
The seam between Xorics plugins (Python, on RIDGames) and the G2 glasses
(reached through the RID Android app's raw BLE driver).

Topology:
    plugin / curl ──POST /glasses/command──▶ [queue] ──GET /glasses/poll──▶ phone app
    phone app ──POST /glasses/event──▶ [ring] ──GET /glasses/events──▶ plugin / curl

This is ADDITIVE, same pattern as api.py: a router factory taking the injected
`auth` callable, mounted in bridge.py with one include_router line. The OpenAI
route and everything else are untouched.

Commands v0 (the phone's GlassesBridgeClient dispatches these):
    {"type": "display_text", "text": "..."}   -> text on the lens (proven script)
    {"type": "selftest"}                       -> runtime protocol+text oracles on-device
    {"type": "ping"}                           -> liveness + session state

Events are whatever the phone reports: command acks {"cmd_id", "ok", "detail"},
BLE frames {"type": "frame", "hex", "service"} (Milestone-1 evidence lands here,
curl-able from RIDGames — no adb needed), and lifecycle notes.

Plugin-side helper: xorics_glasses.py (stdlib-only). Quick manual check:
    curl -s -H 'Authorization: Bearer x' -X POST http://127.0.0.1:8090/glasses/command \
         -d '{"type":"display_text","text":"Hello from Xorics"}'
    curl -s -H 'Authorization: Bearer x' 'http://127.0.0.1:8090/glasses/events?after=0'

State is in-process (single uvicorn worker, which is how bridge.py runs). A
restart drops queued commands and past events — fine for a control bus.
"""

import asyncio
import time
from collections import deque

from fastapi import APIRouter, Request, Response


async def _json(request):
    """Body as dict; {} on empty/invalid so a bare POST never 500s (api.py style)."""
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def make_glasses_router(auth):
    router = APIRouter(prefix="/glasses")

    commands: asyncio.Queue = asyncio.Queue()
    events: deque = deque(maxlen=200)
    counters = {"cmd": 0, "event": 0}
    last_poll = {"ts": 0.0}

    @router.post("/command")
    async def post_command(request: Request):
        auth(request)
        body = await _json(request)
        if not body.get("type"):
            return Response(status_code=400, content='{"error":"missing type"}',
                            media_type="application/json")
        counters["cmd"] += 1
        body["id"] = counters["cmd"]
        body["ts"] = time.time()
        commands.put_nowait(body)
        return {"id": body["id"], "queued": commands.qsize()}

    @router.get("/poll")
    async def poll(request: Request, wait: float = 25.0):
        """Phone long-poll. 200 + {"command": ...} or 204 after `wait` seconds."""
        auth(request)
        last_poll["ts"] = time.time()
        try:
            cmd = await asyncio.wait_for(commands.get(), timeout=min(max(wait, 0.0), 55.0))
        except asyncio.TimeoutError:
            return Response(status_code=204)
        return {"command": cmd}

    @router.post("/event")
    async def post_event(request: Request):
        auth(request)
        body = await _json(request)
        counters["event"] += 1
        body["id"] = counters["event"]
        body["ts"] = time.time()
        events.append(body)
        return {"ok": True, "id": body["id"]}

    @router.get("/events")
    async def get_events(request: Request, after: int = 0, limit: int = 50):
        """Drain for plugins/curl: events with id > after, oldest first."""
        auth(request)
        out = [e for e in events if e.get("id", 0) > after][: max(1, min(limit, 200))]
        last = out[-1]["id"] if out else after
        return {"events": out, "last": last, "total_seen": counters["event"]}

    @router.get("/status")
    async def status(request: Request):
        auth(request)
        now = time.time()
        return {
            "phone_polling": (now - last_poll["ts"]) < 45.0,
            "seconds_since_poll": round(now - last_poll["ts"], 1) if last_poll["ts"] else None,
            "queued_commands": commands.qsize(),
            "events_held": len(events),
            "commands_total": counters["cmd"],
            "events_total": counters["event"],
        }

    return router
