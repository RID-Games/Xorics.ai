#!/usr/bin/env python3
# Xorics — local persistence: projects, chats, messages, files.
# Copyright (C) 2026 Zawayix
#
# This file is part of Xorics, free software under the GNU AGPL v3 or later.
# See <https://www.gnu.org/licenses/>. Designs produced by RUNNING Xorics are
# exempt per LICENSE-EXCEPTION.

"""
The state layer the bridge has been missing.

Until now Xorics had exactly one conversation: ask() rebuilt [system, user] from
scratch every call, so it never remembered the previous turn, and /v1/upload threw
the file away after answering. To use Xorics as a primary agent the way Claude is
used, three things have to persist, and they share one shape that mirrors Claude's:

    projects ──< chats ──< messages          (a project groups conversations)
    projects ──< files                        (a project also holds uploaded files)

    project_id is nullable everywhere: a chat or file with no project is "loose"
    (Claude's default — a conversation that doesn't belong to any project yet).

Design choices, and why:
  * SQLite, stdlib only. One local box, light concurrency, no service to run. The
    file metadata lives in the DB; the BYTES live on disk under data/files/<id>/<name>
    so the DB stays small and tools can be handed a real path to a real datasheet.
  * Every call opens its own connection (WAL + foreign_keys ON). No shared cursor to
    get tangled across the bridge's threadpool. Cheap enough at this scale.
  * Functions return plain JSON-serializable dicts, so a FastAPI route can return the
    result straight through with no marshalling layer.
  * Storage only. Prompt assembly (system message, project instructions injection)
    is NOT done here — that's the bridge's job in layer 2. history_for_model() is the
    one convenience that reaches toward the model, and it only filters/orders rows.

Location is read from $XORICS_DATA_DIR (default ~/xorics-ai/data) on EVERY call, not
at import, so tests can point it at a tmpdir and the bridge can set it at launch
without import-order surprises.

    import store; store.init()              # idempotent; safe to call on every boot
"""

import os
import time
import uuid
import shutil
import sqlite3
from pathlib import Path
from contextlib import contextmanager

# Sentinel so callers can distinguish "filter not given" (any project) from
# "project_id is None" (loose only). Plain None can't carry both meanings.
_UNSET = object()


# --- locations (lazy: read env every call) -----------------------------------
def _data_dir() -> Path:
    return Path(os.environ.get("XORICS_DATA_DIR", Path.home() / "xorics-ai" / "data"))


def _db_path() -> Path:
    return _data_dir() / "xorics.db"


def _files_dir() -> Path:
    return _data_dir() / "files"


# --- small helpers -----------------------------------------------------------
def _now() -> float:
    return time.time()


def _id() -> str:
    return uuid.uuid4().hex


def _row(r):
    return dict(r) if r is not None else None


def _rows(rs):
    return [dict(r) for r in rs]


def _norm_folder(folder: str) -> str:
    """'datasheets' -> '/datasheets'; '' / None -> '/'; '/a/b/' -> '/a/b'."""
    f = (folder or "/").strip()
    if not f.startswith("/"):
        f = "/" + f
    while "//" in f:
        f = f.replace("//", "/")
    if len(f) > 1 and f.endswith("/"):
        f = f[:-1]
    return f or "/"


@contextmanager
def _conn():
    _data_dir().mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(_db_path())
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _touch_project(c, pid: str, now: float):
    if pid:
        c.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, pid))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  instructions TEXT NOT NULL DEFAULT '',
  created_at   REAL NOT NULL,
  updated_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chats(
  id          TEXT PRIMARY KEY,
  project_id  TEXT,
  title       TEXT NOT NULL DEFAULT 'New chat',
  archived    INTEGER NOT NULL DEFAULT 0,
  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS messages(
  id          TEXT PRIMARY KEY,
  chat_id     TEXT NOT NULL,
  role        TEXT NOT NULL,
  content     TEXT NOT NULL,
  built_path  TEXT,
  created_at  REAL NOT NULL,
  FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS files(
  id          TEXT PRIMARY KEY,
  project_id  TEXT,
  folder      TEXT NOT NULL DEFAULT '/',
  name        TEXT NOT NULL,
  size        INTEGER NOT NULL DEFAULT 0,
  mime        TEXT NOT NULL DEFAULT '',
  stored_path TEXT NOT NULL,
  created_at  REAL NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_chats_project ON chats(project_id);
CREATE INDEX IF NOT EXISTS idx_msg_chat      ON messages(chat_id);
CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id);
"""


def init() -> None:
    """Create schema + files dir. Idempotent — call on every boot."""
    with _conn() as c:
        c.executescript(_SCHEMA)
    _files_dir().mkdir(parents=True, exist_ok=True)


# === projects ================================================================
def create_project(name: str, instructions: str = "") -> dict:
    pid, now = _id(), _now()
    with _conn() as c:
        c.execute("INSERT INTO projects(id,name,instructions,created_at,updated_at)"
                  " VALUES(?,?,?,?,?)", (pid, name, instructions, now, now))
    return get_project(pid)


def get_project(pid: str):
    with _conn() as c:
        return _row(c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())


def list_projects() -> list:
    with _conn() as c:
        return _rows(c.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall())


def update_project(pid: str, name=None, instructions=None) -> dict:
    sets, vals = [], []
    if name is not None:
        sets.append("name=?"); vals.append(name)
    if instructions is not None:
        sets.append("instructions=?"); vals.append(instructions)
    sets.append("updated_at=?"); vals.append(_now())
    vals.append(pid)
    with _conn() as c:
        c.execute(f"UPDATE projects SET {','.join(sets)} WHERE id=?", vals)
    return get_project(pid)


def delete_project(pid: str, delete_chats: bool = False) -> None:
    """Remove a project. By default its chats/files survive as loose (project_id->NULL
    via FK); pass delete_chats=True to drop the conversations too (messages cascade).
    File BYTES are never auto-deleted here — only the project row."""
    with _conn() as c:
        if delete_chats:
            c.execute("DELETE FROM chats WHERE project_id=?", (pid,))  # messages cascade
        c.execute("DELETE FROM projects WHERE id=?", (pid,))


# === chats ===================================================================
def create_chat(title: str = "New chat", project_id=None) -> dict:
    cid, now = _id(), _now()
    with _conn() as c:
        c.execute("INSERT INTO chats(id,project_id,title,created_at,updated_at)"
                  " VALUES(?,?,?,?,?)", (cid, project_id, title, now, now))
        _touch_project(c, project_id, now)
    return get_chat(cid)


def get_chat(cid: str):
    with _conn() as c:
        return _row(c.execute("SELECT * FROM chats WHERE id=?", (cid,)).fetchone())


def list_chats(project_id=_UNSET, include_archived: bool = False) -> list:
    """No arg -> every chat. project_id=<id> -> that project. project_id=None -> loose."""
    cond, vals = [], []
    if project_id is not _UNSET:
        if project_id is None:
            cond.append("project_id IS NULL")
        else:
            cond.append("project_id=?"); vals.append(project_id)
    if not include_archived:
        cond.append("archived=0")
    q = "SELECT * FROM chats"
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY updated_at DESC"
    with _conn() as c:
        return _rows(c.execute(q, vals).fetchall())


def rename_chat(cid: str, title: str) -> dict:
    with _conn() as c:
        c.execute("UPDATE chats SET title=?, updated_at=? WHERE id=?", (title, _now(), cid))
    return get_chat(cid)


def move_chat(cid: str, project_id) -> dict:
    now = _now()
    with _conn() as c:
        c.execute("UPDATE chats SET project_id=?, updated_at=? WHERE id=?", (project_id, now, cid))
        _touch_project(c, project_id, now)
    return get_chat(cid)


def set_archived(cid: str, archived: bool = True) -> dict:
    with _conn() as c:
        c.execute("UPDATE chats SET archived=?, updated_at=? WHERE id=?",
                  (1 if archived else 0, _now(), cid))
    return get_chat(cid)


def delete_chat(cid: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM chats WHERE id=?", (cid,))  # messages cascade


# === messages ================================================================
def add_message(cid: str, role: str, content: str, built_path=None) -> dict:
    """Append a turn and float the chat (and its project) to the top of recents."""
    mid, now = _id(), _now()
    with _conn() as c:
        c.execute("INSERT INTO messages(id,chat_id,role,content,built_path,created_at)"
                  " VALUES(?,?,?,?,?,?)", (mid, cid, role, content, built_path, now))
        c.execute("UPDATE chats SET updated_at=? WHERE id=?", (now, cid))
        row = c.execute("SELECT project_id FROM chats WHERE id=?", (cid,)).fetchone()
        if row and row["project_id"]:
            _touch_project(c, row["project_id"], now)
    return get_message(mid)


def get_message(mid: str):
    with _conn() as c:
        return _row(c.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone())


def get_messages(cid: str) -> list:
    with _conn() as c:
        return _rows(c.execute("SELECT * FROM messages WHERE chat_id=? ORDER BY created_at ASC,"
                               " rowid ASC", (cid,)).fetchall())


def history_for_model(cid: str, limit=None) -> list:
    """The chat's turns as [{role, content}, ...] for feeding ask() — user/assistant
    only, oldest first. limit keeps the most recent N. No system prompt here; the
    bridge builds that in layer 2."""
    msgs = get_messages(cid)
    hist = [{"role": m["role"], "content": m["content"]}
            for m in msgs if m["role"] in ("user", "assistant")]
    if limit:
        hist = hist[-limit:]
    return hist


# === files ===================================================================
def add_file(name: str, data: bytes, project_id=None, folder: str = "/", mime: str = "") -> dict:
    """Store bytes under data/files/<id>/<name> and record metadata. The on-disk name
    matches the original so a path can be handed straight to a tool."""
    fid, now = _id(), _now()
    folder = _norm_folder(folder)
    dest_dir = _files_dir() / fid
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    dest.write_bytes(data)
    with _conn() as c:
        c.execute("INSERT INTO files(id,project_id,folder,name,size,mime,stored_path,created_at)"
                  " VALUES(?,?,?,?,?,?,?,?)",
                  (fid, project_id, folder, name, len(data), mime, str(dest), now))
        _touch_project(c, project_id, now)
    return get_file(fid)


def get_file(fid: str):
    with _conn() as c:
        return _row(c.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone())


def list_files(project_id=_UNSET, folder=None) -> list:
    """No arg -> all files. project_id=<id>/None like chats. folder filters to one dir."""
    cond, vals = [], []
    if project_id is not _UNSET:
        if project_id is None:
            cond.append("project_id IS NULL")
        else:
            cond.append("project_id=?"); vals.append(project_id)
    if folder is not None:
        cond.append("folder=?"); vals.append(_norm_folder(folder))
    q = "SELECT * FROM files"
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY folder ASC, name ASC"
    with _conn() as c:
        return _rows(c.execute(q, vals).fetchall())


def list_folders(project_id=_UNSET) -> list:
    """Distinct folder paths — the directory set the file explorer renders as a tree."""
    cond, vals = [], []
    if project_id is not _UNSET:
        if project_id is None:
            cond.append("project_id IS NULL")
        else:
            cond.append("project_id=?"); vals.append(project_id)
    q = "SELECT DISTINCT folder FROM files"
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY folder ASC"
    with _conn() as c:
        return [r["folder"] for r in c.execute(q, vals).fetchall()]


def read_file_bytes(fid: str) -> bytes:
    f = get_file(fid)
    if not f:
        raise KeyError(f"no file {fid}")
    return Path(f["stored_path"]).read_bytes()


def rename_file(fid: str, name: str) -> dict:
    f = get_file(fid)
    if not f:
        raise KeyError(f"no file {fid}")
    old = Path(f["stored_path"])
    new = old.parent / name
    if old.exists():
        shutil.move(str(old), str(new))
    with _conn() as c:
        c.execute("UPDATE files SET name=?, stored_path=? WHERE id=?", (name, str(new), fid))
    return get_file(fid)


def move_file(fid: str, folder=None, project_id=_UNSET) -> dict:
    sets, vals = [], []
    if folder is not None:
        sets.append("folder=?"); vals.append(_norm_folder(folder))
    if project_id is not _UNSET:
        sets.append("project_id=?"); vals.append(project_id)
    if not sets:
        return get_file(fid)
    vals.append(fid)
    with _conn() as c:
        c.execute(f"UPDATE files SET {','.join(sets)} WHERE id=?", vals)
    return get_file(fid)


def delete_file(fid: str) -> None:
    f = get_file(fid)
    if not f:
        return
    shutil.rmtree(Path(f["stored_path"]).parent, ignore_errors=True)
    with _conn() as c:
        c.execute("DELETE FROM files WHERE id=?", (fid,))
