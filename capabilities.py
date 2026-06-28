#!/usr/bin/env python3
# Xorics — honest self-model: capabilities backed by the skills DB.
# Copyright (C) 2026 Zawayix
#
# This file is part of Xorics, free software under the GNU AGPL v3 or later.
# See <https://www.gnu.org/licenses/>. Designs produced by RUNNING Xorics are
# exempt per LICENSE-EXCEPTION.

"""The one place Xorics describes its own capabilities — wired to the skills DB.

Each capability carries a claim ("proven" / "partial" / "none"), the skills-DB domain
that backs it, what I can do, and the honest gap. A claim is only as good as the
verified work behind it: a "proven" tag with zero skills on file does NOT render as
yes — it downgrades to "built for this but unverified". "partial" always reads
partial; "none" always reads no. Every line carries the live verified count.
"""

import skills


CAPABILITIES = [
    {
        "label": "Embedded firmware (C / Arduino / ESP32)",
        "claim": "proven",
        "domain": "firmware",
        "can": "write firmware and take it through a real compiler",
        "gap": "",
    },
    {
        "label": "PCB / circuit design (SKiDL → KiCad)",
        "claim": "partial",
        "domain": "pcb",
        "can": "research parts and draft early SKiDL",
        "gap": "I have not yet produced a verified KiCad netlist, "
               "so I can't deliver a finished board on my own",
    },
    {
        "label": "Android app development",
        "claim": "none",
        "domain": "android",
        "can": "",
        "gap": "I haven't been taught this and have no work to show for it",
    },
]


def _verified_count(domain):
    """Live count of skills on file for `domain` — the evidence behind a claim.

    Wrapped in try/except so a missing/broken DB returns 0 instead of crashing the
    self-model — a self-description that can't render is worse than one that says
    "nothing verified". init() is idempotent and keeps the table present.
    """
    try:
        skills.init()
        return len(skills.list_skills(domain))
    except Exception:
        return 0


def _render(cap, count):
    label = cap["label"]
    claim = cap["claim"]
    can = cap["can"]
    gap = cap["gap"]
    plural = "" if count == 1 else "s"

    if claim == "proven":
        if count > 0:
            line = f"{label}: yes — I can {can}."
        else:
            line = (f"{label}: I'm built for this but have nothing verified yet "
                    "— treat as unproven.")
    elif claim == "partial":
        line = f"{label}: partial — I can {can}."
        if gap:
            line += f" Limitation: {gap}."
    else:  # none
        line = f"{label}: no — {gap}."

    line += f" ({count} verified skill{plural} on file)"
    return line


def self_knowledge():
    """The whole self-model as one string — one line per capability, live counts."""
    return "\n".join(_render(cap, _verified_count(cap["domain"])) for cap in CAPABILITIES)