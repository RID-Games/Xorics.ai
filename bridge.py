#!/usr/bin/env python3
# Xorics — OpenAI-compatible chat bridge (voice surface, Stage 1).
# Copyright (C) 2026 Zawayix
#
# This file is part of Xorics, free software under the GNU AGPL v3 or later.
# See <https://www.gnu.org/licenses/>. Designs produced by RUNNING Xorics are
# exempt per LICENSE-EXCEPTION.

"""
Stage 1 of the voice surface: expose Xorics's manager loop as an OpenAI-compatible
/v1/chat/completions endpoint. This is the *shared core*:

  - The Even Realities G2 glasses point Even Hub's "Add Agent" (Name / URL / Token)
    straight at it. They do speech->text on-device and send transcribed TEXT.
  - The phone voice page (Stage 2) wraps this SAME endpoint with whisper (STT) and
    Kokoro (TTS) bookends.

What the G2 sends (reverse-engineered; there are no public docs for the feature):
    POST <your Agent URL>
    Authorization: Bearer <token>
    {"model": "...", "messages": [{"role": "user", "content": "..."}]}
We ignore `model`, take the last user message, run ask(), and return a standard
chat.completion. Non-streaming — fine for a glasses display.

Run (from ~/xorics-ai, venv active):
    pip install fastapi uvicorn          # one-time, into the venv
    uvicorn bridge:app --host 127.0.0.1 --port 8090

Prove it locally before exposing anything:
    curl -s http://127.0.0.1:8090/v1/chat/completions \
      -H "Authorization: Bearer test" -H "Content-Type: application/json" \
      -d '{"model":"xorics","messages":[{"role":"user","content":"say hi in five words"}]}'

Expose over Tailscale HTTPS (this also gives the phone mic its required secure context):
    tailscale serve --bg 8090            # verify the flag against your tailscale version

Even Hub -> Add Agent:
    URL   = https://ridgames.<tailnet>.ts.net/v1/chat/completions
    Token = whatever you set in XORICS_BRIDGE_TOKEN (or anything, if it's unset)
"""

import os
import time
import uuid
import threading

from fastapi import FastAPI, Request, HTTPException
from starlette.concurrency import run_in_threadpool

import xorics   # pulls in the whole assistant; the REPL is __main__-guarded, so import is safe

# --- one-time setup ----------------------------------------------------------
# Glasses/phone talk to the MANAGER; it routes to the coder itself when a request
# actually needs code.
xorics.BRAIN = xorics.MANAGER

# Continue the saved conversation instead of clobbering it. ask() persists the
# transcript via the memory layer (personality branch); loading it first means the
# bridge APPENDS rather than overwriting state/chat_history.json. No-op on branches
# without the memory layer.
#   NOTE: this makes every surface share ONE Xorics conversation (glasses + phone +
#   terminal interleave). Fine for a single user; revisit if you want per-surface
#   threads.
if hasattr(xorics, "_CHAT_HISTORY") and hasattr(xorics, "_load_history"):
    xorics._CHAT_HISTORY[:] = xorics._load_history()

# ask() drives a single global brain over llama-swap. Serialize so two requests
# can't race BRAIN or thrash the GPU swap.
_ASK_LOCK = threading.Lock()

# Optional shared secret. If unset, any token is accepted (fine behind a tailnet).
_TOKEN = os.environ.get("XORICS_BRIDGE_TOKEN")

app = FastAPI(title="Xorics bridge")


def _extract_user_text(messages):
    """Last user turn -> plain string. The G2 sends string content; tolerate the
    multimodal list form by keeping only text parts."""
    for msg in reversed(messages or []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ).strip()
    return ""


def _completion(text, model):
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model or "xorics",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _run_ask(text):
    # One ask() at a time. Can be slow: a delegate_to_coder run is minutes + two GPU
    # swaps — the glasses/Even Hub will likely time out on those. Handling long tasks
    # (fast-path + the glasses brevity shim) is a later surface concern, not the bridge.
    with _ASK_LOCK:
        return str(xorics.ask(text))


async def _chat(request: Request):
    if _TOKEN is not None:
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if token != _TOKEN:
            raise HTTPException(status_code=401, detail="bad token")

    body = await request.json()
    user_text = _extract_user_text(body.get("messages"))
    if not user_text:
        raise HTTPException(status_code=400, detail="no user message in 'messages'")

    # `stream: true` is ignored for now — we always return a full completion. SSE is a
    # follow-up if a client ever requires it (the G2 capture didn't set it).
    text = await run_in_threadpool(_run_ask, user_text)
    return _completion(text, body.get("model"))


# The G2 POSTs to whatever path you set as the Agent URL. Expose the canonical OpenAI
# path and also bare root, so either URL works.
app.post("/v1/chat/completions")(_chat)
app.post("/")(_chat)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/v1/models")
async def models():
    # Some OpenAI clients probe this; the G2 doesn't need it.
    return {"object": "list", "data": [{"id": "xorics", "object": "model", "owned_by": "rid"}]}
