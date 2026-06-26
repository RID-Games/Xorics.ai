# Xorics — Layer-1 store tests. Plain-assert, no pytest dependency.
# Run: python3 test_store.py
#
# The store has NO external deps (sqlite3 + stdlib), so unlike the netlist fixtures
# this isn't a shape-mock standing in for reality — it drives the real code against a
# real SQLite file in a throwaway dir. That makes a green run here meaningful. The
# live probe that still matters is on RIDGames: prove the BRIDGE reads/writes this
# store once layer 2 wires it in (#lesson: tests are necessary, not sufficient).

import os
import tempfile
import shutil

# Point the store at a throwaway data dir BEFORE importing it. (store reads the env
# on every call, so this is belt-and-suspenders, but set it up front regardless.)
_TMP = tempfile.mkdtemp(prefix="xorics-store-test-")
os.environ["XORICS_DATA_DIR"] = _TMP

import store

_pass = 0
_fail = 0


def check(label, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  ok   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}")


try:
    store.init()
    store.init()  # idempotent — second call must not blow up
    check("init() is idempotent", True)

    # --- projects -----------------------------------------------------------
    p = store.create_project("Even G2 ALS module", instructions="Na-ion, clip housing")
    check("create_project returns a dict with id", isinstance(p, dict) and bool(p["id"]))
    check("project stores instructions", p["instructions"] == "Na-ion, clip housing")
    p2 = store.create_project("Scratchpad")
    check("list_projects returns both", len(store.list_projects()) == 2)
    store.update_project(p["id"], name="Even G2 ALS")
    check("update_project renames", store.get_project(p["id"])["name"] == "Even G2 ALS")

    # --- chats: loose vs project, the None/_UNSET distinction ----------------
    loose = store.create_chat("Loose thought")
    inproj = store.create_chat("ALS firmware", project_id=p["id"])
    check("create_chat (loose) has null project", loose["project_id"] is None)
    check("create_chat (in project) carries project_id", inproj["project_id"] == p["id"])
    check("list_chats() returns ALL chats", len(store.list_chats()) == 2)
    check("list_chats(project_id=p) filters to project", len(store.list_chats(project_id=p["id"])) == 1)
    check("list_chats(project_id=None) returns only loose", len(store.list_chats(project_id=None)) == 1)
    check("  ...and that loose chat is the right one",
          store.list_chats(project_id=None)[0]["id"] == loose["id"])

    # --- messages + history shape -------------------------------------------
    store.add_message(inproj["id"], "user", "design an ambient light sensor module")
    store.add_message(inproj["id"], "assistant", "Delegating to the coder.", built_path="circuits/als_BUILT.py")
    store.add_message(inproj["id"], "tool", "ERC: 0 errors")  # tool turn must NOT reach the model
    msgs = store.get_messages(inproj["id"])
    check("get_messages returns 3 rows in order", len(msgs) == 3 and msgs[0]["role"] == "user")
    check("built_path persisted on the assistant turn", msgs[1]["built_path"] == "circuits/als_BUILT.py")
    hist = store.history_for_model(inproj["id"])
    check("history_for_model drops the tool turn", len(hist) == 2)
    check("history_for_model is [{role,content}] only",
          set(hist[0].keys()) == {"role", "content"})
    check("history_for_model preserves order (user first)", hist[0]["role"] == "user")
    check("history limit keeps the most recent", store.history_for_model(inproj["id"], limit=1)[0]["role"] == "assistant")

    # --- recency: a new message floats its chat to the top of recents --------
    import time as _t
    _t.sleep(0.01)
    store.add_message(loose["id"], "user", "ping")
    top = store.list_chats()[0]
    check("most-recently-messaged chat sorts first", top["id"] == loose["id"])

    # --- archive / rename / move --------------------------------------------
    store.rename_chat(loose["id"], "Renamed")
    check("rename_chat works", store.get_chat(loose["id"])["title"] == "Renamed")
    store.set_archived(loose["id"], True)
    check("archived chat hidden by default", all(c["id"] != loose["id"] for c in store.list_chats()))
    check("archived chat shown when asked", any(c["id"] == loose["id"] for c in store.list_chats(include_archived=True)))
    store.move_chat(loose["id"], p["id"])
    check("move_chat reassigns project", store.get_chat(loose["id"])["project_id"] == p["id"])

    # --- files: bytes on disk, folders, move/rename --------------------------
    f1 = store.add_file("opt3001.pdf", b"%PDF-fake-datasheet", project_id=p["id"], folder="datasheets", mime="application/pdf")
    f2 = store.add_file("notes.txt", b"hello", project_id=p["id"])  # root folder
    f3 = store.add_file("loose.txt", b"x")  # loose file, no project
    check("add_file normalizes folder to /datasheets", f1["folder"] == "/datasheets")
    check("add_file defaults folder to /", f2["folder"] == "/")
    check("file bytes are readable back", store.read_file_bytes(f1["id"]) == b"%PDF-fake-datasheet")
    check("file size recorded", f1["size"] == len(b"%PDF-fake-datasheet"))
    check("list_files(project) returns project files only", len(store.list_files(project_id=p["id"])) == 2)
    check("list_files(folder) filters to one dir", len(store.list_files(project_id=p["id"], folder="datasheets")) == 1)
    check("list_files(project_id=None) returns loose file", len(store.list_files(project_id=None)) == 1)
    check("list_folders shows the explorer dir set",
          store.list_folders(project_id=p["id"]) == ["/", "/datasheets"])
    store.move_file(f2["id"], folder="archive")
    check("move_file changes folder", store.get_file(f2["id"])["folder"] == "/archive")
    store.rename_file(f1["id"], "OPT3001.pdf")
    rn = store.get_file(f1["id"])
    check("rename_file updates name and moves bytes on disk",
          rn["name"] == "OPT3001.pdf" and os.path.exists(rn["stored_path"]) and store.read_file_bytes(f1["id"]) == b"%PDF-fake-datasheet")

    # --- cascade / referential integrity ------------------------------------
    store.delete_chat(inproj["id"])
    check("delete_chat cascades its messages", store.get_messages(inproj["id"]) == [])

    fpath = store.get_file(f1["id"])["stored_path"]
    store.delete_file(f1["id"])
    check("delete_file removes row and bytes", store.get_file(f1["id"]) is None and not os.path.exists(fpath))

    # deleting a project orphans (not deletes) its chats/files by default
    store.delete_project(p["id"])
    check("delete_project sets chats loose (FK SET NULL)", store.get_chat(loose["id"])["project_id"] is None)
    check("delete_project sets files loose (FK SET NULL)", store.get_file(f2["id"])["project_id"] is None)
    check("project row is gone", store.get_project(p["id"]) is None)

finally:
    shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n{_pass} passed, {_fail} failed")
raise SystemExit(1 if _fail else 0)
