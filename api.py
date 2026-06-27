#!/usr/bin/env python3
# Xorics — app-facing REST API: projects, chats, messages (memory-backed).
# Copyright (C) 2026 Zawayix
#
# This file is part of Xorics, free software under the GNU AGPL v3 or later.
# See <https://www.gnu.org/licenses/>. Designs produced by RUNNING Xorics are
# exempt per LICENSE-EXCEPTION.

"""
The surface the RID Android app talks to so Xorics can be used the way Claude is:
many conversations, each remembering its own turns, grouped into projects.

This is ADDITIVE. The OpenAI-compatible /v1/chat/completions route in bridge.py is
untouched — the G2 glasses / Even Hub still point at it (stateless, one shared brain).
These routes are a separate namespace that persists everything through store.py and
feeds a chat's prior turns back into ask() so the conversation has memory.

Wiring (in bridge.py):
    from api import make_router
    app.include_router(make_router(_run_ask_full, _auth))

make_router takes two INJECTED callables, so this module never imports bridge (which
would be a circular import) and so the single ask()-serializing lock is shared:
    run_ask_full(text, history) -> (reply_text, built_path, deliverables)   # holds bridge's _ASK_LOCK
    auth(request) -> None                                      # raises HTTPException on bad token

Body is read with await request.json() (tolerant of an empty body) to match bridge.py's
existing hand-rolled style rather than introducing Pydantic models. project_id accepts
"", "none", or "null" as "loose" (no project), so the app can pass a plain string field.
"""

import base64
import mimetypes
import os

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

import store


async def _json(request):
    """Body as dict; {} on empty/invalid so a POST with no body never 500s."""
    try:
        return await request.json()
    except Exception:
        return {}


def _loose(pid):
    """Normalize the app's 'no project' sentinels to a real None."""
    return None if pid in ("", "none", "null") else pid


# Logical folder the coder's verified deliverables land in, so they're grouped and distinguishable
# from user uploads in the file explorer. XORICS-FEATURE: deliverables-to-store
_DELIVERABLES_FOLDER = "/deliverables"


def make_router(run_ask_full, auth):
    router = APIRouter()
    store.init()   # idempotent; guarantees schema + files dir exist once the bridge boots

    # ======================= projects ====================================
    @router.get("/v1/projects")
    async def list_projects(request: Request):
        auth(request)
        return {"projects": store.list_projects()}

    @router.post("/v1/projects")
    async def create_project(request: Request):
        auth(request)
        body = await _json(request)
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="no 'name'")
        return store.create_project(name, body.get("instructions") or "")

    @router.get("/v1/projects/{project_id}")
    async def get_project(project_id: str, request: Request):
        auth(request)
        p = store.get_project(project_id)
        if not p:
            raise HTTPException(status_code=404, detail="no such project")
        return p

    @router.patch("/v1/projects/{project_id}")
    async def update_project(project_id: str, request: Request):
        auth(request)
        if not store.get_project(project_id):
            raise HTTPException(status_code=404, detail="no such project")
        body = await _json(request)
        return store.update_project(project_id, name=body.get("name"),
                                    instructions=body.get("instructions"))

    @router.delete("/v1/projects/{project_id}")
    async def delete_project(project_id: str, request: Request):
        auth(request)
        body = await _json(request)
        store.delete_project(project_id, delete_chats=bool(body.get("delete_chats")))
        return {"deleted": project_id}

    # ======================= chats =======================================
    @router.get("/v1/chats")
    async def list_chats(request: Request):
        auth(request)
        qp = request.query_params
        include_archived = qp.get("include_archived", "false").lower() == "true"
        if "project_id" not in qp:                       # omitted -> every chat
            chats = store.list_chats(include_archived=include_archived)
        else:                                            # given (incl. "none" -> loose)
            chats = store.list_chats(project_id=_loose(qp.get("project_id")),
                                     include_archived=include_archived)
        return {"chats": chats}

    @router.post("/v1/chats")
    async def create_chat(request: Request):
        auth(request)
        body = await _json(request)
        return store.create_chat(title=(body.get("title") or "New chat"),
                                 project_id=_loose(body.get("project_id")))

    @router.get("/v1/chats/{chat_id}")
    async def get_chat(chat_id: str, request: Request):
        auth(request)
        c = store.get_chat(chat_id)
        if not c:
            raise HTTPException(status_code=404, detail="no such chat")
        return c

    @router.patch("/v1/chats/{chat_id}")
    async def update_chat(chat_id: str, request: Request):
        auth(request)
        if not store.get_chat(chat_id):
            raise HTTPException(status_code=404, detail="no such chat")
        body = await _json(request)
        if body.get("title"):
            store.rename_chat(chat_id, body["title"])
        if "project_id" in body:
            store.move_chat(chat_id, _loose(body["project_id"]))
        if "archived" in body:
            store.set_archived(chat_id, bool(body["archived"]))
        return store.get_chat(chat_id)

    @router.delete("/v1/chats/{chat_id}")
    async def delete_chat(chat_id: str, request: Request):
        auth(request)
        store.delete_chat(chat_id)
        return {"deleted": chat_id}

    # ======================= messages (the memory route) =================
    @router.get("/v1/chats/{chat_id}/messages")
    async def get_messages(chat_id: str, request: Request):
        auth(request)
        if not store.get_chat(chat_id):
            raise HTTPException(status_code=404, detail="no such chat")
        return {"messages": store.get_messages(chat_id)}

    @router.post("/v1/chats/{chat_id}/messages")
    async def post_message(chat_id: str, request: Request):
        auth(request)
        chat = store.get_chat(chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail="no such chat")
        body = await _json(request)
        content = (body.get("content") or "").strip()
        if not content:
            raise HTTPException(status_code=400, detail="no 'content'")

        # history = the turns BEFORE this one. ask() appends the new turn itself, so
        # we read history FIRST, then store the user turn, then ask.
        history = store.history_for_model(chat_id)
        first_turn = len(history) == 0
        user_msg = store.add_message(chat_id, "user", content)

        text, built, deliverables = await run_in_threadpool(run_ask_full, content, history)
        asst_msg = store.add_message(chat_id, "assistant", text, built_path=built)

        # Mirror any deliverable the coder verified to disk THIS turn into the project file store, so
        # it shows up in the app's file explorer — the coder saves to sketches/ or circuits/, which the
        # app never sees otherwise. Files are project-scoped; a loose chat -> loose files. Skip-if-name-
        # exists stops a re-run of the same task from spamming duplicates. Best-effort: a mirror failure
        # must never fail the chat turn (the file still exists on disk regardless). XORICS-FEATURE: deliverables-to-store
        saved_files = []
        existing = ({f["name"] for f in store.list_files(project_id=chat.get("project_id"),
                                                         folder=_DELIVERABLES_FOLDER)}
                    if deliverables else set())
        for path in deliverables:
            name = os.path.basename(path)
            if name in existing:
                continue
            try:
                with open(os.path.expanduser(path), "rb") as fh:
                    raw = fh.read()
                saved_files.append(store.add_file(name, raw, project_id=chat.get("project_id"),
                                                  folder=_DELIVERABLES_FOLDER,
                                                  mime=mimetypes.guess_type(name)[0] or "text/plain"))
                existing.add(name)
            except Exception as e:
                print(f"  [api] could not mirror deliverable {name} to store: {e}")

        # Name a brand-new chat after its first message so the history list isn't all "New chat".
        if first_turn and chat["title"] == "New chat":
            store.rename_chat(chat_id, content[:60])

        return {"user_message": user_msg,
                "assistant_message": asst_msg,
                "files": saved_files,
                "chat": store.get_chat(chat_id)}

    # ======================= files (the file-explorer backend) ===========
    # Persistent storage, distinct from /v1/upload in bridge.py (which is ephemeral
    # "analyze this now"). These files are project knowledge: stored, listed, organized.
    @router.post("/v1/files")
    async def upload_file(request: Request):
        auth(request)
        body = await _json(request)
        filename = (body.get("filename") or "").strip()
        data_b64 = body.get("data")
        if not filename or data_b64 is None:
            raise HTTPException(status_code=400, detail="need 'filename' and base64 'data'")
        try:
            raw = base64.b64decode(data_b64)        # lenient: tolerates wrapped base64
        except Exception:
            raise HTTPException(status_code=400, detail="'data' is not valid base64")
        return store.add_file(filename, raw,
                              project_id=_loose(body.get("project_id")),
                              folder=body.get("folder") or "/",
                              mime=body.get("mime") or "")

    @router.get("/v1/files")
    async def list_files(request: Request):
        auth(request)
        qp = request.query_params
        folder = qp.get("folder")                       # None -> no folder filter
        if "project_id" not in qp:
            files = store.list_files(folder=folder)
        else:
            files = store.list_files(project_id=_loose(qp.get("project_id")), folder=folder)
        return {"files": files}

    @router.get("/v1/folders")
    async def list_folders(request: Request):
        auth(request)
        qp = request.query_params
        if "project_id" not in qp:
            return {"folders": store.list_folders()}
        return {"folders": store.list_folders(project_id=_loose(qp.get("project_id")))}

    @router.get("/v1/files/{file_id}")
    async def get_file_meta(file_id: str, request: Request):
        auth(request)
        f = store.get_file(file_id)
        if not f:
            raise HTTPException(status_code=404, detail="no such file")
        return f

    @router.get("/v1/files/{file_id}/content")
    async def download_file(file_id: str, request: Request):
        auth(request)
        f = store.get_file(file_id)
        if not f:
            raise HTTPException(status_code=404, detail="no such file")
        raw = store.read_file_bytes(file_id)
        return Response(content=raw,
                        media_type=f["mime"] or "application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{f["name"]}"'})

    @router.patch("/v1/files/{file_id}")
    async def update_file(file_id: str, request: Request):
        auth(request)
        if not store.get_file(file_id):
            raise HTTPException(status_code=404, detail="no such file")
        body = await _json(request)
        if body.get("name"):
            store.rename_file(file_id, body["name"])
        if "folder" in body or "project_id" in body:    # move (folder and/or project)
            kwargs = {}
            if "folder" in body:
                kwargs["folder"] = body["folder"]
            if "project_id" in body:
                kwargs["project_id"] = _loose(body["project_id"])
            store.move_file(file_id, **kwargs)
        return store.get_file(file_id)

    @router.delete("/v1/files/{file_id}")
    async def delete_file(file_id: str, request: Request):
        auth(request)
        store.delete_file(file_id)
        return {"deleted": file_id}

    return router
