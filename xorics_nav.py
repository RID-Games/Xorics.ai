#!/usr/bin/env python3
# Xorics — nav app #1: turn-by-turn text on the G2 HUD.
# Copyright (C) 2026 Zawayix
#
# This file is part of Xorics, free software under the GNU AGPL v3 or later.
# See <https://www.gnu.org/licenses/>. Designs produced by RUNNING Xorics are
# exempt per LICENSE-EXCEPTION.

"""
Increment 1 of the nav app: GraphHopper (on RIDGames) computes a driving
route; each turn instruction is formatted as a small text block and pushed to
the lens over the proven display path (xorics_glasses.display). No GPS yet —
steps advance on Enter. Proves routing → formatting → HUD end to end.

Increment 2 — active route: show_route / clear_route are the manager's tool
surface (registered in xorics.py). show_route validates coordinates, routes,
and persists the route via save_active_route to state/nav_route.json;
dashboard_router's Nav page reads that file on every fetch, so a voice ask
lands on the lens immediately and survives a bridge restart. Coordinates only
until the geocoder (§3.B) and GPS-origin (§3.C) increments land.

Usage:
    python3 xorics_nav.py selftest
        Offline oracle: formats an embedded GraphHopper fixture, asserts
        layout invariants. No network, no glasses. Exits 0 on OK.

    python3 xorics_nav.py LAT,LON LAT,LON [LAT,LON ...]
        Fetch a car route from GraphHopper and print every HUD block to the
        terminal (smoke test — no glasses needed).

    python3 xorics_nav.py LAT,LON LAT,LON --hud
        Same, but each block is also displayed on the glasses. Enter
        advances to the next step, q+Enter quits. Bridge + Xorics G2
        service must be running.

Env:
    XORICS_GH_URL       GraphHopper base URL   (default http://127.0.0.1:8989)
    XORICS_NAV_UNITS    "mi" (default) or "km"
    XORICS_BRIDGE_URL   passed through to xorics_glasses for --hud

Layout contract (GlassesText wraps at 25 chars/line, 10 lines/page; a dense
10x25 page overflows the length byte, so every block here stays tiny):
    line 1:  glyph + distance          e.g.  "> 0.3 mi"        (short)
    line 2:  GraphHopper maneuver text, hard-capped at 50 chars
             (device wraps it to at most 2 lines)
    line 3:  "step/total  ETA N min"
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GH_URL = os.environ.get("XORICS_GH_URL", "http://127.0.0.1:8989").rstrip("/")
UNITS = os.environ.get("XORICS_NAV_UNITS", "mi")

MANEUVER_MAX = 50  # chars; device wraps at 25, so <= 2 rendered lines

# GraphHopper instruction sign -> HUD glyph (ASCII only — G2 font coverage
# for arrows is unverified, so we don't gamble on Unicode).
SIGN_GLYPH = {
    -98: "u",   # U-turn (unknown side)
    -8: "u",    # U-turn left
    8: "u",     # U-turn right
    -7: "<",    # keep left
    7: ">",     # keep right
    -6: "(o)",  # leave roundabout
    6: "(o)",   # use roundabout
    -3: "<<",   # sharp left
    -2: "<",    # left
    -1: "<",    # slight left
    0: "^",     # continue
    1: ">",     # slight right
    2: ">",     # right
    3: ">>",    # sharp right
    4: "[*]",   # arrive
    5: "[+]",   # via point
}


def fmt_dist(meters, units=None):
    """300 ft / 0.3 mi (default) or 300 m / 1.6 km."""
    u = units or UNITS
    if u == "km":
        if meters < 1000:
            return "%d m" % (round(meters / 10.0) * 10)
        km = meters / 1000.0
        return ("%.1f km" % km) if km < 10 else ("%d km" % round(km))
    feet = meters * 3.28084
    if feet < 1000:
        return "%d ft" % (round(feet / 10.0) * 10)
    miles = meters / 1609.344
    return ("%.1f mi" % miles) if miles < 10 else ("%d mi" % round(miles))


def fmt_eta(ms):
    mins = round(ms / 60000.0)
    return "<1 min" if mins < 1 else "%d min" % mins


def fmt_step(instr, idx, total, remaining_ms):
    """One HUD block (a single string with newlines) for instruction idx."""
    glyph = SIGN_GLYPH.get(instr.get("sign", 0), "^")
    text = (instr.get("text") or "").strip() or "Continue"
    if len(text) > MANEUVER_MAX:
        text = text[: MANEUVER_MAX - 3] + "..."
    line1 = "%s %s" % (glyph, fmt_dist(instr.get("distance", 0.0)))
    line3 = "%d/%d  ETA %s" % (idx + 1, total, fmt_eta(remaining_ms))
    return "\n".join([line1, text, line3])


def blocks_from_path(path):
    """All HUD blocks for a GraphHopper path, in order."""
    instrs = path.get("instructions") or []
    out = []
    remaining = sum(i.get("time", 0) for i in instrs)
    for idx, instr in enumerate(instrs):
        out.append(fmt_step(instr, idx, len(instrs), remaining))
        remaining -= instr.get("time", 0)
    return out


def route(points):
    """Car route through points [(lat, lon), ...] -> GraphHopper paths[0]."""
    q = [("profile", "car"), ("instructions", "true"),
         ("points_encoded", "false"), ("locale", "en")]
    for lat, lon in points:
        q.append(("point", "%s,%s" % (lat, lon)))
    url = GH_URL + "/route?" + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("message", str(e))
        except Exception:
            msg = str(e)
        raise SystemExit("GraphHopper rejected the request: %s" % msg)
    except urllib.error.URLError as e:
        raise SystemExit(
            "Can't reach GraphHopper at %s (%s) — is the server running?"
            % (GH_URL, e.reason))
    paths = data.get("paths") or []
    if not paths:
        raise SystemExit("GraphHopper returned no route: %s"
                         % data.get("message", data))
    return paths[0]


def parse_point(s):
    try:
        lat, lon = (float(x) for x in s.split(","))
    except ValueError:
        raise SystemExit("Bad point %r — expected LAT,LON" % s)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise SystemExit("Point %r out of range" % s)
    return lat, lon


# ------------------------------------------------- active route (increment 2)
# The manager-set route the dash Nav page displays. Written by the show_route
# tool below, read by dashboard_router._nav_lines on every page fetch — so a
# voice ask handled inside the bridge process is visible on the lens
# immediately, and because it's a FILE (not module state) a route set from the
# xo REPL (a separate process) shows on the lens too, and survives a bridge
# restart. Lives in state/ (gitignored, per-machine, regenerated) next to the
# chat transcript. XORICS-FEATURE: nav-tool

# Keep in sync with dashboard_router.DEFAULT_ROUTE (its own env-or-baked copy,
# retained so the dash still degrades sanely if this module is absent).
DEFAULT_ROUTE_SPEC = os.environ.get("XORICS_NAV_ROUTE",
                                    "44.5013,-88.0622 44.5433,-87.8262")
ACTIVE_ROUTE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "state", "nav_route.json")


def save_active_route(spec, label=None):
    """Validate and persist `spec` ("LAT,LON LAT,LON [...]") as the active route.

    Atomic write (tmp + os.replace) so the read side can never see a
    half-written file. Raises SystemExit on a bad spec — the module's CLI
    idiom, same contract as route()/parse_point(); callers that must not die
    (the show_route tool, the dash) catch it."""
    pts = [parse_point(p) for p in spec.split()]
    if len(pts) < 2:
        raise SystemExit("Route needs at least 2 points, got %d" % len(pts))
    os.makedirs(os.path.dirname(ACTIVE_ROUTE_PATH), exist_ok=True)
    tmp = ACTIVE_ROUTE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"points": spec, "label": label, "set_at": time.time()}, f)
    os.replace(tmp, ACTIVE_ROUTE_PATH)


def load_active_route():
    """The active route dict ({"points", "label", "set_at"}) or None.

    Never raises: the read side is the lens, which must degrade to the default
    route rather than 500 — missing file, corrupt JSON, or wrong shape all
    read as "no active route"."""
    try:
        with open(ACTIVE_ROUTE_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) and data.get("points") else None
    except (OSError, ValueError):
        return None


def clear_active_route():
    """Remove the active route. True if one was cleared, False if none was set."""
    try:
        os.remove(ACTIVE_ROUTE_PATH)
        return True
    except OSError:
        return False


# ------------------------------------------- manager tools (wired in xorics.py)

def show_route(destination, origin=None, label=None):
    """Manager tool: compute a car route and put it on the glasses Nav page.

    Coordinates only ("LAT,LON") — there is no geocoder (§3.B) and no GPS
    (§3.C) yet, so a missing `origin` falls back to the first point of
    DEFAULT_ROUTE_SPEC and the result says so. Always returns a plain string:
    every failure (bad point, GraphHopper down, point outside the loaded map)
    is caught HERE — including this module's SystemExit CLI idiom, which the
    xorics.py agent loop does NOT catch (`except Exception` misses it) and
    which would otherwise kill the bridge worker mid-ask.
    XORICS-FEATURE: nav-tool"""
    try:
        dest = str(destination).strip()
        parse_point(dest)                       # validate, keep the given string
        if origin:
            org = str(origin).strip()
            parse_point(org)
            org_src = "given"
        else:
            org = DEFAULT_ROUTE_SPEC.split()[0]
            org_src = "default origin — no GPS yet"
        spec = "%s %s" % (org, dest)
        path = route([parse_point(p) for p in spec.split()])
        blocks = blocks_from_path(path)
        save_active_route(spec, label=label)
    except SystemExit as e:
        return ("[nav error: %s] — pass coordinates as 'LAT,LON' inside the "
                "loaded map region; if you only have a street address, say you "
                "can't geocode yet instead of guessing." % e)
    except Exception as e:  # pragma: no cover — belt for non-CLI failures
        return "[nav error: %s: %s]" % (type(e).__name__, e)
    first = blocks[0].split("\n")[0] if blocks else "(no instructions)"
    return ("ROUTE SET -> %s: %s, ETA %s, %d steps; first: %s. From %s (%s). "
            "It is on the glasses Nav page now; clear_route removes it."
            % (label or dest, fmt_dist(path.get("distance", 0.0)),
               fmt_eta(path.get("time", 0)), len(blocks), first, org, org_src))


def clear_route():
    """Manager tool: take the manager-set route off the Nav page (back to the
    default preview route). Never raises. XORICS-FEATURE: nav-tool"""
    return ("Route cleared — the Nav page is back on its default route."
            if clear_active_route() else
            "No active route was set; the Nav page is already on its default route.")


# ---------------------------------------------------------------- selftest

FIXTURE = {"paths": [{
    "distance": 4517.3, "time": 512000,
    "instructions": [
        {"text": "Continue onto Harbor Way", "street_name": "Harbor Way",
         "distance": 812.4, "time": 93000, "sign": 0, "interval": [0, 7]},
        {"text": "Turn left onto Marina Blvd", "street_name": "Marina Blvd",
         "distance": 1620.0, "time": 181000, "sign": -2, "interval": [7, 31]},
        {"text": "Keep right and take the ramp onto US-101 North",
         "street_name": "US-101 N",
         "distance": 1704.6, "time": 186000, "sign": 7, "interval": [31, 58]},
        {"text": "Turn sharp right onto Gate Rd", "street_name": "Gate Rd",
         "distance": 352.1, "time": 47000, "sign": 3, "interval": [58, 66]},
        {"text": "Arrive at destination", "street_name": "",
         "distance": 28.2, "time": 5000, "sign": 4, "interval": [66, 67]},
    ]}]}


def selftest():
    ok = True

    def check(name, got, want):
        nonlocal ok
        if got != want:
            ok = False
            print("FAIL %s: got %r want %r" % (name, got, want))

    check("dist ft", fmt_dist(28.2, "mi"), "90 ft")
    check("dist mi", fmt_dist(1620.0, "mi"), "1.0 mi")
    check("dist m", fmt_dist(812.4, "km"), "810 m")
    check("dist km", fmt_dist(1620.0, "km"), "1.6 km")
    check("eta", fmt_eta(512000), "9 min")
    check("eta sub-minute", fmt_eta(5000), "<1 min")

    blocks = blocks_from_path(FIXTURE["paths"][0])
    check("block count", len(blocks), 5)
    for i, b in enumerate(blocks):
        lines = b.split("\n")
        check("block %d line count" % i, len(lines), 3)
        for ln in lines:
            if len(ln) > MANEUVER_MAX:
                ok = False
                print("FAIL block %d line too long (%d): %r"
                      % (i, len(ln), ln))
        print(b)
        print("-" * 25)
    check("first glyph", blocks[0].split()[0], "^")
    check("keep-right glyph", blocks[2].split()[0], ">")
    check("arrive glyph", blocks[4].split()[0], "[*]")
    check("truncation",
          len(blocks[2].split("\n")[1]) <= MANEUVER_MAX, True)
    check("first ETA", blocks[0].split("\n")[2], "1/5  ETA 9 min")
    check("last ETA", blocks[4].split("\n")[2], "5/5  ETA <1 min")

    # Active-route state round-trip (increment 2) — side-effect-free: the
    # module path is pointed at a temp dir for the duration, so a selftest can
    # never clobber a real route the lens is showing.
    global ACTIVE_ROUTE_PATH
    import tempfile
    _orig_path = ACTIVE_ROUTE_PATH
    try:
        with tempfile.TemporaryDirectory() as td:
            ACTIVE_ROUTE_PATH = os.path.join(td, "nav_route.json")
            check("no route yet", load_active_route(), None)
            save_active_route("44.5013,-88.0622 44.5433,-87.8262",
                              label="New Franken")
            got = load_active_route() or {}
            check("route points", got.get("points"),
                  "44.5013,-88.0622 44.5433,-87.8262")
            check("route label", got.get("label"), "New Franken")
            check("clear", clear_active_route(), True)
            check("clear again", clear_active_route(), False)
            check("cleared", load_active_route(), None)
            check("clear_route msg",
                  "already" in clear_route(), True)
            try:
                save_active_route("garbage")
                check("bad spec rejected", "no exit", "SystemExit")
            except SystemExit:
                pass
            try:
                save_active_route("44.5,-88.0")
                check("one-point spec rejected", "no exit", "SystemExit")
            except SystemExit:
                pass
    finally:
        ACTIVE_ROUTE_PATH = _orig_path

    print("SELFTEST OK" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


# --------------------------------------------------------------------- cli

def main(argv):
    if len(argv) >= 1 and argv[0] == "selftest":
        return selftest()

    hud = "--hud" in argv
    pts = [parse_point(a) for a in argv if a != "--hud"]
    if len(pts) < 2:
        print(__doc__)
        return 2

    path = route(pts)
    blocks = blocks_from_path(path)
    print("Route: %d steps, %s, %s" % (
        len(blocks), fmt_dist(path.get("distance", 0.0)),
        fmt_eta(path.get("time", 0))))
    print("=" * 25)

    g = None
    if hud:
        import xorics_glasses as g  # lazy: terminal mode needs no bridge
        st = g.status()
        if not st.get("phone_polling"):
            print("WARNING: phone is not polling — start the glasses "
                  "service in the Xorics G2 app (Button 2)")

    for b in blocks:
        print(b)
        print("-" * 25)
        if g is not None:
            cid = g.display(b)
            ack = g.wait_ack(cid)
            print("ack:" if ack else "ACK TIMEOUT — check logcat -s XoricsG2",
                  ack or "")
            if input("[Enter]=next step, q=quit > ").strip().lower() == "q":
                break
    print("Nav run complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
