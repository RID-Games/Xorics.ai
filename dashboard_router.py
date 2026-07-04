#!/usr/bin/env python3
# Xorics — glasses HUD dashboard pages (Even Hub "Xorics Dash" WebView app).
# Copyright (C) 2026 Zawayix
#
# This file is part of Xorics, free software under the GNU AGPL v3 or later.
# See <https://www.gnu.org/licenses/>. Designs produced by RUNNING Xorics are
# exempt per LICENSE-EXCEPTION.

"""
Additive FastAPI router feeding the Xorics Dash glasses app.

Data path (whitelist workaround, proven by xorics-mic-test):
    glasses WebView --same-origin--> vite :5174 /page/<x> --local--> here /dashboard/<x>

Every page endpoint returns compact JSON already shaped for the 1-bit HUD:
    {"lines": ["SHORT", "LINES", ...]}     # <=9 lines, <=26 chars each
/pages returns the modular page config (single source of truth, server-side).

Mount in bridge.py (one line, OpenAI/glasses/api routes untouched):
    app.include_router(make_dashboard_router(_auth))

Env (optional):
    XORICS_NEWS_FEED   RSS/Atom URL, default BBC World
"""

import os
import urllib.request
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

NEWS_FEED = os.environ.get("XORICS_NEWS_FEED", "https://feeds.bbci.co.uk/news/world/rss.xml")

MAX_LINES = 9   # fits the 560x272 body pane with headroom; tune on-lens
MAX_CHARS = 26  # raw-BLE formatter used 25; SDK font metrics unknown; tune on-lens


def _clip(s: str) -> str:
    s = " ".join((s or "").split())  # collapse whitespace/newlines
    return s if len(s) <= MAX_CHARS else s[: MAX_CHARS - 1] + "\u2026"


def _hud(title: str, rows: list[str]) -> dict:
    lines = [title.upper()] + [_clip(r) for r in rows]
    return {"lines": lines[:MAX_LINES]}


# --- news (real data source; proves the adapter shape end to end) -------------
def _fetch_news_lines() -> list[str]:
    req = urllib.request.Request(NEWS_FEED, headers={"User-Agent": "xorics-dash/0.1"})
    with urllib.request.urlopen(req, timeout=3) as r:
        root = ET.fromstring(r.read())
    # RSS 2.0: channel/item/title ; Atom: {ns}entry/{ns}title
    titles = [t.text for t in root.iterfind(".//item/title") if t.text]
    if not titles:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        titles = [t.text for t in root.iterfind(".//a:entry/a:title", ns) if t.text]
    return titles[: MAX_LINES - 1]


def make_dashboard_router(auth):
    router = APIRouter(prefix="/dashboard")

    @router.get("/pages")
    async def pages(request: Request):
        auth(request)
        return {"pages": [{"id": "agent", "label": "Agent"},
                          {"id": "nav", "label": "Nav"},
                          {"id": "news", "label": "News"}]}

    @router.get("/agent")
    async def agent(request: Request):
        auth(request)
        # TODO(next increment): read real manager/coder state from xorics
        # (current task, phase, last verdict, honesty-gate status). Stub proves
        # the glasses -> vite -> bridge path.
        return _hud("agent", ["state: idle", "task: \u2014", "last: \u2014", "gate: \u2014"])

    @router.get("/nav")
    async def nav(request: Request, to: str | None = None):
        auth(request)
        # TODO(next increment): wrap xorics_nav.py (GraphHopper :8989). Its
        # interface is unconfirmed here and the file is uncommitted — wiring it
        # blind would be fabrication. Stub proves the path + query plumbing.
        if to:
            return _hud("nav", [f"to: {to}", "(routing not wired)", "next: xorics_nav.py"])
        return _hud("nav", ["no destination", "(routing not wired)", "next: xorics_nav.py"])

    @router.get("/news")
    async def news(request: Request):
        auth(request)
        try:
            titles = await run_in_threadpool(_fetch_news_lines)
            if titles:
                return _hud("news", titles)
            return _hud("news", ["(feed empty)"])
        except Exception as e:  # unreachable feed must degrade, not 500
            return _hud("news", ["(feed unreachable)", _clip(type(e).__name__)])

    return router
