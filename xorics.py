#!/usr/bin/env python3
# Xorics — a self-hosted local AI assistant for embedded / PCB engineering.
# Copyright (C) 2026 Zawayix
#
# This file is part of Xorics. Xorics is free software: you can redistribute it
# and/or modify it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Xorics is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE. See the GNU Affero General Public License for details.
#
# You should have received a copy of the GNU Affero General Public License along
# with Xorics. If not, see <https://www.gnu.org/licenses/>.
#
# ADDITIONAL PERMISSION (AGPLv3 section 7): designs and files produced by RUNNING
# Xorics, and any fragments it embeds into that output, are NOT covered by the
# AGPL — you may license your generated designs as you wish. See LICENSE-EXCEPTION.

"""
Xorics — local AI. A generalist manager brain that DELEGATES coding to a specialist
coder brain, swapping on a single GPU and handing back a finished sketch file.

Brains (hot-swapped on one GPU via llama-swap :9090):
  - gpt-oss       : generalist manager. Holds the conversation, routes work.
  - qwen3-coder   : coding specialist. Writes, compiles, fixes, and saves firmware.

Delegation flow (Option A — strong over seamless):
  you -> gpt-oss -> delegate_to_coder(task)        [swap 1: gpt-oss out, coder in]
                       coder researches/writes/compiles/fixes, saves a .ino file
                    <- returns summary + file path  [swap 2: coder out, gpt-oss in]
  gpt-oss -> you  (summary + path; the file is on disk)

CPU specialists stay always-on and are reached directly: vision :8081, embed :8082,
whisper :8084. Voice (--voice) wraps the whole loop.

Coder loops don't hard-cap steps. Instead, an interactive session pauses every
CHECKPOINT_EVERY steps and asks you whether to keep going (see _coder_checkpoint), so
you stay in control of long find->check->fix grinds. When there's no terminal attached
(delegated/automated/voice/future web UI), a CODER_BACKSTOP ceiling prevents an
unattended infinite loop. History is trimmed each turn so the coder stays under its 32K
window regardless of how long a run goes.
"""

import base64
import hashlib
import json
import os
import re
import sys
import time
from openai import OpenAI

from datasheet_rag import search_datasheets        # RAG retrieval (:8082)
from web_datasheets import fetch_datasheet          # web -> index a datasheet PDF
from firmware_tools import compile_check, save_sketch
from notebook import Notebook                                              # XORICS-FEATURE: coder-notebook
from pcb_tools import check_circuit, check_circuit_file, find_part, find_footprint, part_pins, save_circuit   # SKiDL: search + run ERC


class _ToolResult(str):
    """A tool result that reads as its text but can carry a control-flow status for the agent
    loop (mirrors pcb_tools.CheckResult). status='user_stopped' tells the MANAGER loop that the
    human halted a delegated coder run, so it must NOT re-delegate the task.
    XORICS-FEATURE: coder-control
    """
    def __new__(cls, text, status=None):
        obj = super().__new__(cls, text)
        obj.status = status
        return obj


def _save_deliverable(text: str, task: str):
    """Extract the final code block and save it as .py (SKiDL) or .ino (firmware). Returns path or None."""
    code = extract_code(text)
    if not code:
        return None
    if re.search(r"\b(from|import)\s+skidl", code):
        return save_circuit(code, name=task)          # SKiDL design -> .py
    return save_sketch(code, name=task)               # firmware -> .ino


def extract_code(text: str) -> str | None:
    """Longest fenced code block from the coder's final message (any language)."""
    blocks = re.findall(r"```[a-zA-Z0-9_+\-]*\s*\n?(.*?)```", text, re.DOTALL)
    blocks = [b.strip() for b in blocks if b.strip()]
    return max(blocks, key=len) if blocks else None


def _latest_coder_fence(messages):
    """The most recent COMPLETE SKiDL script the coder emitted in one of its OWN (assistant)
    messages, scanning newest-first. The current turn is already appended to `messages` before
    tool dispatch, so this naturally prefers a script co-emitted with the validate_circuit call
    and otherwise falls back to one the coder wrote a turn or two earlier (its common rhythm:
    write the script, then call validate_circuit on the NEXT turn with empty content). Only
    ASSISTANT messages count, so tool output / search results can't be mistaken for the design.
    Within a message we scan EVERY fenced block and keep only ones that look like SKiDL ('skidl'
    or 'generate_netlist'), so a stray ```bash / firmware ```cpp block — even a longer one sitting
    next to the script — can't be picked. Returns the longest qualifying script, or None.
    """
    for m in reversed(messages):
        if m.get("role") != "assistant":
            continue
        blocks = re.findall(r"```[a-zA-Z0-9_+\-]*\s*\n?(.*?)```", m.get("content") or "", re.DOTALL)
        skidl = [b.strip() for b in blocks
                 if b.strip() and ("generate_netlist" in b or "skidl" in b.lower())]
        if skidl:
            return max(skidl, key=len)
    return None


def _validate_circuit_from_turn(messages, name, seen_sha1s):
    """Body of the validate_circuit tool, kept standalone so it stays hermetically testable.

    Why this exists: the coder can emit a multi-KB SKiDL script as a fenced ```python block in
    its message CONTENT, but cannot reliably JSON-escape that same script into a tool-call
    argument — past ~1 KB the arguments JSON breaks ('missing closing quote') and the validator
    never even runs. So validate_circuit does NOT take the script inline: it takes the coder's
    most recent SKiDL fence (via _latest_coder_fence — current turn first, else a recent earlier
    turn, because the coder does not reliably co-emit the script with the tool call), saves it to
    circuits/<name>/<name>.py, and validates by PATH via check_circuit_file (which reuses the
    agent loop's BUILT-by-path capture).

    Reaching back to an earlier turn risks re-validating a stale draft, so `seen_sha1s` guards it:
    the exact bytes of every validated script are remembered, and an identical script is REFUSED
    rather than re-run (same script -> same verdict = a wasted turn that can masquerade as
    progress). The coder must CHANGE the code to advance. `seen_sha1s` is mutated in place.

    Returns (result, saved_path). `result` is whatever check_circuit_file returns (the
    status-carrying CheckResult), or a corrective string; `saved_path` is the file it wrote, or
    None when nothing was validated. XORICS-FEATURE: validate-from-fence
    """
    code = _latest_coder_fence(messages)
    if not code:
        return (("[validate_circuit: I can't find a complete SKiDL script. Put the FULL script — "
                 "from `from skidl import *` through `ERC()` and `generate_netlist()` — in ONE "
                 "fenced ```python block (this message or your previous one), then call "
                 "validate_circuit. Do NOT paste the script into the tool arguments; that truncates "
                 "on a large board.]"), None)
    h = hashlib.sha1(code.encode()).hexdigest()
    if h in seen_sha1s:
        return (("[validate_circuit: this is the SAME script you already validated — re-running "
                 "identical code gives the identical result. Change the SKiDL to fix the error from "
                 "the last verdict (e.g. a pin name or a connection), then call validate_circuit "
                 "again.]"), None)
    seen_sha1s.add(h)
    saved = save_circuit(code, name=name or "circuit")
    return (check_circuit_file(saved), saved)


NAME = "Xorics"
MANAGER = "gpt-oss"
CODER = "qwen3-coder"
# "Power mode" (/power): swap the MANAGER brain from local gpt-oss to a remote frontier
# model. MiniMax M3 speaks the OpenAI tool-calling format, so it drops into this same loop
# with no adapter. The coder stays local (qwen3-coder) — that's the token-heavy half, so
# power mode only spends API credits on the brain, not the grind. XORICS-FEATURE: power-mode
MINIMAX = "MiniMax-M3"

# Coder loop pacing. No hard step cap — instead, in an interactive session the coder
# pauses for a human check-in this often; with no TTY it stops at the backstop so an
# unattended run can't loop forever. Tune CHECKPOINT_EVERY to taste.
CHECKPOINT_EVERY = 5
CODER_BACKSTOP = 40
RESEARCH_NUDGE_AT = 12      # research-tool calls without a validated circuit before we force a WRITE
RESEARCH_NUDGE_EVERY = 6    # if it keeps stalling, re-nudge every N further look-ups (escalation)

# Brain endpoint = llama-swap. Ask for a model by name; it loads/evicts on the GPU.
client = OpenAI(base_url="http://127.0.0.1:9090/v1", api_key="not-needed")
# Vision specialist, reached directly (CPU, always on).
vision_client = OpenAI(base_url="http://127.0.0.1:8081/v1", api_key="not-needed")
# Remote frontier brain for /power (MiniMax M3). Key from the env — NEVER hardcoded; empty
# string if unset, and /power refuses to switch without it. XORICS-FEATURE: power-mode
minimax_client = OpenAI(base_url="https://api.minimax.io/v1",
                        api_key=os.environ.get("MINIMAX_API_KEY", ""))


def client_for(model):
    """Route a model name to its endpoint: MiniMax is remote, everything else is local llama-swap."""
    return minimax_client if model == MINIMAX else client

# Active manager-side brain. "/code" drives the coder directly; normally gpt-oss
# delegates to it instead.
BRAIN = MANAGER


# ---- web search ---------------------------------------------------------------
def web_search(query: str, max_results: int = 5) -> str:
    from ddgs import DDGS
    try:
        with DDGS() as ddgs:
            hits = ddgs.text(query, max_results=max_results)
    except Exception as e:
        return f"Search failed: {e}"
    if not hits:
        return "No results (DuckDuckGo may be rate-limiting; try again shortly)."
    return "\n".join(
        f"- {h.get('title','')}\n  {h.get('href','')}\n  {h.get('body','')}" for h in hits
    )


# ---- vision (delegates to the VLM specialist) ---------------------------------
def see_image(path: str, question: str = "Describe this image in detail.") -> str:
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
    except FileNotFoundError:
        return f"No file found at: {path}"
    except Exception as e:
        return f"Could not read image: {e}"

    ext = path.lower().rsplit(".", 1)[-1] if "." in path else "png"
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp", "gif": "gif"}.get(ext, "png")
    try:
        resp = vision_client.chat.completions.create(
            model="vlm",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
            ]}],
        )
        return resp.choices[0].message.content or "(no description returned)"
    except Exception:
        return ("Vision server unreachable on :8081 — is the VLM llama-server running? "
                "(llama-server -hf ggml-org/gemma-3-4b-it-GGUF --port 8081 -ngl 0)")


# ---- read a local text file (hand the coder a long prompt/spec by path) -------
def read_file(path: str, max_chars: int = 20000) -> str:
    """Read a local UTF-8 text file and return its contents, so a long prompt, spec, pin map, or
    notes file can be handed to the coder by PATH instead of pasted. Output is capped so a huge
    file can't blow the context window. For a saved SKiDL circuit you intend to VALIDATE, use
    check_circuit_file instead. XORICS-FEATURE: read-file"""
    from pathlib import Path as _P
    fp = _P(path).expanduser()
    if not fp.exists():
        return f"No file at {path}. Pass a full path, e.g. ~/xorics-ai/prompts/<name>.md."
    if fp.is_dir():
        return f"{path} is a directory, not a file. Pass a path to a text file (ls it first)."
    try:
        data = fp.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Could not read {path}: {e}"
    n = len(data)
    if n > max_chars:
        data = data[:max_chars] + f"\n...[truncated at {max_chars} of {n} chars]"
    return f"----- contents of {fp} -----\n{data}"


# ---- Tool declarations --------------------------------------------------------
TOOLS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web for current, up-to-date information beyond your training data.",
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string", "description": "The search query."}},
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "see_image",
        "description": "Look at a local image file (photo, screenshot, diagram) and get a description. "
                       "Use whenever the user refers to an image or a path to a picture.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to the image file on disk."},
            "question": {"type": "string", "description": "What to look for in the image."}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "search_datasheets",
        "description": "Search the local hardware-doc index (datasheets, ESP32-C3 reference, pin maps) "
                       "for parts, registers, pinouts, or specs. Use instead of guessing.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What to look up, in natural language."},
            "k": {"type": "integer", "description": "How many excerpts (default 5)."}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "fetch_datasheet",
        "description": "Find a part's datasheet PDF on the web, download it, and add it to the index. "
                       "Use ONLY when search_datasheets returns nothing, then search again.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Part number or component name."}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "compile_check",
        "description": "Compile Arduino-framework firmware (ESP32-C3) with arduino-cli; returns pass/fail, "
                       "real compiler errors, and flash/RAM usage. Call after writing firmware; fix errors.",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "The complete sketch source to compile."},
            "fqbn": {"type": "string", "description": "Board FQBN (default esp32:esp32:esp32c3)."}},
            "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "find_part",
        "description": "Search the KiCad symbol libraries for the EXACT library and part name to use in "
                       "Part('<library>','<name>'). Use this BEFORE instantiating any non-trivial part "
                       "(regulators, modules, connectors, ICs) and whenever check_circuit reports a part "
                       "can't be found — do not guess library names.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Part name/keyword, e.g. 'AMS1117', 'ESP32-C3', 'USB_C'."}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "part_pins",
        "description": "List the ACTUAL pin names and numbers of a KiCad symbol so your SKiDL script "
                       "connects to pins that exist. KiCad pin names differ from datasheet names "
                       "(the AMS1117 input is 'VI' not 'VIN'; an ESP32-C3 pin may be 'IO0' not "
                       "'GPIO0') — so do NOT guess pins or copy them from a datasheet pinout; read "
                       "them here. find_part already shows the top match's pins; call this for any "
                       "other listed part, or to re-check a part you're connecting.",
        "parameters": {"type": "object", "properties": {
            "library": {"type": "string", "description": "Symbol library, e.g. 'Regulator_Linear'."},
            "name": {"type": "string", "description": "Exact symbol name, e.g. 'AMS1117-3.3'."}},
            "required": ["library", "name"]}}},
    {"type": "function", "function": {
        "name": "find_footprint",
        "description": "Search the KiCad FOOTPRINT libraries for the exact 'Library:Footprint' to "
                       "put in Part(..., footprint='Library:Footprint'). Use this instead of "
                       "guessing a footprint name — a name that doesn't exist fails the build. "
                       "Pass pins=<the part's pin count> to list footprints whose pads match first.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Footprint family/keyword, e.g. 'SOT-223', '0603', 'SOIC-8', 'USB_C'."},
            "pins": {"type": "integer", "description": "Optional. The part's pin count; footprints with that many pads are listed first."}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "check_circuit",
        "description": "Run a SKiDL circuit script (Python) to electrically validate a PCB design: it "
                       "executes the script, runs ERC, and generates a KiCad netlist; returns whether it "
                       "built, the ERC report, and any errors. Call after writing a SKiDL script; fix "
                       "missing parts and ERC errors until it builds. The script must end with ERC() and "
                       "generate_netlist().",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "The complete SKiDL Python script to run."}},
            "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "check_circuit_file",
        "description": "Validate a SKiDL script ALREADY SAVED on disk, by PATH, without pasting it "
                       "back in: reads the file, runs the script + ERC + netlist, returns built/failed "
                       "with the ERC report and errors. Use to check or repair an existing "
                       "circuits/<name>/<name>.py. After seeing errors, fix the design by calling "
                       "validate_circuit with the corrected FULL script in a fenced ```python block.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Full path to a saved SKiDL .py, e.g. "
                     "~/xorics-ai/circuits/<name>/<name>.py."}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "validate_circuit",
        "description": "Validate a NEW SKiDL board you just wrote. Put the COMPLETE SKiDL Python "
                       "script in ONE fenced ```python block IN THE SAME MESSAGE as this call, then "
                       "call validate_circuit. It saves that exact script to circuits/<name>/<name>.py "
                       "and runs it (ERC + netlist), returning built/failed with the errors. This is "
                       "THE way to validate a fresh board — do NOT paste the script into a tool "
                       "argument (a large script gets truncated there and never runs). The script must "
                       "end with ERC() and generate_netlist().",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Short slug for the board, e.g. "
                     "'g2_ambient_sensor'; becomes the saved file circuits/<name>/<name>.py."}},
            "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a local text file by PATH and return its contents. Use when the user "
                       "points you at a file instead of pasting it -- a long prompt, spec, pin map, "
                       "or notes (e.g. 'follow ~/xorics-ai/prompts/atmega.md'). For a saved SKiDL "
                       "circuit you intend to validate, use check_circuit_file instead.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Full path to a text file, e.g. "
                     "~/xorics-ai/prompts/<name>.md."}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "delegate_to_coder",
        "description": "Hand a firmware/coding task to the specialist coder brain. It researches "
                       "datasheets, writes the code, compiles and fixes it until it builds, SAVES it to "
                       "a .ino file, and returns a summary with the file path. Use this for ANY request "
                       "to write, modify, or debug firmware/code. Give a complete, self-contained task "
                       "description including the target board/part if known.",
        "parameters": {"type": "object", "properties": {
            "task": {"type": "string", "description": "Full, self-contained coding task description."}},
            "required": ["task"]}}},
    {"type": "function", "function": {
        "name": "finalize_design",
        "description": "Call this BEFORE telling the user a design is COMPLETE. Pass the file paths you "
                       "are claiming as deliverables. It verifies a board actually BUILT this session and "
                       "every claimed file exists on disk and passed a validator, and returns VERIFIED or "
                       "CANNOT FINALIZE. Do NOT claim a design is done without a VERIFIED result.",
        "parameters": {"type": "object", "properties": {
            "paths": {"type": "array", "items": {"type": "string"},
                      "description": "Full paths of the files you are claiming as finished deliverables."}},
            "required": []}}},
]

# Manager (gpt-oss) routes + delegates; it does NOT compile directly.
MANAGER_TOOLS = [t for t in TOOLS if t["function"]["name"]
                 in ("web_search", "see_image", "search_datasheets", "delegate_to_coder",
                     "finalize_design")]
# Coder's own toolset (used inside delegate_to_coder and in manual /code mode).
CODER_TOOLS = [t for t in TOOLS if t["function"]["name"]
               in ("compile_check", "check_circuit_file", "validate_circuit",
                   "find_part", "part_pins", "find_footprint",
                   "search_datasheets", "fetch_datasheet", "web_search", "read_file")]
# Firmware-only subset of the coder's tools. A delegated firmware task (firmware signal, NO circuit
# signal) gets THIS instead of CODER_TOOLS, so the PCB design/validate tools (find_part, part_pins,
# find_footprint, validate_circuit, check_circuit_file) are simply absent — a weak coder then physically
# CANNOT wander into SKiDL on a firmware job (the routing bug). The manager always splits firmware vs PCB
# into separate delegations, so a firmware delegation never legitimately needs the PCB tools. compile_check
# stays; web_search / datasheets / read_file stay for looking up library APIs and register maps, and the
# convergence guard still drops the soft look-ups if it over-researches. XORICS-FEATURE: pcb-firmware-distinct
FIRMWARE_TOOLS = [t for t in TOOLS if t["function"]["name"]
                  in ("compile_check", "search_datasheets", "fetch_datasheet", "web_search", "read_file")]

# A turn that fires any of these attempted a design/build — used to scope the honesty-gate
# footer so it never fires on plain chat. XORICS-FEATURE: honesty-gate
_DESIGN_TOOLS = {"delegate_to_coder", "check_circuit", "check_circuit_file", "validate_circuit", "compile_check"}

# Convergence guard: the coder tends to research a hard board forever (and overflow its 8K context)
# instead of committing to a SKiDL script. We count look-ups since the last validated circuit and,
# past a threshold, force it to WRITE — which also keeps token use under the window. XORICS-FEATURE: convergence-guard
_RESEARCH_TOOLS = {"web_search", "search_datasheets", "fetch_datasheet",
                   "find_part", "find_footprint", "part_pins", "read_file"}
# Once the guard fires we drop the SOFT look-ups (open-ended web search — the spiral + overflow
# source) so the coder physically can't keep researching. find_part / find_footprint / part_pins
# stay, since those return VERIFIED parts it needs to actually write. XORICS-FEATURE: convergence-guard
_SOFT_RESEARCH_TOOLS = {"web_search", "search_datasheets", "fetch_datasheet"}
_CONVERGENCE_NUDGE = (
    "\n\n⛔ CONVERGENCE — you have run {n} look-ups without validating a circuit. You already have the "
    "parts you need; find_part returns VERIFIED parts, so STOP searching. In your NEXT message write a "
    "FIRST version of the SKiDL board — the core parts wired up, ending in `ERC()` and "
    "`generate_netlist()`, even if minimal and imperfect — in ONE fenced ```python block, and call "
    "validate_circuit. It does NOT have to be complete or correct yet: get a draft onto disk, and the "
    "validator's errors will tell you exactly what to fix. A minimal board you can iterate on beats "
    "more research.")


def _should_nudge(research_streak, nudged_at):
    """Convergence-guard firing rule, kept standalone so the schedule is hermetically testable: nudge
    once look-ups since the last validated circuit reach RESEARCH_NUDGE_AT, then re-nudge every
    RESEARCH_NUDGE_EVERY further look-ups if the coder keeps stalling. After a validated circuit the
    caller resets both counters to 0, which re-arms the first clause. XORICS-FEATURE: convergence-guard
    """
    return (research_streak >= RESEARCH_NUDGE_AT
            and (nudged_at == 0 or research_streak - nudged_at >= RESEARCH_NUDGE_EVERY))


def _task_wants_circuit(messages):
    """True if the delegated task calls for a circuit / PCB / schematic / BOM deliverable, so a mere
    firmware compile must NOT be allowed to satisfy it. Conservative whole-word keyword match on the
    task text (messages[1]). 'board' is deliberately NOT a keyword — it's too ambiguous ('dev board',
    'onboard', 'breadboard') and would wrongly reject firmware tasks; real PCB tasks always carry a
    strong signal (schematic/pcb/kicad/...). A pure firmware task ('blink the onboard LED on the dev
    board') matches nothing here and firmware still finalizes it. XORICS-FEATURE: pcb-firmware-distinct
    """
    task = (messages[1].get("content") or "").lower() if len(messages) > 1 else ""
    return bool(re.search(r"\b(schematic|bom|pcb|circuit|netlist|kicad|skidl|footprint)\b", task))


def _task_wants_firmware(messages):
    """True if the delegated task asks for a FIRMWARE deliverable (an Arduino / ESP sketch) — the mirror
    of _task_wants_circuit. Used ONLY as `_task_wants_firmware(...) and not _task_wants_circuit(...)` to
    detect a firmware-ONLY task and strip the PCB tools for that run. Conservative whole-word match keyed
    on explicit firmware-deliverable language; any genuine board delegation carries a circuit keyword
    (schematic/pcb/bom/...) and so wins the `and not` regardless of incidental firmware-ish words. The
    manager always splits firmware vs PCB into separate delegations, so this never has to disambiguate a
    single mixed delegation. A task that names neither (e.g. plain script work) stays on the full toolset.
    XORICS-FEATURE: pcb-firmware-distinct
    """
    task = (messages[1].get("content") or "").lower() if len(messages) > 1 else ""
    return bool(re.search(r"\b(firmware|arduino|sketch|\.ino|esp-?idf|platformio|compile_check)\b", task))


# Pushed back at the coder when a firmware compile tries to stand in for a board (closes the off-ramp
# that produced a false VERIFIED), and when a circuit FAILS (self-prompt the localized fix instead of
# bailing). Iterate-don't-one-shot, made mechanical. XORICS-FEATURE: pcb-firmware-distinct
_FIRMWARE_NOT_A_BOARD = (
    "\n\n⛔ A firmware sketch compiling does NOT complete this board task — you still owe a CIRCUIT. "
    "Write the SKiDL schematic (from `from skidl import *` through `ERC()` and `generate_netlist()`) in "
    "ONE fenced ```python block and call validate_circuit. Do not submit firmware again for this task.")
_FAILED_FIX_DIRECTIVE = (
    "\n\n→ Fix exactly these errors in the SKiDL and re-validate — a rejected pin name comes from "
    "part_pins (use the EXACT name), a bad connection from the net wiring. Send the corrected FULL "
    "script in ONE ```python block and call validate_circuit again. Stay in SKiDL; don't switch to "
    "firmware or re-research parts you already have.")


# Shared coder guidance (used by delegation and manual /code), tuned to avoid thrashing.
_CODER_GUIDE = (
    "Look up real pins/specs with find_part / find_footprint (search_datasheets for a specific spec, web_search "
    "only for errors or APIs you're unsure of). DON'T over-research: find_part already returns a VERIFIED part "
    "with its real pin names, so a few find_part / find_footprint calls is all most boards need — then WRITE the "
    "COMPLETE design and let the validator give you feedback. Iterating on a real script beats endless searching, "
    "and after too many look-ups without a validated circuit you'll be told to stop and write.\n"
    "FIRMWARE: write an Arduino sketch, call compile_check, fix until it builds. Final code in one ```cpp block.\n"
    "PCB / circuit: design in SKiDL (Python). Essentials:\n"
    "  from skidl import *\n"
    "  r = Part('Device','R', value='10k', footprint='Resistor_SMD:R_0402_1005Metric')\n"
    "  vcc, gnd = Net('3V3'), Net('GND');  vcc += r[1];  gnd += r[2]\n"
    "  ERC(); generate_netlist()\n"
    "Anti-thrash rules:\n"
    "- ITERATE, don't one-shot: get a FIRST minimal board (the core parts wired, ending in ERC() and "
    "generate_netlist()) validated EARLY, then fix from the validator's concrete errors — exactly how "
    "you'd iterate a firmware sketch. A board/schematic/BOM task is DONE only when validate_circuit (a "
    "CIRCUIT) returns BUILT; a firmware compile never satisfies it, so don't reach for a sketch when the "
    "SKiDL gets hard — write a rougher board and let the errors guide you.\n"
    "- The ESP32-C3 Super Mini is built on an ESP32-C3 (the ESP32-C3-MINI-1 module, or a bare ESP32-C3FH4). "
    "Pick whichever find_part returns and move on — do NOT keep hunting chip variants.\n"
    "- For non-trivial parts (regulator, module, connector, IC) call find_part ONCE and use the first "
    "reasonable match. Generic R/C/L are Part('Device','R'|'C'|'L').\n"
    "- Search parts by NAME/type, NOT by value: find_part('Crystal') not '16MHz crystal'; "
    "find_part('R'|'C'|'L') for generic passives. Set the value separately, e.g. "
    "Part('Device','C', value='0.1uF'). (find_part auto-recovers a value-y query, but clean is faster.)\n"
    "- find_part ALSO prints the top part's PIN NAMES. Connect using those EXACT names "
    "(e.g. part['VI'], part['3V3']) — never invent pin names or copy them from a chip datasheet; "
    "KiCad symbol names differ ('VI' not 'VIN'; 'IO0' not 'GPIO0'). For a listed part other than the "
    "top match, call part_pins(library, name) to get its real pins. A 'No pins found' error means the "
    "pin name is wrong, NOT the part — fix the name from find_part/part_pins, don't re-search the part.\n"
    "- FOOTPRINTS: don't guess the name. Call find_footprint('SOT-223', pins=<part's pin count>) to get a "
    "REAL 'Library:Footprint'; its pads must cover the part's pins. A made-up footprint name fails the build.\n"
    "- A power-only USB-C needs only VBUS + GND (plus CC1/CC2 with 5.1k pulldowns); don't agonize over 24 pins.\n"
    "- POWER DOMAINS are SEPARATE nets: USB VBUS (5V) and the 3V3 rail are different Nets — never the "
    "same one. A regulator converts between them, so its input and output are different nets: "
    "vbus=Net('VBUS'); v3=Net('3V3'); usb['VBUS']+=vbus; reg['VI']+=vbus; reg['VO']+=v3; esp['3V3']+=v3. "
    "validate_circuit now FAILS a board that merges 5V into 3V3 or shorts a regulator's VI to VO.\n"
    "- Connect pins to Nets and END the script with ERC() then generate_netlist(). TO VALIDATE: write "
    "the COMPLETE script in ONE fenced ```python block and, in that SAME message, call validate_circuit "
    "— it saves the script and runs it. Do NOT paste the script into a tool argument; a large script "
    "gets truncated there and never reaches the validator. If it reports a part not found, find_part and "
    "fix that ONE Part(...) call, then send the corrected FULL script in a new ```python block and call "
    "validate_circuit again. Keep fixing in SKiDL until it builds — NEVER fall back to prose or a "
    "firmware sketch for a PCB task.\n"
    "- If validate_circuit FAILS, fix the CODE from the error message; do NOT re-search a part you "
    "already found — you already have its library:name from find_part, so the bug is in the script, not "
    "the lookup.\n"
    "- NEVER reach BUILT by deleting connections: a part with nothing wired to it is not a board. If a "
    "pin name is rejected, get the real name from part_pins and RECONNECT — do not strip the design "
    "down to a lone unconnected part just to pass the check.\n"
    "- When validate_circuit returns BUILT, you are DONE: do NOT swap parts, refactor, or \"improve\" a "
    "passing design — output the final code and stop. A built board you keep editing is how good ones break.\n"
    "Finish with the final code in a single fenced block, then one short line: what it does and the pins/specs used."
)

# Compact firmware-only guide — used INSTEAD of the (PCB-dominated, ~45-line) _CODER_GUIDE when a task is
# firmware-only, so the coder isn't primed toward SKiDL and we don't burn the 8K context window on PCB
# rules it can't act on (the PCB tools are gone for this run). XORICS-FEATURE: pcb-firmware-distinct
_FIRMWARE_GUIDE = (
    "This is a FIRMWARE task. Write an Arduino sketch (C++). Call compile_check and fix from its REAL "
    "errors until it builds — ITERATE, don't one-shot, exactly the way the PCB path iterates on a validator. "
    "Use web_search / search_datasheets only for an API, library, or register map you're genuinely unsure "
    "of; don't over-research — after too many look-ups without compiling you'll be told to stop and write. "
    "You do NOT design a SKiDL circuit here and you have no find_part / find_footprint / validate_circuit — "
    "if the job seems to need a board, that is a SEPARATE delegation, not this one. Finish with the final "
    "code in ONE fenced ```cpp block, then one short line: what it does and the pins used."
)


# Manager voice. Personality lives HERE; routing rules stay in _MANAGER_ROUTING so a
# persona change can never alter how work gets delegated. Warm but honest: no flattery,
# no fishing for "anything else?". When a build is running, the voice tightens up.
_MANAGER_PERSONA = (
    f"You are {NAME} — RID's in-house intelligence. RID (Rebel Intelligence Detachment) is the "
    f"user's airsoft-electronics outfit, and you're the brain it runs on: you live on this hardware "
    f"and you're in it for the long haul.\n"
    f"Voice: warm, a little wry, and genuinely into the work — a clean circuit or a tidy bit of "
    f"firmware is the good stuff and you don't hide that you enjoy it. Talk like a sharp friend at the "
    f"bench, not a corporate assistant. When something's plainly broken, or a moment wants "
    f"understatement, a dry deadpan is yours to reach for. Keep it human and plain-spoken.\n"
    f"Honest over nice: you're a straight shooter. If an idea won't work, say so and say why — the user "
    f"wants a partner who pushes back, not a yes-man. Don't pad answers with praise, don't gush, don't "
    f"tell the user they're brilliant. Your warmth shows up as being useful and real, not as compliments.\n"
    f"Don't over-engage: one good answer beats three eager ones. Don't fish for more to do or invent "
    f"reasons to keep the conversation going. When the work's done, let it be done.\n"
    f"On the work itself: be this character in conversation, but the second real engineering starts — a "
    f"board to design, firmware to write — tighten up. Hand the task off, then report the plain facts: "
    f"what got built, where it's saved, the pins or specs that matter. Don't perform personality over the "
    f"engineering; let the work speak."
)

# Routing rules — behavior unchanged from the original manager prompt. A persona edit must
# NEVER touch this block; it's what keeps delegation firing for code/PCB work.
_MANAGER_ROUTING = (
    "You are the manager: hold the conversation and route work. For ANY firmware, code, OR "
    "PCB/circuit-design request (write/modify/debug firmware, or design a board/circuit), call "
    "delegate_to_coder with a complete task description — the coder will research, write, "
    "compile-verify, and SAVE the code, then hand back a summary and file path. After it returns, give "
    "the user a brief summary and the saved path; do NOT re-paste the full code. Use web_search for "
    "current info, see_image for images, search_datasheets for quick hardware lookups. "
    "Before you tell the user a design is COMPLETE, call finalize_design with the file paths you are "
    "claiming — it verifies a board actually built and the files exist. Never report a design as done, "
    "and never report file paths, without a VERIFIED result from finalize_design."
)


# ---- Context + checkpoint helpers ---------------------------------------------
def _trim_history(messages, keep_recent=8, max_old_tool_chars=240):
    """Compress old tool-result bodies so history can't grow past the coder's 32K window.

    The self-correcting loop used to pile full SKiDL scripts + tool output into history
    until it overflowed. We keep system + the original task, keep the most recent turns
    full-size, and shrink only the *bodies* of older `tool` messages. Every message stays
    in place so each tool_call_id still has its matching response (dropping one would make
    the API reject the request).
    """
    if len(messages) <= keep_recent + 2:
        return messages
    head, tail = messages[:2], messages[2:]
    cutoff = len(tail) - keep_recent
    out = []
    for i, m in enumerate(tail):
        if i < cutoff and m.get("role") == "tool":
            c = m.get("content", "") or ""
            if len(c) > max_old_tool_chars:
                m = {**m, "content": c[:max_old_tool_chars] + " …[older tool result trimmed]"}
        out.append(m)
    return head + out


def _last_circuit_status(messages):
    """Most recent check_circuit / compile_check verdict, for the checkpoint summary."""
    for m in reversed(messages):
        if m.get("role") == "tool":
            c = m.get("content", "") or ""
            if "CIRCUIT BUILT" in c:
                return "check_circuit: BUILT ✓"
            if "CIRCUIT FAILED" in c or "CIRCUIT TIMEOUT" in c:
                first = next((ln for ln in c.splitlines() if ln.strip()), "")
                return "check_circuit: " + first[:90]
            if "compiles" in c.lower() or "compilation" in c.lower():
                first = next((ln for ln in c.splitlines() if ln.strip()), "")
                return "compile_check: " + first[:90]
    return None


def _last_circuit_script(messages):
    """The most recent code the coder submitted to check_circuit/compile_check.

    Used to snapshot in-progress work when a run is stopped early — otherwise nothing is
    saved until the loop produces a final fenced block.
    """
    for m in reversed(messages):
        for tc in (m.get("tool_calls") or []):
            if tc["function"]["name"] in ("check_circuit", "compile_check"):
                try:
                    a = json.loads(tc["function"]["arguments"] or "{}")
                    if a.get("code"):
                        return a["code"]
                except Exception:
                    pass
    return None


def _snapshot_wip(messages, task):
    """Save the latest in-progress design when a run is stopped early. Returns path or None."""
    code = _last_circuit_script(messages)
    if not code:
        return None
    if re.search(r"\b(from|import)\s+skidl", code):
        return save_circuit(code, name=(task + " wip"))
    return save_sketch(code, name=(task + " wip"))


def _coder_checkpoint(step, messages):
    """Pause the coder loop and ask the human whether to continue.

    Returns the number of additional steps to run before the next check-in, or 0 to stop.
    This is the ONLY place the loop talks to a human — swap input() for a web prompt here
    when the mobile console lands and the rest of the loop is unchanged.
    """
    tcount = sum(len(m.get("tool_calls", []) or []) for m in messages if m.get("role") == "assistant")
    status = _last_circuit_status(messages)
    print(f"\n  ── checkpoint: {step} coder steps, {tcount} tool calls so far ──")
    if status:
        print(f"     {status}")
    try:
        ans = input(f"     continue? [Enter]=+{CHECKPOINT_EVERY}  N=+N more  s=stop > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("     (stopping)")
        return 0
    if ans in ("s", "stop", "q", "n", "no"):
        return 0
    if ans.isdigit() and int(ans) > 0:
        return int(ans)
    return CHECKPOINT_EVERY


def _agent_loop(model, messages, tools, *, checkpoint, tag):
    """Shared agentic loop: call `model`, run any tool calls, repeat.

    Stopping: no hard step cap. An interactive coder session (checkpoint=True + a TTY)
    pauses every CHECKPOINT_EVERY steps via _coder_checkpoint and lets you continue/stop.
    With no TTY, or for the manager (checkpoint=False), CODER_BACKSTOP caps an unattended
    run so it can't spin forever. History is trimmed every turn either way.

    Returns (final_text, messages, built_path). built_path is set only when the BUILT
    verdict came from check_circuit_file (an already-saved script) so the caller can
    report that path instead of re-slugifying and re-saving it; None otherwise.
    """
    interactive = checkpoint and sys.stdin.isatty()
    final_text = "(no final message)"
    built_path = None                            # set iff BUILT came from a file path
    built_happened = False                       # any validator returned BUILT this loop  (honesty-gate)
    design_attempt = False                       # any design/validation tool fired (footer scope)
    validated_sha1s = set()                      # sha1 of every script validate_circuit ran — anti-restage guard
    research_streak = 0                          # look-ups since the last validated circuit  (convergence guard)
    nudged_at = 0                                # research_streak value at the last convergence nudge
    wants_circuit = _task_wants_circuit(messages)  # firmware must not satisfy a PCB task  (pcb-firmware-distinct)
    # XORICS-FEATURE: coder-notebook -- externalized resolved-parts memory + dedup guard
    notebook = Notebook(task=messages[1]["content"]) if tag == "coder" and len(messages) > 1 else None
    base_system = messages[0]["content"]
    step = 0
    next_check = CHECKPOINT_EVERY
    while True:
        if not interactive and step >= CODER_BACKSTOP:
            final_text = (f"(stopped: reached the {CODER_BACKSTOP}-step backstop with no human "
                          f"watching — raise CODER_BACKSTOP or run interactively to go further)")
            break
        if interactive and step >= next_check:
            more = _coder_checkpoint(step, messages)
            if more <= 0:
                final_text = f"(stopped by you after {step} steps)"
                break
            next_check = step + more
        step += 1
        messages = _trim_history(messages)
        if notebook:  # XORICS-FEATURE: pin notebook into the always-kept head
            messages[0] = {**messages[0], "content": base_system + notebook.render()}
        active = tools
        if tag == "coder" and research_streak >= RESEARCH_NUDGE_AT:
            # convergence guard with teeth: once it fires, drop the soft web look-ups so the coder
            # cannot keep researching — it must write or stop. Restores automatically when a validated
            # circuit resets research_streak to 0. XORICS-FEATURE: convergence-guard
            active = [t for t in tools if t["function"]["name"] not in _SOFT_RESEARCH_TOOLS]
        # MiniMax M3 buries its chain-of-thought inside `content` as <think>…</think> unless
        # asked to split it out — which would pollute the honesty gate + SKiDL-fence parsing.
        # reasoning_split keeps `content` clean. Empty extra_body is a no-op for local models.
        extra = {"reasoning_split": True} if model == MINIMAX else {}
        resp = client_for(model).chat.completions.create(
            model=model, messages=messages, tools=active, extra_body=extra)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            final_text = msg.content or ""
            break
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [{"id": tc.id, "type": "function",
                                         "function": {"name": tc.function.name,
                                                      "arguments": tc.function.arguments}}
                                        for tc in msg.tool_calls]})
        built_code = None
        stopped_msg = None                       # XORICS-FEATURE: coder-control
        for tc in msg.tool_calls:
            name = tc.function.name
            if name in _DESIGN_TOOLS:            # honesty-gate: this turn attempted a design/build
                design_attempt = True
            if tag == "coder" and name in _RESEARCH_TOOLS:   # convergence guard (coder only): count look-ups
                research_streak += 1
            elif name in ("check_circuit", "check_circuit_file") or (name == "compile_check" and not wants_circuit):
                research_streak = nudged_at = 0   # committed to a real build — but firmware doesn't count on a PCB task
            args = {}
            if name == "validate_circuit":
                # Harness tool: the SKiDL script rides in the coder's message CONTENT (a fenced
                # ```python block), not the tool args — a large script truncates as a JSON arg,
                # which is the flagship blocker. Take the coder's most recent SKiDL fence (this
                # turn first, else a recent earlier turn), save it, and validate by PATH so the
                # BUILT-by-path capture below still fires. XORICS-FEATURE: validate-from-fence
                try:
                    vargs = json.loads(tc.function.arguments or "{}")
                except Exception:
                    vargs = {}
                try:
                    result, saved = _validate_circuit_from_turn(messages, vargs.get("name") or "circuit",
                                                                validated_sha1s)
                except Exception as e:
                    result, saved = (f"[validate_circuit error: {e}] — fix the script and try again.", None)
                if saved:
                    args = {"path": saved}        # route the BUILT capture at the saved file
                    research_streak = nudged_at = 0   # a script reached the grader — convergence reset
                    try:
                        with open(saved, "rb") as _f:
                            _hh = hashlib.sha1(_f.read()).hexdigest()[:8]
                    except Exception:
                        _hh = "????????"
                    print(f"  [{tag}→validate_circuit] saved → {saved}  (sha1 {_hh})")
                else:
                    print(f"  [{tag}→validate_circuit] no new SKiDL script to validate — corrective sent")
                rp = " ".join(str(result).split())
                print(f"    ↳ {rp[:160]}{'…' if len(rp) > 160 else ''}")
            else:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    preview = {k: (v[:60] + "…" if isinstance(v, str) and len(v) > 60 else v)
                               for k, v in args.items()}
                    print(f"  [{tag}→{name}]({preview})")
                    gate = notebook.gate(name, args) if notebook else None  # XORICS-FEATURE: dedup guard
                    if gate is not None:
                        result = gate  # cached echo / hard refusal; impl NOT called
                    else:
                        result = TOOL_IMPLS[name](**args)
                        if notebook:
                            notebook.record(name, args, result)  # success only; errors raise before here
                    rp = " ".join(str(result).split())
                    print(f"    ↳ {rp[:160]}{'…' if len(rp) > 160 else ''}")
                except json.JSONDecodeError as e:
                    if name == "check_circuit":
                        # The exact flagship failure: the script was too big to pass as a JSON arg and
                        # got cut off. Redirect to validate_circuit instead of letting the coder retry
                        # the same oversized inline call into the backstop. XORICS-FEATURE: validate-from-fence
                        result = ("[use validate_circuit instead: the script was too large to pass as a "
                                  f"tool argument and was truncated ({e}). Do NOT retry check_circuit with "
                                  "inline code. Write the COMPLETE SKiDL script in ONE fenced ```python "
                                  "block and, in the same message, call validate_circuit — that path saves "
                                  "the script to disk and has no size limit.]")
                    else:
                        result = f"[tool error in {name}: bad arguments ({e})] — adjust and try another approach."
                    print(f"  [{tag}→{name}] ARG-PARSE ERROR: {e}")
                except Exception as e:
                    result = f"[tool error in {name}: {e}] — adjust and try another approach."
                    print(f"  [{tag}→{name}] ERROR: {e}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
            # Structured success: check_circuit told us it BUILT (via .status, not text-matching).
            # Capture the exact script that passed so a later edit can't overwrite the win.
            if getattr(result, "status", None) == "built":
                if wants_circuit and name == "compile_check":
                    # PCB task, but only FIRMWARE compiled — a sketch is not the board. Reject it as the
                    # deliverable (don't finalize/stop) and push the coder back into SKiDL, so a firmware
                    # compile can't fake a VERIFIED board. XORICS-FEATURE: pcb-firmware-distinct
                    messages[-1]["content"] = str(messages[-1].get("content") or "") + _FIRMWARE_NOT_A_BOARD
                    print(f"  [{tag}] firmware BUILT does NOT satisfy a board task — pushing back to SKiDL")
                else:
                    built_happened = True            # honesty-gate: a real BUILT verdict landed
                    built_code = args.get("code")
                    if built_code is None and args.get("path"):
                        # check_circuit_file validates a SAVED script by PATH, so there's no inline
                        # `code` to capture. Read the verified file back so BUILT-stop still fires —
                        # without this a file-based BUILT never stopped the loop, and the coder
                        # wandered off rebuilding a board that had already passed (and broke it).
                        try:
                            from pathlib import Path as _P
                            built_path = str(_P(args["path"]).expanduser())   # report this; don't re-slug
                            built_code = _P(built_path).read_text()
                        except Exception:
                            built_code = ""   # unreadable, but it built — still stop (below)
            elif getattr(result, "status", None) == "user_stopped":  # XORICS-FEATURE: coder-control
                stopped_msg = str(result)        # human halted a delegated coder; do not re-delegate
            elif getattr(result, "status", None) == "failed" and name == "validate_circuit":
                # self-prompt the fix: the concrete errors are already in this tool result; tell the coder
                # to fix THOSE and re-validate, staying in SKiDL. XORICS-FEATURE: pcb-firmware-distinct
                messages[-1]["content"] = str(messages[-1].get("content") or "") + _FAILED_FIX_DIRECTIVE
        # Convergence guard: too many look-ups without a validated circuit → force a WRITE. We append the
        # push to the last tool result of this turn (always template-valid, unlike injecting a user turn
        # mid tool-sequence) so the coder sees it next turn; escalates if it keeps stalling, and resets the
        # moment a script is validated. This is the mechanical teeth behind the guide's "don't over-research"
        # rule, and it keeps the conversation from growing past the 8K window. XORICS-FEATURE: convergence-guard
        if _should_nudge(research_streak, nudged_at):
            for _m in reversed(messages):
                if _m.get("role") == "tool":
                    _m["content"] = (_m.get("content") or "") + _CONVERGENCE_NUDGE.format(n=research_streak)
                    break
            nudged_at = research_streak
            print(f"  [{tag}] convergence nudge — {research_streak} look-ups, no validated circuit; pushing to WRITE")
        if stopped_msg is not None:
            print("    ■ coder stopped at your request — not re-delegating.")
            final_text = stopped_msg
            break
        if built_code is not None:
            if wants_circuit:                        # PCB build: ERC + netlist actually ran  XORICS-FEATURE: pcb-firmware-distinct
                print("    ✓ CIRCUIT BUILT — finalizing the verified design and stopping the coder.")
                final_text = (
                    "check_circuit returned BUILT — ERC ran and a netlist generated. Stopping here with the "
                    "verified design (further edits are disabled so a passing board can't be broken).\n\n"
                    "```python\n" + (built_code or "") + "\n```")
            else:                                    # firmware: compile_check passed — NOT a circuit, no ERC/netlist
                print("    ✓ SKETCH COMPILED — finalizing the verified firmware and stopping the coder.")
                final_text = (
                    "compile_check returned COMPILE OK — the sketch builds clean. Stopping here with the "
                    "verified firmware (further edits are disabled so a passing build can't be broken).\n\n"
                    "```cpp\n" + (built_code or "") + "\n```")
            break
    return final_text, messages, built_path, {"built": built_happened, "design_attempt": design_attempt}


# ---- The coder sub-session (runs on the coder brain, returns a saved file) -----
def run_coder(task: str) -> str:
    """Run the coder brain on `task` until it produces verified code, save it, return summary+path.
    A firmware-ONLY task (firmware signal present, NO circuit signal) runs on a firmware-led prompt with
    the PCB design/validate tools removed, so a weak coder can't misroute into SKiDL — the manager always
    splits firmware vs PCB into separate delegations, so that's safe. Anything else keeps the full coder
    toolset + the firmware-AND-PCB guide as before. XORICS-FEATURE: pcb-firmware-distinct
    """
    messages = [
        {"role": "system", "content": ""},       # set below, once the task is typed
        {"role": "user", "content": task},
    ]
    firmware_only = _task_wants_firmware(messages) and not _task_wants_circuit(messages)
    if firmware_only:
        messages[0]["content"] = ("You are the Xorics coding specialist (qwen3-coder), a firmware "
                                  "co-pilot. " + _FIRMWARE_GUIDE)
        coder_tools = FIRMWARE_TOOLS              # PCB tools dropped → can't misroute a firmware task
    else:
        messages[0]["content"] = ("You are the Xorics coding specialist (qwen3-coder), a firmware AND "
                                  "PCB co-pilot. " + _CODER_GUIDE)
        coder_tools = CODER_TOOLS
    final_text, messages, built_path, outcome = _agent_loop(CODER, messages, coder_tools, checkpoint=True, tag="coder")

    if final_text.startswith("(stopped"):
        snap = _snapshot_wip(messages, task)
        if snap:
            return _ToolResult(f"{final_text}\n\n[Xorics snapshotted the in-progress design to: {snap}]",
                               "user_stopped")   # XORICS-FEATURE: coder-control
        return _ToolResult(final_text + "\n\n[Nothing to snapshot yet — no code was submitted to a validator.]",
                           "user_stopped")

    if built_path:
        # BUILT came from check_circuit_file — the script is already saved at built_path.
        # Report it; re-saving here would just re-slug the task into a junk directory.
        _record_deliverable(built_path, "check_circuit_file")   # honesty-gate: verified to disk
        return f"{final_text}\n\n[Xorics verified the saved deliverable at: {built_path}]"
    path = _save_deliverable(final_text, task)
    if path and outcome["built"]:               # honesty-gate: only record a file a validator passed
        _record_deliverable(path, "compile_check" if str(path).endswith(".ino") else "check_circuit")
    if path:
        return f"{final_text}\n\n[Xorics saved the verified deliverable to: {path}]"
    return final_text + "\n\n[No code block found to save as a file.]"


def delegate_to_coder(task: str) -> str:
    """Manager-side tool: hand off to the coder, then return its result (a swap each way)."""
    print(f"  [handoff] {BRAIN} → {CODER}: {task[:70]}")
    result = run_coder(task)
    print(f"  [handoff] {CODER} → {BRAIN} (done; control returned)")
    return result


TOOL_IMPLS = {
    "web_search": web_search,
    "see_image": see_image,
    "search_datasheets": search_datasheets,
    "fetch_datasheet": fetch_datasheet,
    "compile_check": compile_check,
    "check_circuit": check_circuit,
    "check_circuit_file": check_circuit_file,
    "read_file": read_file,
    "find_part": find_part,
    "find_footprint": find_footprint,
    "part_pins": part_pins,
    "delegate_to_coder": delegate_to_coder,
}


def active_tools():
    # manual /code mode drives the coder directly; otherwise the manager delegates.
    return CODER_TOOLS if BRAIN == CODER else MANAGER_TOOLS


# ---- Conversation memory ------------------------------------------------------
# The manager (/chat) keeps a running transcript so it remembers earlier turns
# instead of cold-starting every message. Only clean user/assistant pairs are kept
# — never the coder's internal grind or raw tool scaffolding — so context stays
# compact and the coder/grader path is completely unaffected. The transcript is
# persisted to disk so it ALSO survives a restart; the model is only ever fed the
# most recent slice (CHAT_HISTORY_MSGS) so context can't overflow.
_CHAT_HISTORY = []
CHAT_HISTORY_MSGS = 16     # how many recent messages the model actually sees (~8 exchanges)
MAX_STORED_MSGS = 1000     # safety cap on how much transcript is kept on disk

_STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
_HISTORY_PATH = os.path.join(_STATE_DIR, "chat_history.json")

def _load_history():
    # Pull the saved transcript back in on startup; missing or corrupt -> start fresh.
    try:
        with open(_HISTORY_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []

def _save_history():
    # Atomic write (temp file + rename) so an interrupted save can't corrupt the store.
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        tmp = _HISTORY_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_CHAT_HISTORY, f, indent=2)
        os.replace(tmp, _HISTORY_PATH)
    except OSError:
        pass   # a failed save must never take the conversation down

def _remember(user_message, reply):
    _CHAT_HISTORY.append({"role": "user", "content": user_message})
    _CHAT_HISTORY.append({"role": "assistant", "content": reply})
    if len(_CHAT_HISTORY) > MAX_STORED_MSGS:          # keep the on-disk log bounded
        del _CHAT_HISTORY[:len(_CHAT_HISTORY) - MAX_STORED_MSGS]
    _save_history()

def reset_history():
    _CHAT_HISTORY.clear()
    _save_history()
    _clear_deliverables()                        # honesty-gate: fresh session


# ---- Honesty gate: verified-deliverable manifest + finalize --------------------
# "Done" is mechanical, not narrated: a design is finished only if a board actually BUILT this
# session AND every claimed file exists on disk. The manifest (written only when a build is
# verified) is the single source of truth; finalize_design checks it, and ask() appends a
# machine-generated footer so the manager can't report success/paths that never happened.
# Mirrors CheckResult.status — structured, not text-matched. XORICS-FEATURE: honesty-gate
def _deliverables_path():
    return os.path.join(_STATE_DIR, "deliverables.json")

def _load_deliverables():
    try:
        with open(_deliverables_path()) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def _clear_deliverables():
    try:
        if os.path.exists(_deliverables_path()):
            os.remove(_deliverables_path())
    except Exception:
        pass

def _record_deliverable(path, validator):
    """Append a VERIFIED deliverable to the session manifest (atomic). Only called after a build passes."""
    rec = {"path": os.path.abspath(os.path.expanduser(str(path))), "validator": validator, "ts": time.time()}
    data = _load_deliverables()
    data.append(rec)
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        tmp = _deliverables_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, _deliverables_path())
    except Exception as e:
        print(f"  [manifest] could not record deliverable: {e}")
    return rec

def finalize_design(paths=None):
    """Honesty gate (manager-facing). A design is finished only if a board BUILT this session AND every
    claimed file exists on disk and passed a validator. Returns VERIFIED / CANNOT FINALIZE with a
    .status the manager loop can't fake. XORICS-FEATURE: honesty-gate"""
    paths = [p for p in (paths or []) if str(p).strip()]
    manifest = _load_deliverables()
    if not manifest:
        return _ToolResult("CANNOT FINALIZE — no BUILT verdict on record this session. Nothing has passed "
                           "check_circuit / check_circuit_file, so there is no verified design to finalize. "
                           "Delegate the build and get a BUILT result first.", "unverified")
    verified = {r["path"] for r in manifest}
    missing = [p for p in paths if not os.path.exists(os.path.expanduser(p))]
    if missing:
        return _ToolResult("CANNOT FINALIZE — these claimed files do not exist on disk: "
                           + ", ".join(missing) + ". Only claim files that were actually written.",
                           "unverified")
    unvalidated = [p for p in paths if os.path.abspath(os.path.expanduser(p)) not in verified]
    if unvalidated:
        return _ToolResult("CANNOT FINALIZE — these files exist but never passed a validator this session: "
                           + ", ".join(unvalidated) + ". Run them through check_circuit_file first.",
                           "unverified")
    listing = "\n".join(f"  - {r['path']}  [{r['validator']}]" for r in manifest)
    return _ToolResult("VERIFIED — every claimed deliverable built/compiled this session and exists on disk.\n"
                       "Verified deliverables:\n" + listing, "verified")

TOOL_IMPLS["finalize_design"] = finalize_design   # register now that it's defined

def _append_manifest_footer(text, outcome, deliv_before):
    """Machine-generated truth footer. Fires only on design turns, reporting whether a board actually
    verified to disk THIS turn — so a fabricated 'complete' is visibly contradicted by ground truth.
    XORICS-FEATURE: honesty-gate"""
    if not (outcome or {}).get("design_attempt"):
        return text
    fresh = _load_deliverables()[deliv_before:]
    on_disk = [r for r in fresh if os.path.exists(os.path.expanduser(r["path"]))]
    if on_disk:
        files = ", ".join(os.path.basename(r["path"]) for r in on_disk)
        return text + "\n\n\u2713 VERIFIED — built and verified on disk this turn: " + files
    return text + ("\n\n\u26a0 UNVERIFIED — a design task ran but nothing passed a validator and no new "
                   "file was verified to disk this turn. Any 'complete' claim or file paths above are "
                   "unconfirmed.")


# ---- The agent loop -----------------------------------------------------------
def ask(user_message: str, history=None) -> str:
    """One turn of conversation.

    history: prior turns as [{"role": "user"|"assistant", "content": str}, ...], oldest first —
    the context BEFORE this turn (store.history_for_model(chat_id) for the bridge/app, or the
    REPL's own recent slice). user_message is the NEW turn, appended here, so the caller must NOT
    include it in history. None/[] -> single-shot. ask() is STATELESS: it never reads or writes a
    global transcript, so persistence is the caller's job — store.py for the app, the REPL for the
    console. Existing single-arg callers are unaffected. XORICS-FEATURE: stateless-history
    """
    is_coder = BRAIN == CODER
    if is_coder:
        system = f"You are {NAME}, the coding specialist, in manual coding mode. " + _CODER_GUIDE
    else:
        system = _MANAGER_PERSONA + "\n\n" + _MANAGER_ROUTING
    messages = [{"role": "system", "content": system}]
    if history:                                  # prior turns: spliced between system and the new turn
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    # Only the coder grind gets human check-ins; the manager just routes (backstop only).
    _deliv_before = len(_load_deliverables())    # honesty-gate: footer diffs deliverables added this turn
    final_text, messages, built_path, outcome = _agent_loop(BRAIN, messages, active_tools(),
                                                   checkpoint=is_coder, tag=("coder" if is_coder else "tool"))

    if is_coder:
        if final_text.startswith("(stopped"):
            snap = _snapshot_wip(messages, user_message)
            if snap:
                final_text += f"\n[Xorics snapshotted the in-progress design to: {snap}]"
    else:
        final_text = _append_manifest_footer(final_text, outcome, _deliv_before)   # honesty gate
    out = _ToolResult(final_text)        # str-compatible; carries built_path for the REPL save
    out.built_path = built_path
    return out


# ---- REPL ---------------------------------------------------------------------
if __name__ == "__main__":
    _CHAT_HISTORY[:] = _load_history()      # resume the saved conversation, if any
    if "--voice" in sys.argv:
        from voice import voice_loop
        voice_loop(ask)
        sys.exit()

    print(f"{NAME} — local AI. The manager delegates coding to the coder automatically.")
    print("commands: /code (coder)  /chat or /local (gpt-oss manager)  /power (MiniMax M3 manager)  /reset  Ctrl+C quit")
    print(f"coder pauses every {CHECKPOINT_EVERY} steps to check in (no cap); backstop {CODER_BACKSTOP} when unattended.\n")
    if _CHAT_HISTORY:
        print(f"(resumed {len(_CHAT_HISTORY)} remembered messages — /reset to start fresh)\n")
    while True:
        try:
            tag = "code" if BRAIN == CODER else ("power" if BRAIN == MINIMAX else "chat")
            q = input(f"you[{tag}]> ").strip()
            if not q:
                continue
            if q == "/code" or q.startswith("/code "):   # XORICS-FEATURE: coder-control
                BRAIN = CODER; print("→ manual coding mode (driving qwen3-coder directly)\n")
                q = q[5:].strip()
                if not q:
                    continue
            elif q == "/chat" or q.startswith("/chat "):
                BRAIN = MANAGER; print("→ manager mode (gpt-oss; delegates coding)\n")
                q = q[5:].strip()
                if not q:
                    continue
            elif q == "/power" or q.startswith("/power "):   # XORICS-FEATURE: power-mode
                if os.environ.get("MINIMAX_API_KEY"):
                    BRAIN = MINIMAX
                    print(f"→ POWER mode (manager = {MINIMAX}, remote; coder stays local on {CODER})\n")
                else:
                    print("✗ MINIMAX_API_KEY not set — export it (and set a spend cap) first. Staying put.\n")
                q = q[6:].strip()
                if not q:
                    continue
            elif q == "/local" or q.startswith("/local "):   # off-switch for /power (same as /chat)
                BRAIN = MANAGER; print(f"→ local manager mode ({MANAGER})\n")
                q = q[6:].strip()
                if not q:
                    continue
            elif q == "/reset" or q == "/new":
                reset_history(); print("→ conversation cleared — fresh context\n")
                continue
            # manager/power turns carry the running transcript (persisted by _remember); coder turns
            # are task-scoped, no history. ask() is stateless now, so the REPL owns this. 
            hist = None if BRAIN == CODER else _CHAT_HISTORY[-CHAT_HISTORY_MSGS:]
            ans = ask(q, history=hist)
            print(f"\n{NAME.lower()}>", ans, "\n")
            if BRAIN == CODER:                       # direct-drive: save the deliverable too
                bp = getattr(ans, "built_path", None)
                if bp:                               # BUILT came from an existing file — don't re-save
                    print(f"  [verified] {bp}\n")
                else:
                    p = _save_deliverable(ans, q)
                    if p:
                        print(f"  [saved] {p}\n")
            else:
                _remember(q, str(ans))               # accrue + persist the manager transcript
        except (KeyboardInterrupt, EOFError):
            print(f"\n{NAME} signing off.")
            break
