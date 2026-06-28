#!/usr/bin/env python3
# Xorics — skill memory: how-tos distilled from VERIFIED successes, recalled on
# relevant future tasks.
# Copyright (C) 2026 Zawayix
#
# This file is part of Xorics, free software under the GNU AGPL v3 or later.
# See <https://www.gnu.org/licenses/>. Designs produced by RUNNING Xorics are
# exempt per LICENSE-EXCEPTION.

"""
The thing that lets Xorics stop re-solving the same problem.

A *skill* is a short markdown how-to — "the problem was X; here is the approach that
worked" — written ONLY after the honesty gate has verified the underlying work
actually happened (a sketch that compiled, a circuit that built). It is the task->how
half of delegation memory: the deliverables ledger already records the verified
ARTIFACT at the run_coder seam; this records what it took to get there, so the next
related task starts from the last working answer instead of from scratch.

Storage mirrors store.py on purpose:
  * Same SQLite file (data/xorics.db, $XORICS_DATA_DIR read every call), own `skills`
    table. store.py is left untouched; we honor its documented contract (location from
    the env var) instead of reaching into its internals.
  * The markdown body is ALSO written to the file store under /skills via
    store.add_file(), so a recalled skill shows up in the same app file browser as
    deliverables. App-visibility is best-effort and never blocks a save.

The honesty gate is the spine, not decoration: save_skill() REFUSES to persist a skill
with no validator, and (when a source path is given) refuses if that artifact is not on
disk. A self-improving agent that writes skills enshrining work that never ran is the
fabrication problem with write access — so a skill that can't point at a verified
result simply does not get saved.

Recall is deliberately dumb first (token overlap against title/trigger/tags — the
"what is this for" fields, not the body), because draft-then-fix: get useful recall on
disk, then upgrade to nomic-embed semantic search once it earns its keep.

    import skills; skills.init()            # idempotent; safe on every boot
"""

import os
import re
import time
import uuid
import sqlite3
from pathlib import Path
from contextlib import contextmanager

try:
    import store  # public API only: store.add_file() for app-visible markdown
except Exception:        # storage/recall core works without it (e.g. isolated tests)
    store = None


# --- location: SAME db as store.py, by its documented contract -----------------
def _data_dir() -> Path:
    return Path(os.environ.get("XORICS_DATA_DIR", Path.home() / "xorics-ai" / "data"))


def _db_path() -> Path:
    return _data_dir() / "xorics.db"


def _now() -> float:
    return time.time()


def _id() -> str:
    return uuid.uuid4().hex


def _row(r):
    return dict(r) if r is not None else None


def _rows(rs):
    return [dict(r) for r in rs]


@contextmanager
def _conn():
    _data_dir().mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(_db_path())
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    try:
        yield c
        c.commit()
    finally:
        c.close()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS skills(
    id          TEXT PRIMARY KEY,
    created_at  REAL,
    updated_at  REAL,
    title       TEXT NOT NULL,
    trigger     TEXT NOT NULL,
    domain      TEXT,
    tags        TEXT,
    body        TEXT NOT NULL,
    validator   TEXT NOT NULL,
    source_path TEXT,
    source_chat TEXT,
    file_id     TEXT,
    times_used  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_skills_domain ON skills(domain);
"""


def init() -> None:
    """Create the skills table if absent. Idempotent; safe on every boot."""
    with _conn() as c:
        c.executescript(_SCHEMA)


# --- tokenization for recall ---------------------------------------------------
_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set:
    return set(_WORD.findall((text or "").lower()))


class UnverifiedSkill(ValueError):
    """Raised when save_skill is called without a validator, or with a missing source."""


def save_skill(title: str, trigger: str, body: str, validator: str,
               *, domain: str = "", tags: str = "", source_path: str = "",
               source_chat: str = "", mirror_to_files: bool = True) -> dict:
    """Persist a skill — ONLY if it is backed by a verified result.

    validator   : the honesty-gate check that signed off on the underlying work
                  (e.g. "compile_check", "check_circuit_file"). REQUIRED, non-empty.
    source_path : the artifact the skill was distilled from. If given, it MUST exist
                  on disk or the skill is refused (mirrors the ledger's verified-to-disk).

    Returns the stored skill dict. Raises UnverifiedSkill if the gate fails.
    """
    if not (validator or "").strip():
        raise UnverifiedSkill("refusing to save a skill with no validator")
    if source_path and not os.path.exists(source_path):
        raise UnverifiedSkill(
            f"refusing to save a skill whose source artifact is missing: {source_path}")
    if not (title or "").strip() or not (trigger or "").strip() or not (body or "").strip():
        raise ValueError("title, trigger, and body are all required")

    now, sid, file_id = _now(), _id(), ""
    if mirror_to_files and store is not None:
        try:
            md = f"# {title}\n\n**When:** {trigger}\n\n**Verified by:** {validator}\n"
            if source_path:
                md += f"**From:** {source_path}\n"
            md += "\n" + body.strip() + "\n"
            safe_name = (title.strip().replace("/", "-") or "skill") + ".md"
            f = store.add_file(safe_name, md.encode("utf-8"),
                               folder="/skills", mime="text/markdown")
            file_id = f.get("id", "")
        except Exception:
            file_id = ""        # never block a verified skill on app-visibility

    with _conn() as c:
        c.execute(
            "INSERT INTO skills(id,created_at,updated_at,title,trigger,domain,tags,body,"
            "validator,source_path,source_chat,file_id,times_used) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)",
            (sid, now, now, title.strip(), trigger.strip(), domain.strip(), tags.strip(),
             body.strip(), validator.strip(), source_path, source_chat, file_id))
        row = c.execute("SELECT * FROM skills WHERE id=?", (sid,)).fetchone()
    return _row(row)


def get_skill(sid: str):
    with _conn() as c:
        return _row(c.execute("SELECT * FROM skills WHERE id=?", (sid,)).fetchone())


def list_skills(domain: str = "") -> list:
    with _conn() as c:
        if domain:
            rs = c.execute("SELECT * FROM skills WHERE domain=? ORDER BY created_at DESC",
                           (domain,)).fetchall()
        else:
            rs = c.execute("SELECT * FROM skills ORDER BY created_at DESC").fetchall()
    return _rows(rs)


def search_skills(task: str, k: int = 3, domain: str = "", min_score: float = 1.0) -> list:
    """Recall: rank skills by token overlap of `task` against title/trigger/tags/domain.

    Matches on the 'what is this for' fields, not the body, to keep recall sharp.
    Returns up to k skills (each with an added 'score'), best first, score >= min_score.
    """
    qt = _tokens(task)
    if not qt:
        return []
    out = []
    for s in list_skills(domain):
        title_t = _tokens(s["title"])      # strongest signal
        trig_t = _tokens(s["trigger"])
        tag_t = _tokens(s["tags"]) | _tokens(s["domain"])
        score = 2.0 * len(qt & title_t) + 1.5 * len(qt & trig_t) + 1.0 * len(qt & tag_t)
        if score >= min_score:
            sc = dict(s)
            sc["score"] = score
            out.append(sc)
    out.sort(key=lambda s: s["score"], reverse=True)
    return out[:k]


def mark_used(sid: str) -> None:
    """Bump the use counter (for later Curator pruning of dead weight)."""
    with _conn() as c:
        c.execute("UPDATE skills SET times_used=times_used+1, updated_at=? WHERE id=?",
                  (_now(), sid))


def format_for_prompt(found: list, max_body_chars: int = 700) -> str:
    """Render recalled skills compactly for injection into the system message.

    Lives here so the recall hook in ask() stays one line and the token budget is in
    one place — injected skills compete with history under _trim_history.
    """
    if not found:
        return ""
    parts = ["## Recalled skills — how you solved related tasks before "
             "(reuse the working approach, don't reinvent):"]
    for s in found:
        body = s["body"].strip()
        if len(body) > max_body_chars:
            body = body[:max_body_chars].rstrip() + " …"
        parts.append(f"### {s['title']} "
                     f"(when: {s['trigger']}; verified by {s['validator']})\n{body}")
    return "\n\n".join(parts)
