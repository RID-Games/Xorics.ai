# personality-ttrpg — branch notes

Running notes for the personality + TTRPG branch. Kept branch-local (out of INFRA-NOTES)
to avoid adding merge surface against `main`. Engine work stays on `main`.

## Manager persona (done)

Factored the inline manager prompt in `ask()` into two constants:

- `_MANAGER_PERSONA` — the voice: warm/wry maker, dry deadpan when something's broken,
  honest over flattering, no over-engagement, RID's in-house intelligence. Tightens to
  plain facts the moment a real build starts.
- `_MANAGER_ROUTING` — the original delegation + tool rules, unchanged.

`ask()` composes `system = _MANAGER_PERSONA + "\n\n" + _MANAGER_ROUTING`, so a voice change
can't alter delegation. Coder side (`_CODER_GUIDE`, `pcb_tools.py`) untouched. Regression
check before trusting it: run a "design me a board" request and confirm `delegate_to_coder`
still fires and the coder still builds.

## G2 glasses output (FUTURE — do not build yet)

Goal: run conversational Xorics on Even Realities G2 smart glasses — hands-free, plus a
GM-narrating-in-your-ear TTRPG mode.

Clean path: Even Hub companion app -> "Add Agent" -> custom OpenAI-compatible endpoint
(Name / URL / Token). Wrap `ask()` in a tiny `/v1/chat/completions` shim; point the G2
Agent URL at RIDGames over Tailscale (`http://100.121.204.85:<port>/v1/chat/completions`).
Fully local, no cloud — the phone is already on the tailnet.

Verified (web, Mar 2026 — community reverse-engineering; there were no official API docs
for the custom-agent feature at the time, so re-check `hub.evenrealities.com/docs` before
building):

- G2 does speech-to-text ON-DEVICE and sends transcribed TEXT, not audio. The shim
  receives a normal `{"messages":[...]}` POST -> no whisper needed in this path.
- Payload: Bearer-token auth; a `model` field that's a fixed string (the shim should
  IGNORE it); the user's text in `messages`.
- Display: 576x136px monochrome green per the reverse-engineering writeup — this CONFLICTS
  with the handoff's 576x288. Confirm against Even's official spec. Either way: very few
  lines, green-on-black.

Design rule (affects persona + /rpg): the voice must collapse to a few short, speakable,
markup-free lines on demand. Implement glasses brevity as a MODE OVERLAY injected by the
shim ("HUD mode: a few short lines, spoken cadence, no lists or code") — NOT baked into the
base persona, so terminal `/chat` stays full-length. `/rpg` narration is already this
shape, so building it well serves the glasses goal for free.

Shim is small (~30 lines: FastAPI/Flask — take the last user message, call `ask()`, return
OpenAI-shaped JSON). TTS (parked) is what reads replies aloud — the "in your ear" half.

Alt integration paths if the Agent-endpoint route chafes:
- Even Hub SDK WebView app (`hub.evenrealities.com`) over WebSocket.
- Community BLE protocol: `github.com/i-soxi/even-g2-protocol`.
