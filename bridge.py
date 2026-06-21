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
footer{padding:14px 16px 18px;display:flex;flex-direction:column;align-items:center;gap:10px;border-top:1px solid #1d2420;}
#status{font-size:13px;color:var(--dim);min-height:18px;text-align:center;}
.meter{position:relative;width:100%;max-width:320px;height:10px;background:#10140f;border:1px solid #1d2420;border-radius:6px;overflow:hidden;}
#fill{height:100%;width:0%;background:#2a3a31;transition:width .05s linear;}
#mark{position:absolute;top:-2px;bottom:-2px;width:2px;background:var(--warn);left:20%;}
.ctl{width:100%;max-width:320px;display:flex;align-items:center;gap:10px;font-size:12px;color:var(--dim);}
.ctl input{flex:1;}
#talk{width:84px;height:84px;border-radius:50%;border:none;background:var(--accent);color:#06210f;font-size:15px;font-weight:700;cursor:pointer;transition:background .15s,transform .1s;margin-top:4px;}
#talk:active{transform:scale(.96);}
#talk.on{background:var(--rec);color:#2a0000;animation:pulse 1.4s infinite;}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(255,77,77,.45);}50%{box-shadow:0 0 0 12px rgba(255,77,77,0);}}
</style>
</head>
<body>
<header>Xor<span>ics</span> &middot; voice</header>
<div id="log"></div>
<footer>
  <div id="status">tap to start</div>
  <div class="meter"><div id="fill"></div><div id="mark"></div></div>
  <div class="ctl">sens <input id="sens" type="range" min="0" max="100" value="55"></div>
  <button id="talk">Start</button>
</footer>
<script>
const TOKEN = "browser"; // if you set XORICS_BRIDGE_TOKEN on the server, set the same value here
const log = document.getElementById("log");
const statusEl = document.getElementById("status");
const btn = document.getElementById("talk");
const fill = document.getElementById("fill");
const mark = document.getElementById("mark");
const sens = document.getElementById("sens");

// --- VAD tuning ---
const SILENCE_MS = 1200;     // trailing silence that ends an utterance
const MIN_SPEECH_MS = 350;   // ignore blips shorter than this
const MAX_REC_MS = 15000;    // hard cap on one utterance
const SCALE = 0.15;          // RMS that maps to a full meter
let threshold = 0.03;        // set by the sensitivity slider

let stream=null, audioCtx=null, analyser=null, dataArr=null;
let session=false, state="idle";          // idle | listening | recording | busy
let rec=null, chunks=[], lastVoice=0, speechStart=0, recStart=0, rafId=null;

function setStatus(t){ statusEl.textContent = t; }
function addMsg(who, text){
  const row=document.createElement("div"); row.className="row "+(who==="you"?"you":"xor");
  const tag=document.createElement("div"); tag.className="who"; tag.textContent=who==="you"?"you":"xorics";
  const m=document.createElement("div"); m.className="msg "+(who==="you"?"you":"xor"); m.textContent=text;
  row.appendChild(tag); row.appendChild(m); log.appendChild(row); log.scrollTop=log.scrollHeight;
}
function setBtn(on){ btn.className = on?"on":""; btn.textContent = on?"Stop":"Start"; }

// sensitivity: right = more sensitive (lower threshold). range 0.06 (loud only) .. 0.005
function applySens(){
  const v = parseInt(sens.value,10)/100;
  threshold = 0.06 - v*0.055;
  mark.style.left = Math.min(100, threshold/SCALE*100) + "%";
}
sens.addEventListener("input", applySens);
applySens();

function rms(){
  analyser.getFloatTimeDomainData(dataArr);
  let s=0; for(let i=0;i<dataArr.length;i++){ s += dataArr[i]*dataArr[i]; }
  return Math.sqrt(s/dataArr.length);
}
function drawMeter(level){
  fill.style.width = Math.min(100, level/SCALE*100) + "%";
  fill.style.background = level>threshold ? "var(--accent)" : "#2a3a31";
}

// One rAF loop runs for the whole session. It only acts while listening/recording,
// so it never picks up Xorics's own reply (state is 'busy' through play).
function loop(){
  if(!session){ return; }
  const level = rms();
  drawMeter(level);
  if(state==="listening" || state==="recording"){
    const now = performance.now();
    if(level > threshold){
      lastVoice = now;
      if(state==="listening"){ startRec(); speechStart=now; state="recording"; setStatus("hearing you\u2026"); }
    }
    if(state==="recording"){
      const silentFor = now-lastVoice, recLen = now-recStart;
      if((silentFor>SILENCE_MS && (now-speechStart)>MIN_SPEECH_MS) || recLen>MAX_REC_MS){ stopAndSend(); }
    }
  }
  rafId = requestAnimationFrame(loop);
}

function startRec(){
  chunks = [];
  rec = new MediaRecorder(stream);
  rec.ondataavailable = e => { if(e.data && e.data.size) chunks.push(e.data); };
  rec.start(); recStart = performance.now();
}
function stopAndSend(){
  state = "busy"; setStatus("\u2026");
  const mime = (rec && rec.mimeType) || "audio/webm";
  rec.onstop = () => sendTurn(new Blob(chunks, {type:mime}));
  try { rec.stop(); } catch(e){ sendTurn(new Blob(chunks,{type:mime})); }
}
function rearm(){
  if(!session){ setStatus("tap to start"); drawMeter(0); return; }
  state = "listening"; lastVoice = performance.now(); setStatus("listening\u2026");
}

async function sendTurn(blob){
  setStatus("transcribing\u2026");
  let transcript = "";
  try {
    const r = await fetch("/stt", {method:"POST", headers:{Authorization:"Bearer "+TOKEN}, body:blob});
    if(!r.ok) throw new Error(r.status+" "+(await r.text()).slice(0,120));
    transcript = ((await r.json()).text||"").trim();
  } catch(e){ setStatus("stt error: "+e.message); return rearm(); }
  if(!transcript){ setStatus("(didn't catch that)"); return rearm(); }
  addMsg("you", transcript);

  setStatus("thinking\u2026");
  let reply = "";
  try {
    const r = await fetch("/v1/chat/completions", {method:"POST",
      headers:{Authorization:"Bearer "+TOKEN, "Content-Type":"application/json"},
      body: JSON.stringify({model:"xorics", messages:[{role:"user", content:transcript}]})});
    if(!r.ok) throw new Error(String(r.status));
    const j = await r.json();
    reply = (((j.choices||[])[0]||{}).message||{}).content || "";
  } catch(e){ setStatus("chat error: "+e.message); return rearm(); }
  if(!reply){ setStatus("(no reply)"); return rearm(); }
  addMsg("xorics", reply);

  setStatus("speaking\u2026");
  try {
    const r = await fetch("/tts", {method:"POST",
      headers:{Authorization:"Bearer "+TOKEN, "Content-Type":"application/json"},
      body: JSON.stringify({text: reply})});
    if(!r.ok) throw new Error(String(r.status));
    const url = URL.createObjectURL(await r.blob());
    const audio = new Audio(url);
    audio.onended = () => { URL.revokeObjectURL(url); rearm(); };
    audio.onerror = () => { URL.revokeObjectURL(url); rearm(); };
    await audio.play();              // re-arm happens on 'ended', so we don't record the reply
  } catch(e){ setStatus("reply shown (audio blocked)"); rearm(); }
}

btn.addEventListener("click", async () => {
  if(!session){
    try { if(!stream) stream = await navigator.mediaDevices.getUserMedia({audio:true}); }
    catch(e){ setStatus("mic blocked: "+e.message); return; }
    if(!audioCtx){
      audioCtx = new (window.AudioContext||window.webkitAudioContext)();
      const src = audioCtx.createMediaStreamSource(stream);
      analyser = audioCtx.createAnalyser(); analyser.fftSize = 1024;
      dataArr = new Float32Array(analyser.fftSize);
      src.connect(analyser);
    }
    try { await audioCtx.resume(); } catch(e){}
    session = true; state = "listening"; lastVoice = performance.now();
    setBtn(true); setStatus("listening\u2026"); loop();
  } else {
    session = false; state = "idle";
    if(rec && rec.state === "recording"){ try{ rec.onstop=null; rec.stop(); }catch(e){} }
    if(rafId) cancelAnimationFrame(rafId);
    setBtn(false); setStatus("tap to start"); drawMeter(0);
  }
});
</script>
</body>
</html>"""
