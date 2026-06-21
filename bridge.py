#!/usr/bin/env python3
# Xorics — OpenAI-compatible chat bridge + voice surface.
# Copyright (C) 2026 Zawayix
#
# This file is part of Xorics, free software under the GNU AGPL v3 or later.
# See <https://www.gnu.org/licenses/>. Designs produced by RUNNING Xorics are
# exempt per LICENSE-EXCEPTION.

"""
One FastAPI app, two surfaces over the same chat core:

  /v1/chat/completions  - OpenAI-compatible. The Even Realities G2 glasses point
                          Even Hub's "Add Agent" straight at it (text in, text out).
  /                     - push-to-talk voice page for the phone browser.
  /stt                  - audio in  -> ffmpeg (16kHz mono) -> whisper :8084 -> {text}
  /tts                  - {text} in -> Kokoro :8880 (af_heart, wav) -> audio bytes

Phone voice flow (the page orchestrates): mic -> /stt -> /v1/chat/completions -> /tts -> play.
whisper was built without --convert, so /stt MUST resample to 16kHz mono first; that same
ffmpeg step also turns the browser's webm/opus into something whisper accepts.

Run (from ~/xorics-ai, venv active; no new pip installs needed — httpx ships with openai):
    uvicorn bridge:app --host 127.0.0.1 --port 8090
Already exposed via `tailscale serve --bg 8090`, so the page is at
    https://ridgames.<tailnet>.ts.net/
on any tailnet device. Open it on the phone, allow the mic, tap Talk.

Config (env, all optional):
    XORICS_BRIDGE_TOKEN  shared bearer token (unset = any token accepted; tailnet still gates)
    XORICS_WHISPER_URL   default http://127.0.0.1:8084/inference
    XORICS_KOKORO_URL    default http://127.0.0.1:8880/v1/audio/speech
    XORICS_TTS_VOICE     default af_heart
"""

import os
import time
import uuid
import threading
import tempfile
import subprocess

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool

import xorics   # pulls in the whole assistant; the REPL is __main__-guarded, so import is safe

# --- one-time setup ----------------------------------------------------------
xorics.BRAIN = xorics.MANAGER   # glasses/phone talk to the manager; it routes to the coder itself

# Continue the saved conversation instead of clobbering it (no-op without the memory layer).
if hasattr(xorics, "_CHAT_HISTORY") and hasattr(xorics, "_load_history"):
    xorics._CHAT_HISTORY[:] = xorics._load_history()

_ASK_LOCK = threading.Lock()    # ask() drives one global brain over llama-swap — serialize
_TOKEN = os.environ.get("XORICS_BRIDGE_TOKEN")

WHISPER_URL = os.environ.get("XORICS_WHISPER_URL", "http://127.0.0.1:8084/inference")
KOKORO_URL = os.environ.get("XORICS_KOKORO_URL", "http://127.0.0.1:8880/v1/audio/speech")
TTS_VOICE = os.environ.get("XORICS_TTS_VOICE", "bm_fable")

app = FastAPI(title="Xorics bridge")


def _auth(request: Request):
    if _TOKEN is not None:
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if token != _TOKEN:
            raise HTTPException(status_code=401, detail="bad token")


# --- chat core (OpenAI-compatible) -------------------------------------------
def _extract_user_text(messages):
    """Last user turn -> plain string. Tolerate the multimodal list form."""
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
    # One ask() at a time. A delegate_to_coder run is minutes + two GPU swaps — fine over
    # the phone (it waits), but the glasses/Even Hub will time out on those.
    with _ASK_LOCK:
        return str(xorics.ask(text))


async def _chat(request: Request):
    _auth(request)
    body = await request.json()
    user_text = _extract_user_text(body.get("messages"))
    if not user_text:
        raise HTTPException(status_code=400, detail="no user message in 'messages'")
    text = await run_in_threadpool(_run_ask, user_text)
    return _completion(text, body.get("model"))


# --- voice: STT (whisper) and TTS (Kokoro) -----------------------------------
def _transcribe(audio_bytes):
    """Browser audio -> 16kHz mono wav (ffmpeg) -> whisper /inference -> text."""
    in_path = out_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".in", delete=False) as f:
            f.write(audio_bytes)
            in_path = f.name
        out_path = in_path + ".wav"
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", in_path, "-ar", "16000", "-ac", "1", out_path],
            capture_output=True,
        )
        if proc.returncode != 0:
            raise HTTPException(status_code=500,
                                detail="ffmpeg: " + proc.stderr.decode("utf-8", "ignore")[-300:])
        with open(out_path, "rb") as f:
            wav = f.read()
        r = httpx.post(WHISPER_URL,
                       files={"file": ("audio.wav", wav, "audio/wav")},
                       data={"response_format": "json"},
                       timeout=120)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"whisper {r.status_code}: {r.text[:200]}")
        return (r.json().get("text") or "").strip()
    finally:
        for p in (in_path, out_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


def _synthesize(text):
    """text -> Kokoro /v1/audio/speech (wav) -> audio bytes."""
    r = httpx.post(KOKORO_URL,
                   json={"model": "kokoro", "input": text, "voice": TTS_VOICE,
                         "response_format": "wav"},
                   timeout=120)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"kokoro {r.status_code}: {r.text[:200]}")
    return r.content


@app.post("/stt")
async def stt(request: Request):
    _auth(request)
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty audio")
    text = await run_in_threadpool(_transcribe, data)
    return {"text": text}


@app.post("/tts")
async def tts(request: Request):
    _auth(request)
    body = await request.json()
    text = (body.get("text") or body.get("input") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="no text")
    audio = await run_in_threadpool(_synthesize, text)
    return Response(content=audio, media_type="audio/wav")


# --- the push-to-talk page ---------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def page():
    return _PAGE


# --- chat routes + aux -------------------------------------------------------
# G2 POSTs to whatever path you set as the Agent URL — canonical path and bare root both work.
app.post("/v1/chat/completions")(_chat)
app.post("/")(_chat)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "xorics", "object": "model", "owned_by": "rid"}]}


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>Xorics</title>
<style>
:root{--bg:#0b0d0c;--fg:#d7e0d9;--dim:#7c8a82;--accent:#36e07a;--rec:#ff4d4d;--warn:#f5c451;--panel:#131715;}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent;}
html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;}
body{display:flex;flex-direction:column;}
header{padding:14px 18px;font-weight:600;letter-spacing:.5px;border-bottom:1px solid #1d2420;}
header span{color:var(--accent);}
#log{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px;}
.row{display:flex;flex-direction:column;}
.row.you{align-items:flex-end;}
.row.xor{align-items:flex-start;}
.who{font-size:11px;color:var(--dim);margin:0 6px 2px;}
.msg{max-width:82%;padding:10px 13px;border-radius:14px;line-height:1.4;white-space:pre-wrap;word-wrap:break-word;}
.msg.you{background:#1f6f43;color:#eafff2;border-bottom-right-radius:4px;}
.msg.xor{background:var(--panel);border:1px solid #232b27;border-bottom-left-radius:4px;}
footer{padding:16px;display:flex;flex-direction:column;align-items:center;gap:10px;border-top:1px solid #1d2420;}
#status{font-size:13px;color:var(--dim);min-height:18px;text-align:center;}
#talk{width:84px;height:84px;border-radius:50%;border:none;background:var(--accent);color:#06210f;font-size:15px;font-weight:700;cursor:pointer;transition:background .15s,transform .1s;}
#talk:active{transform:scale(.96);}
#talk.rec{background:var(--rec);color:#2a0000;animation:pulse 1s infinite;}
#talk.busy{background:var(--warn);color:#2a2300;}
#talk:disabled{opacity:.85;}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(255,77,77,.5);}50%{box-shadow:0 0 0 14px rgba(255,77,77,0);}}
</style>
</head>
<body>
<header>Xor<span>ics</span> &middot; voice</header>
<div id="log"></div>
<footer>
  <div id="status">tap to talk</div>
  <button id="talk">Talk</button>
</footer>
<script>
const TOKEN = "browser"; // if you set XORICS_BRIDGE_TOKEN on the server, set the same value here
const log = document.getElementById("log");
const statusEl = document.getElementById("status");
const btn = document.getElementById("talk");
let stream=null, rec=null, chunks=[], recording=false, busy=false;

function setStatus(t){ statusEl.textContent = t; }
function addMsg(who, text){
  const row = document.createElement("div"); row.className = "row " + (who==="you"?"you":"xor");
  const tag = document.createElement("div"); tag.className = "who"; tag.textContent = who==="you"?"you":"xorics";
  const m = document.createElement("div"); m.className = "msg " + (who==="you"?"you":"xor"); m.textContent = text;
  row.appendChild(tag); row.appendChild(m); log.appendChild(row); log.scrollTop = log.scrollHeight;
}
function setBtn(state){
  btn.className = state==="rec" ? "rec" : state==="busy" ? "busy" : "";
  btn.textContent = state==="rec" ? "Stop" : state==="busy" ? "\u2026" : "Talk";
  btn.disabled = state==="busy";
}
function finish(){ busy=false; recording=false; setBtn("idle"); }

btn.addEventListener("click", async () => {
  if (busy) return;
  if (!recording) {
    try { if (!stream) stream = await navigator.mediaDevices.getUserMedia({audio:true}); }
    catch(e){ setStatus("mic blocked: " + e.message); return; }
    chunks = [];
    rec = new MediaRecorder(stream);
    rec.ondataavailable = e => { if (e.data && e.data.size) chunks.push(e.data); };
    rec.onstop = handleStop;
    rec.start(); recording = true; setBtn("rec"); setStatus("listening\u2026");
  } else {
    recording = false; setBtn("busy"); setStatus("\u2026"); rec.stop();
  }
});

async function handleStop(){
  busy = true; setBtn("busy");
  const blob = new Blob(chunks, {type:(rec && rec.mimeType) || "audio/webm"});
  setStatus("transcribing\u2026");
  let transcript = "";
  try {
    const r = await fetch("/stt", {method:"POST", headers:{Authorization:"Bearer "+TOKEN}, body:blob});
    if (!r.ok) throw new Error(r.status + " " + (await r.text()).slice(0,140));
    transcript = ((await r.json()).text || "").trim();
  } catch(e){ setStatus("stt error: " + e.message); return finish(); }
  if (!transcript){ setStatus("heard nothing \u2014 try again"); return finish(); }
  addMsg("you", transcript);

  setStatus("thinking\u2026");
  let reply = "";
  try {
    const r = await fetch("/v1/chat/completions", {method:"POST",
      headers:{Authorization:"Bearer "+TOKEN, "Content-Type":"application/json"},
      body: JSON.stringify({model:"xorics", messages:[{role:"user", content:transcript}]})});
    if (!r.ok) throw new Error(String(r.status));
    const j = await r.json();
    reply = (((j.choices||[])[0]||{}).message||{}).content || "";
  } catch(e){ setStatus("chat error: " + e.message); return finish(); }
  if (!reply){ setStatus("(no reply)"); return finish(); }
  addMsg("xorics", reply);

  setStatus("speaking\u2026");
  try {
    const r = await fetch("/tts", {method:"POST",
      headers:{Authorization:"Bearer "+TOKEN, "Content-Type":"application/json"},
      body: JSON.stringify({text: reply})});
    if (!r.ok) throw new Error(String(r.status));
    const url = URL.createObjectURL(await r.blob());
    const audio = new Audio(url);
    audio.onended = () => { URL.revokeObjectURL(url); setStatus("tap to talk"); };
    await audio.play();
  } catch(e){ setStatus("reply shown (audio blocked)"); }
  finish();
}
</script>
</body>
</html>"""
