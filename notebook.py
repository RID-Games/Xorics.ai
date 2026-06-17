#!/usr/bin/env python3
# Xorics — a self-hosted local AI assistant for embedded / PCB engineering.
# Copyright (C) 2026 Zawayix
#
# This file is part of Xorics. Xorics is free software: you can redistribute it
# and/or modify it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Xorics is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE. See the GNU Affero General Public License for details.
#
# You should have received a copy of the GNU Affero General Public License along
# with Xorics. If not, see <https://www.gnu.org/licenses/>.
#
# ADDITIONAL PERMISSION (AGPLv3 section 7): designs and files produced by RUNNING
# Xorics, and any fragments it embeds into that output, are NOT covered by the
# AGPL — you may license your generated designs as you wish. See LICENSE-EXCEPTION.

"""
Xorics coder notebook -- externalized, context-trim-proof memory for the coder loop.
XORICS-FEATURE: coder-notebook

WHY THIS EXISTS
The SKiDL build loop used to spin on part_pins: it re-fetched lookups it had already
resolved and never wrote a netlist. Two compounding causes -- (1) no resolved-parts
memory, so each identical call looked new, and (2) 32K context overflow scrolling the
early results out of the window. Prose guidance ("don't re-search a part you already
found") didn't hold, because it's just text the model can ignore.

This notebook is the mechanical fix:
  * AUTO-WRITE: every successful lookup is recorded by the harness (not the model) into
    a compact block that rides in the system message -- so it survives _trim_history,
    which always keeps the head full-size. The coder is re-grounded every single turn.
  * DEDUP GUARD: identical lookups are capped. The first repeat returns the CACHED result
    plus a nudge; further repeats are HARD-REFUSED (nudge only, impl never called) -- a
    guard the model can't prose its way around, because the tool simply doesn't fire.

Scope decisions (session handoff 2026-06-16):
  * Auto-write only for now. A model-driven note() tool is on the do-later list.
  * Dedup applies to READ-ONLY lookups only (find_part / part_pins / search_datasheets),
    never to check_circuit/compile_check -- driving the coder TO the validator is the goal.
  * Errored calls are exempt: record() runs only on success, so a transient failure never
    populates the cache and a legitimate retry is never refused.

STORAGE: RAM is the source of truth. We flush an atomic, write-on-change markdown file
purely for durability + inspection (`cat` it to watch what the coder thinks it knows).
The notebook must NEVER take down the coder loop, so every public method is defensive.
"""

import json
import os
import re
import time

# How many identical repeats of a lookup to tolerate before hard-refusing.
#   0 = refuse on the very first repeat (the pinned block already re-serves the data)
#   1 = allow one cached echo, then refuse  (default: gentle insurance)
LOOKUP_REPEAT_LIMIT = 1

# Read-only lookups that are safe to dedup. NOT check_circuit / compile_check.
LOOKUP_TOOLS = ("find_part", "part_pins", "search_datasheets")

NOTEBOOK_DIR = os.environ.get("XORICS_NOTEBOOK_DIR", "notebooks")


def _slug(text, n=48):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s[:n] or "design"


def _short(text, n=120):
    s = " ".join(str(text).split())
    return s if n is None else s[:n]


# XORICS-FEATURE: house-parts
# Canonical staples seeded into every coder session so the coder uses verified names directly
# instead of searching (and mis-ranking) parts it needs on every board. The (lib, name) entries
# are instantiation-checked by apply-house-parts.sh at apply time -- if any fail to load, that
# script aborts before writing, so this list can't go stale into "authoritative but wrong".
HOUSE_PARTS = [
    ("Device", "R", "value='10k'; pins NUMERIC: r[1], r[2]"),
    ("Device", "C", "value='0.1uF'; pins NUMERIC: c[1], c[2]"),
    ("Device", "C_Polarized", "bulk/electrolytic, value='10uF'; pins NUMERIC"),
    ("Device", "Crystal", "value='16MHz'; pins 1, 2"),
    ("Device", "LED", "pins: K, A"),
    ("Switch", "SW_Push", "tactile / reset button; pins NUMERIC"),
    ("MCU_Microchip_ATmega", "ATmega328P-P", "DIP-28 AVR; call part_pins once for the full 28-pin map"),
]

HOUSE_HEADERS = (
    "HEADERS/CONNECTORS -- choose by GEOMETRY; never a bare find_part('Header'):\n"
    "    find_part('Header 2x14') -> Connector_Generic:Conn_02x14_Odd_Even (dual-row, all I/O)\n"
    "    find_part('Header 2x3')  -> Connector_Generic:Conn_02x03_Odd_Even (ISP)\n"
    "    find_part('Header 2 Pin')-> Connector_Generic:Conn_01x02 (power, single-row)\n"
    "  Generic Conn_* pins are NUMERIC: header[1], header[2]; do NOT use pin names."
)


def _house_lines():
    """The static HOUSE PARTS block pinned above the auto-tracked notebook every turn.
    XORICS-FEATURE: house-parts"""
    out = ["--- HOUSE PARTS (verified -- use these names directly; no find_part needed) ---"]
    for lib, name, note in HOUSE_PARTS:
        out.append(f"  Part('{lib}', '{name}')  -- {note}")
    out.append(HOUSE_HEADERS)
    out.append("For any part NOT listed here, use find_part.")
    return out


class Notebook:
    def __init__(self, task=""):
        self.task = task if isinstance(task, str) else str(task)
        self._cache = {}     # key -> cached result text (successful lookups only)
        self._display = {}   # key -> (name, args) for rendering RESOLVED lines
        self._order = []     # keys in first-seen order
        self._repeats = {}   # key -> repeat count
        self._parts = False
        self._pins = False
        self._checked = False
        self._built = False
        self._tripped = False   # a lookup hit the refusal -> coder is repeating
        self._last_written = None
        try:
            os.makedirs(NOTEBOOK_DIR, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            self.path = os.path.join(NOTEBOOK_DIR, f"{_slug(self.task)}-{ts}.md")
        except Exception:
            self.path = None

    # key canonicalization: case/space-insensitive so trivially-different repeats still
    # collide; original args are kept separately for display.
    def _key(self, name, args):
        norm = {}
        for k, v in (args or {}).items():
            norm[k] = " ".join(str(v).split()).lower() if isinstance(v, str) else v
        return name + "|" + json.dumps(norm, sort_keys=True)

    # gate: called BEFORE the impl. None => let it run. str => short-circuit the call.
    def gate(self, name, args):
        try:
            if name not in LOOKUP_TOOLS:
                return None
            key = self._key(name, args)
            if key not in self._cache:          # never successfully done -> allow
                return None
            self._repeats[key] = self._repeats.get(key, 0) + 1
            if self._repeats[key] <= LOOKUP_REPEAT_LIMIT:
                return ("[notebook] You already resolved this -- reusing the cached result. "
                        "Do NOT look it up again; use these exact names and move on.\n\n"
                        + self._cache[key])
            self._tripped = True
            self._flush()
            return ("[notebook] REFUSED: identical lookup already resolved. The result is in "
                    "your NOTEBOOK (top of this message). Stop researching -- write the complete "
                    "SKiDL script now and call check_circuit.")
        except Exception:
            return None     # never block the loop on a notebook bug

    # record: called AFTER a successful impl (errors raise before this is reached).
    def record(self, name, args, result):
        try:
            if name == "find_part":
                self._parts = True
                self._pins = True          # find_part also prints the top match's pins
            elif name == "part_pins":
                self._pins = True
            elif name == "check_circuit":
                self._checked = True
                if getattr(result, "status", None) == "built" or "CIRCUIT BUILT" in str(result):
                    self._built = True
            if name in LOOKUP_TOOLS:
                key = self._key(name, args)
                if key not in self._cache:
                    self._cache[key] = str(result)
                    self._display[key] = (name, dict(args or {}))
                    self._order.append(key)
            self._flush()
        except Exception:
            pass            # a flush/parse hiccup must never discard a good result

    # render: the compact block pinned into the system message each turn.
    def render(self):
        try:
            lines = _house_lines() + ["", "--- NOTEBOOK (auto-tracked; survives context trimming) ---",
                     "RESOLVED -- reuse these EXACT names; do NOT look them up again:"]
            if self._order:
                for key in self._order:
                    name, args = self._display[key]
                    a = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
                    # XORICS-FEATURE: notebook-full-pins -- the pin list the coder must connect to
                    # must survive verbatim; part_pins is never capped, find_part keeps its top-match pins.
                    cap = {"part_pins": None, "find_part": 600}.get(name, 120)
                    lines.append(f"  - {name}({a}) -> {_short(self._cache[key], cap)}")
            else:
                lines.append("  - (nothing resolved yet)")

            def box(b):
                return "[x]" if b else "[ ]"

            lines.append(f"PROGRESS: {box(self._parts)} parts  {box(self._pins)} pins  "
                         f"{box(self._checked)} netlist checked  {box(self._built)} BUILT")
            if self._parts and self._pins and not self._built and (self._tripped or self._order):
                lines.append("-> You have your parts and pins. STOP looking things up. Write the "
                             "complete SKiDL script and call check_circuit NOW.")
            lines.append("----------------------------------------------------------")
            return "\n".join(lines) + "\n"
        except Exception:
            return ""       # degrade to no pinned block rather than crash the turn

    # atomic, write-on-change flush to a cat-able markdown file.
    def _flush(self):
        if not self.path:
            return
        try:
            body = ("# Xorics coder notebook\n"
                    f"task: {self.task}\n"
                    f"updated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"repeat-limit: {LOOKUP_REPEAT_LIMIT}\n"
                    + self.render())
            if body == self._last_written:      # write-on-change: skip identical content
                return
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(body)
            os.replace(tmp, self.path)          # atomic
            self._last_written = body
        except Exception:
            pass
