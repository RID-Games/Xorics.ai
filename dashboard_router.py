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
    app.include_router(make_dashboard_router(_auth,
                                             busy=_ASK_LOCK.locked,
                                             last_ask=lambda: dict(_LAST_ASK)))
busy/last_ask are optional: without them the agent page degrades to "?" fields
instead of failing, so the router stays importable standalone (tests).

AGENT page — every field is real state, no narration:
    state  = the bridge's _ASK_LOCK (busy iff an ask() is running right now)
    brain  = xorics.BRAIN (+plan when PLAN_MODE), read live from the module
    task   = last ask routed through THIS bridge process (xorics.ask() is
             stateless by contract, so xorics._CHAT_HISTORY does NOT update on
             bridge asks — the bridge tracks its own _LAST_ASK instead)
    built  = tail of the honesty ledger (xorics._load_deliverables()): only
             files a validator actually passed. Empty ledger -> "—", honestly.

NAV page — wraps xorics_nav.py (repo root; GraphHopper :8989 on RIDGames):
    Route points come from ?from=LAT,LON&to=LAT,LON, else the manager-set
    active route (xorics_nav.load_active_route — written by the show_route
    tool; adds a "to: <label>" line when labeled), else XORICS_NAV_ROUTE,
    else the curl-proven WI route (Lambeau Field -> New Franken).
    Renders: totals, the first turn block, and a next-turn preview.
    xorics_nav.route()/parse_point() raise SystemExit (CLI idiom) — caught
    explicitly here, since `except Exception` would NOT catch it and a
    GraphHopper outage must degrade the page, not kill the worker.
    No GPS yet: this increment proves routing -> bridge -> dash -> lens.
    Step advance / live position is future work (needs the List/DETAIL view).

Env (optional):
    XORICS_NEWS_FEED    RSS/Atom URL, default BBC World
    XORICS_NAV_ROUTE    default route "LAT,LON LAT,LON [LAT,LON ...]"
"""

import os
import time
import urllib.request
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Query, Request
from starlette.concurrency import run_in_threadpool

NEWS_FEED = os.environ.get("XORICS_NEWS_FEED", "https://feeds.bbci.co.uk/news/world/rss.xml")

# Lambeau Field -> New Franken: the exact pair curl-proven against the local
# Wisconsin extract (Geofabrik 2026-07-03; ~23 km, 12-step car route).
DEFAULT_ROUTE = os.environ.get("XORICS_NAV_ROUTE",
                               "44.5013,-88.0622 44.5433,-87.8262")

MAX_LINES = 9   # fits the 560x272 body pane with headroom; tune on-lens
MAX_CHARS = 26  # raw-BLE formatter used 25; SDK font metrics unknown; tune on-lens


def _clip(s: str) -> str:
    s = " ".join((s or "").split())  # collapse whitespace/newlines
    return s if len(s) <= MAX_CHARS else s[: MAX_CHARS - 1] + "\u2026"


def _wrap(s: str, width: int = MAX_CHARS) -> list[str]:
    """Word-wrap into full lines (nav maneuvers are <=50 chars and must not be
    clipped mid-instruction — 'Keep right and take the…' is a wrong turn)."""
    words = " ".join((s or "").split()).split(" ")
    lines: list[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w) if cur else w
    if cur:
        lines.append(cur)
    return [_clip(l) for l in lines] or [""]   # _clip guards lone >width words


def _hud(title: str, rows: list[str]) -> dict:
    lines = [title.upper()] + [_clip(r) for r in rows]
    return {"lines": lines[:MAX_LINES]}


def _hhmm(ts) -> str:
    try:
        return time.strftime("%H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return "?"


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


# --- agent (live bridge + xorics state; every row is verifiable) --------------
def _agent_rows(busy, last_ask) -> list[str]:
    import xorics  # already imported (and configured) by bridge.py; cached

    rows = []
    if busy is not None:
        rows.append("state: " + ("busy" if busy() else "idle"))
    else:
        rows.append("state: ?")

    brain = getattr(xorics, "BRAIN", None) or "?"
    if getattr(xorics, "PLAN_MODE", False):
        brain += " +plan"
    rows.append("brain: " + brain)

    last = last_ask() if last_ask else None
    if last and last.get("text"):
        rows.append("task: " + last["text"])
        rows.append("asked: " + _hhmm(last.get("ts")))
    else:
        rows.append("task: \u2014")

    deliv = xorics._load_deliverables()
    if deliv:
        d = deliv[-1]
        rows.append("built: " + os.path.basename(str(d.get("path", "?"))))
        rows.append("via: " + str(d.get("validator", "?")))
        rows.append("at: " + _hhmm(d.get("ts")))
    else:
        rows.append("built: \u2014")   # honesty ledger empty — say so, no dressing
    return rows


# --- nav (xorics_nav.py -> GraphHopper :8989; module is repo-root local) ------
def _nav_lines(frm: str | None, to: str | None) -> list[str]:
    """Blocking (urllib inside xorics_nav) — call via run_in_threadpool."""
    try:
        import xorics_nav  # lazy: bridge must still boot if the file is absent
    except ImportError:
        return ["(xorics_nav missing)", "expected in repo root"]

    if (frm is None) != (to is None):   # exactly one given — ambiguous, refuse
        return ["need BOTH from & to", "or neither (default)"]
    label = None
    if frm:
        spec = "%s %s" % (frm, to)
    else:
        # Manager-set route (show_route tool) beats the launch-time default.
        # getattr guards version skew: an older xorics_nav on disk without the
        # helper must degrade to the default, not 500 the lens.
        # XORICS-FEATURE: nav-tool
        active = getattr(xorics_nav, "load_active_route", lambda: None)()
        if active:
            spec, label = active["points"], active.get("label")
        else:
            spec = DEFAULT_ROUTE

    try:
        pts = [xorics_nav.parse_point(p) for p in spec.split()]
        if len(pts) < 2:
            return ["(bad route spec)", _clip(spec)]
        path = xorics_nav.route(pts)
    except SystemExit as e:   # xorics_nav's CLI-idiom errors; except Exception misses these
        return ["(route failed)"] + _wrap(str(e))[:4]
    except Exception as e:
        return ["(route error)", _clip(type(e).__name__)]

    blocks = xorics_nav.blocks_from_path(path)
    if not blocks:
        return ["(no instructions)"]

    lines = ["%s  ETA %s" % (xorics_nav.fmt_dist(path.get("distance", 0.0)),
                             xorics_nav.fmt_eta(path.get("time", 0)))]
    step = blocks[0].split("\n")            # [glyph+dist, maneuver<=50, k/n ETA]
    lines.append(step[0])
    if len(step) > 2:
        lines.extend(_wrap(step[1]))        # wrap, never clip, the instruction
        lines.append(step[2])
    if len(blocks) > 1:
        lines.append("then: " + blocks[1].split("\n")[0])
    if label:
        lines.append("to: " + label)   # manager-set destination, user's words
    return lines


def make_dashboard_router(auth, busy=None, last_ask=None):
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
        try:
            rows = _agent_rows(busy, last_ask)
        except Exception as e:   # state read must degrade, not 500 the lens
            rows = ["(state unreadable)", _clip(type(e).__name__)]
        return _hud("agent", rows)

    @router.get("/nav")
    async def nav(request: Request,
                  frm: str | None = Query(None, alias="from"),
                  to: str | None = None):
        auth(request)
        lines = await run_in_threadpool(_nav_lines, frm, to)
        return _hud("nav", lines)

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
