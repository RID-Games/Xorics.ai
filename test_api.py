# Xorics — app API tests. Drives the REAL router (api.make_router) over a REAL store
# in a throwaway dir, with the model stubbed (so no GPU) and auth a no-op. The stub
# RECORDS the history it's handed, so we prove the memory wiring — that each turn sees
# the prior turns and not itself. The live proof (a real reply that remembers) is the
# curl probe on RIDGames after the bridge restarts. A green TestClient run != that.

import os, tempfile, shutil, base64
_TMP = tempfile.mkdtemp(prefix="xorics-api-test-")
os.environ["XORICS_DATA_DIR"] = _TMP

from fastapi import FastAPI
from fastapi.testclient import TestClient
import api

_pass = _fail = 0
def check(label, cond):
    global _pass, _fail
    if cond: _pass += 1; print(f"  ok   {label}")
    else:    _fail += 1; print(f"  FAIL {label}")

# Stubbed model: record each call's history; return (reply, built_path=None, deliverables=[]) to
# match _run_ask_full's 3-tuple contract.
SEEN = []
def fake_run_ask(text, history):
    SEEN.append({"text": text, "hist_len": len(history), "hist": list(history)})
    return (f"reply#{len(SEEN)} to {text!r}", None, [])

def no_auth(request):
    return None

app = FastAPI()
app.include_router(api.make_router(fake_run_ask, no_auth))
c = TestClient(app)

try:
    # ---- projects ----------------------------------------------------------
    r = c.post("/v1/projects", json={"name": "Even G2 ALS", "instructions": "Na-ion"})
    check("POST /v1/projects -> 200", r.status_code == 200)
    pid = r.json()["id"]
    check("project carries instructions", r.json()["instructions"] == "Na-ion")
    check("POST /v1/projects with no name -> 400", c.post("/v1/projects", json={}).status_code == 400)
    check("GET /v1/projects lists it", any(p["id"] == pid for p in c.get("/v1/projects").json()["projects"]))
    check("GET /v1/projects/{id} -> 200", c.get(f"/v1/projects/{pid}").status_code == 200)
    check("GET unknown project -> 404", c.get("/v1/projects/nope").status_code == 404)
    check("PATCH renames project", c.patch(f"/v1/projects/{pid}", json={"name": "ALS"}).json()["name"] == "ALS")

    # ---- chats: loose vs project, filters ----------------------------------
    loose = c.post("/v1/chats", json={}).json()
    check("POST /v1/chats (empty body) -> loose chat", loose["project_id"] is None and loose["title"] == "New chat")
    inproj = c.post("/v1/chats", json={"title": "firmware", "project_id": pid}).json()
    check("POST /v1/chats in project carries project_id", inproj["project_id"] == pid)
    check("GET /v1/chats (no filter) -> all", len(c.get("/v1/chats").json()["chats"]) == 2)
    check("GET /v1/chats?project_id=<id> filters", len(c.get(f"/v1/chats?project_id={pid}").json()["chats"]) == 1)
    check("GET /v1/chats?project_id=none -> loose only", len(c.get("/v1/chats?project_id=none").json()["chats"]) == 1)

    # Use a fresh DEFAULT-titled chat for the memory + auto-title flow (auto-title only
    # fires while a chat is still "New chat" — inproj above was given an explicit title).
    mem = c.post("/v1/chats", json={}).json()
    cid = mem["id"]
    check("new chat starts titled 'New chat'", mem["title"] == "New chat")
    check("GET messages (empty) -> []", c.get(f"/v1/chats/{cid}/messages").json()["messages"] == [])

    # ---- the memory route: history grows, new turn never in the history sent
    r1 = c.post(f"/v1/chats/{cid}/messages", json={"content": "design an ALS module"})
    check("POST message -> 200", r1.status_code == 200)
    check("response carries both turns", r1.json()["user_message"]["role"] == "user" and r1.json()["assistant_message"]["role"] == "assistant")
    check("turn 1: model saw EMPTY history", SEEN[-1]["hist_len"] == 0)
    check("auto-title: new chat named after first message", r1.json()["chat"]["title"] == "design an ALS module")

    r2 = c.post(f"/v1/chats/{cid}/messages", json={"content": "what sensor?"})
    check("turn 2: model saw 2 prior turns (user+assistant)", SEEN[-1]["hist_len"] == 2)
    check("turn 2: prior history is [user, assistant] in order",
          [m["role"] for m in SEEN[-1]["hist"]] == ["user", "assistant"])
    check("turn 2: the NEW message is NOT in the history sent",
          all(m["content"] != "what sensor?" for m in SEEN[-1]["hist"]))

    r3 = c.post(f"/v1/chats/{cid}/messages", json={"content": "and the battery?"})
    check("turn 3: model saw 4 prior turns", SEEN[-1]["hist_len"] == 4)
    check("auto-title does NOT overwrite on later turns", r3.json()["chat"]["title"] == "design an ALS module")

    msgs = c.get(f"/v1/chats/{cid}/messages").json()["messages"]
    check("all 6 turns persisted (3 user + 3 assistant)", len(msgs) == 6)
    check("persisted assistant text matches the stub's reply", msgs[1]["content"].startswith("reply#1"))

    # ---- validation / 404s -------------------------------------------------
    check("POST empty content -> 400", c.post(f"/v1/chats/{cid}/messages", json={"content": "  "}).status_code == 400)
    check("POST to unknown chat -> 404", c.post("/v1/chats/ghost/messages", json={"content": "hi"}).status_code == 404)
    check("GET messages of unknown chat -> 404", c.get("/v1/chats/ghost/messages").status_code == 404)

    # ---- patch chat: rename / move-to-loose / archive ----------------------
    check("PATCH rename chat", c.patch(f"/v1/chats/{cid}", json={"title": "renamed"}).json()["title"] == "renamed")
    check("PATCH move chat to loose", c.patch(f"/v1/chats/{cid}", json={"project_id": "none"}).json()["project_id"] is None)
    c.patch(f"/v1/chats/{cid}", json={"archived": True})
    check("archived chat hidden from default list", all(x["id"] != cid for x in c.get("/v1/chats").json()["chats"]))
    check("archived chat shown with include_archived", any(x["id"] == cid for x in c.get("/v1/chats?include_archived=true").json()["chats"]))

    # ---- delete chat cascades messages -------------------------------------
    c.delete(f"/v1/chats/{cid}")
    check("DELETE chat -> gone (404 after)", c.get(f"/v1/chats/{cid}").status_code == 404)

    # ---- delete project (default: orphan its chats, don't delete them) ------
    proj_chat = c.post("/v1/chats", json={"project_id": pid}).json()
    c.delete(f"/v1/projects/{pid}")
    check("DELETE project orphans its chat to loose", c.get(f"/v1/chats/{proj_chat['id']}").json()["project_id"] is None)
    check("DELETE project -> project 404 after", c.get(f"/v1/projects/{pid}").status_code == 404)

    # ======================= files =========================================
    fpid = c.post("/v1/projects", json={"name": "Files proj"}).json()["id"]
    blob = b"%PDF-1.7 fake datasheet bytes"
    b64 = base64.b64encode(blob).decode()

    up = c.post("/v1/files", json={"filename": "opt3001.pdf", "data": b64,
                                   "project_id": fpid, "folder": "datasheets", "mime": "application/pdf"})
    check("POST /v1/files -> 200", up.status_code == 200)
    fid = up.json()["id"]
    check("upload: folder normalized to /datasheets", up.json()["folder"] == "/datasheets")
    check("upload: size recorded from decoded bytes", up.json()["size"] == len(blob))
    check("POST /v1/files missing data -> 400", c.post("/v1/files", json={"filename": "x"}).status_code == 400)
    check("POST /v1/files bad base64 -> 400", c.post("/v1/files", json={"filename": "x", "data": "a"}).status_code == 400)

    # a second file in the project root, and a loose file (no project)
    c.post("/v1/files", json={"filename": "notes.txt", "data": base64.b64encode(b"hi").decode(), "project_id": fpid})
    c.post("/v1/files", json={"filename": "loose.bin", "data": base64.b64encode(b"x").decode()})

    check("GET /v1/files (no filter) -> all 3", len(c.get("/v1/files").json()["files"]) == 3)
    check("GET /v1/files?project_id=<id> -> 2", len(c.get(f"/v1/files?project_id={fpid}").json()["files"]) == 2)
    check("GET /v1/files?project_id=none -> 1 loose", len(c.get("/v1/files?project_id=none").json()["files"]) == 1)
    check("GET /v1/files?folder=datasheets -> 1", len(c.get(f"/v1/files?project_id={fpid}&folder=datasheets").json()["files"]) == 1)
    check("GET /v1/folders shows the tree dirs",
          c.get(f"/v1/folders?project_id={fpid}").json()["folders"] == ["/", "/datasheets"])

    check("GET /v1/files/{id} metadata -> 200", c.get(f"/v1/files/{fid}").status_code == 200)
    check("GET unknown file -> 404", c.get("/v1/files/ghost").status_code == 404)

    dl = c.get(f"/v1/files/{fid}/content")
    check("GET /content returns the exact bytes", dl.content == blob)
    check("GET /content sets a download filename", "opt3001.pdf" in dl.headers.get("content-disposition", ""))
    check("GET /content of unknown file -> 404", c.get("/v1/files/ghost/content").status_code == 404)

    check("PATCH move file to another folder", c.patch(f"/v1/files/{fid}", json={"folder": "archive"}).json()["folder"] == "/archive")
    check("PATCH move file to loose (no project)", c.patch(f"/v1/files/{fid}", json={"project_id": "none"}).json()["project_id"] is None)
    rn = c.patch(f"/v1/files/{fid}", json={"name": "OPT3001.pdf"}).json()
    check("PATCH rename file updates name", rn["name"] == "OPT3001.pdf")
    check("renamed file still downloads the same bytes", c.get(f"/v1/files/{fid}/content").content == blob)

    c.delete(f"/v1/files/{fid}")
    check("DELETE file -> 404 after", c.get(f"/v1/files/{fid}").status_code == 404)

finally:
    shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n{_pass} passed, {_fail} failed")
raise SystemExit(1 if _fail else 0)
