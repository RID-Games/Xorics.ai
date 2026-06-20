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
import json
import re
import sys
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

NAME = "Xorics"
MANAGER = "gpt-oss"
CODER = "qwen3-coder"

# Coder loop pacing. No hard step cap — instead, in an interactive session the coder
# pauses for a human check-in this often; with no TTY it stops at the backstop so an
# unattended run can't loop forever. Tune CHECKPOINT_EVERY to taste.
CHECKPOINT_EVERY = 5
CODER_BACKSTOP = 40

# Brain endpoint = llama-swap. Ask for a model by name; it loads/evicts on the GPU.
client = OpenAI(base_url="http://127.0.0.1:9090/v1", api_key="not-needed")
# Vision specialist, reached directly (CPU, always on).
vision_client = OpenAI(base_url="http://127.0.0.1:8081/v1", api_key="not-needed")

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
                       "check_circuit with the corrected code inline.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Full path to a saved SKiDL .py, e.g. "
                     "~/xorics-ai/circuits/<name>/<name>.py."}},
            "required": ["path"]}}},
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
]

# Manager (gpt-oss) routes + delegates; it does NOT compile directly.
MANAGER_TOOLS = [t for t in TOOLS if t["function"]["name"]
                 in ("web_search", "see_image", "search_datasheets", "delegate_to_coder")]
# Coder's own toolset (used inside delegate_to_coder and in manual /code mode).
CODER_TOOLS = [t for t in TOOLS if t["function"]["name"]
               in ("compile_check", "check_circuit", "check_circuit_file", "find_part", "part_pins",
                   "find_footprint",
                   "search_datasheets", "fetch_datasheet", "web_search", "read_file")]


# Shared coder guidance (used by delegation and manual /code), tuned to avoid thrashing.
_CODER_GUIDE = (
    "Look up real pins/specs with search_datasheets (fetch_datasheet if missing); web_search for errors or "
    "APIs you're unsure of. Don't over-research — after a couple of lookups, WRITE the design and let the "
    "validator give you feedback; iterate from real errors, not endless searching.\n"
    "FIRMWARE: write an Arduino sketch, call compile_check, fix until it builds. Final code in one ```cpp block.\n"
    "PCB / circuit: design in SKiDL (Python). Essentials:\n"
    "  from skidl import *\n"
    "  r = Part('Device','R', value='10k', footprint='Resistor_SMD:R_0402_1005Metric')\n"
    "  vcc, gnd = Net('3V3'), Net('GND');  vcc += r[1];  gnd += r[2]\n"
    "  ERC(); generate_netlist()\n"
    "Anti-thrash rules:\n"
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
    "check_circuit now FAILS a board that merges 5V into 3V3 or shorts a regulator's VI to VO.\n"
    "- Connect pins to Nets, END with ERC() then generate_netlist(), then call check_circuit. If it reports a "
    "part not found, find_part and fix that ONE Part(...) call, then re-check. Keep fixing in SKiDL until it "
    "builds — NEVER fall back to prose or a firmware sketch for a PCB task.\n"
    "- If check_circuit FAILS, fix the CODE from the error message; do NOT re-search a part you already found "
    "— you already have its library:name from find_part, so the bug is in the script, not the lookup.\n"
    "- NEVER reach BUILT by deleting connections: a part with nothing wired to it is not a board. If a "
    "pin name is rejected, get the real name from part_pins and RECONNECT — do not strip the design "
    "down to a lone unconnected part just to pass the check.\n"
    "- When check_circuit returns BUILT, you are DONE: do NOT swap parts, refactor, or \"improve\" a "
    "passing design — output the final code and stop. A built board you keep editing is how good ones break.\n"
    "Finish with the final code in a single fenced block, then one short line: what it does and the pins/specs used."
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
        resp = client.chat.completions.create(model=model, messages=messages, tools=tools)
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
            args = {}
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
            except Exception as e:
                result = f"[tool error in {name}: {e}] — adjust and try another approach."
                print(f"  [{tag}→{name}] ERROR: {e}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
            # Structured success: check_circuit told us it BUILT (via .status, not text-matching).
            # Capture the exact script that passed so a later edit can't overwrite the win.
            if getattr(result, "status", None) == "built":
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
        if stopped_msg is not None:
            print("    ■ coder stopped at your request — not re-delegating.")
            final_text = stopped_msg
            break
        if built_code is not None:
            print("    ✓ CIRCUIT BUILT — finalizing the verified design and stopping the coder.")
            final_text = (
                "check_circuit returned BUILT — ERC ran and a netlist generated. Stopping here with the "
                "verified design (further edits are disabled so a passing board can't be broken).\n\n"
                "```python\n" + (built_code or "") + "\n```")
            break
    return final_text, messages, built_path


# ---- The coder sub-session (runs on the coder brain, returns a saved file) -----
def run_coder(task: str) -> str:
    """Run the coder brain on `task` until it produces verified code, save it, return summary+path."""
    messages = [
        {"role": "system",
         "content": "You are the Xorics coding specialist (qwen3-coder), a firmware AND PCB co-pilot. " + _CODER_GUIDE},
        {"role": "user", "content": task},
    ]
    final_text, messages, built_path = _agent_loop(CODER, messages, CODER_TOOLS, checkpoint=True, tag="coder")

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
        return f"{final_text}\n\n[Xorics verified the saved deliverable at: {built_path}]"
    path = _save_deliverable(final_text, task)
    if path:
        return f"{final_text}\n\n[Xorics saved the verified deliverable to: {path}]"
    return final_text + "\n\n[No code block found to save as a file.]"


def delegate_to_coder(task: str) -> str:
    """Manager-side tool: hand off to the coder, then return its result (a swap each way)."""
    print(f"  [handoff] {MANAGER} → {CODER}: {task[:70]}")
    result = run_coder(task)
    print(f"  [handoff] {CODER} → {MANAGER} (done; control returned)")
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


# ---- The agent loop -----------------------------------------------------------
def ask(user_message: str) -> str:
    if BRAIN == CODER:
        system = f"You are {NAME}, the coding specialist, in manual coding mode. " + _CODER_GUIDE
    else:
        system = (f"You are {NAME}, a helpful AI assistant running locally on the user's hardware. You are "
                  f"the manager: hold the conversation and route work. For ANY firmware, code, OR "
                  f"PCB/circuit-design request (write/modify/debug firmware, or design a board/circuit), "
                  f"call delegate_to_coder with a complete task description — "
                  f"the coder will research, write, compile-verify, and SAVE the code, then hand back a "
                  f"summary and file path. After it returns, give the user a brief summary and the saved "
                  f"path; do NOT re-paste the full code. Use web_search for current info, see_image for "
                  f"images, search_datasheets for quick hardware lookups. Refer to yourself as {NAME}.")

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user_message}]

    # Only the coder grind gets human check-ins; the manager just routes (backstop only).
    is_coder = BRAIN == CODER
    final_text, messages, built_path = _agent_loop(BRAIN, messages, active_tools(),
                                                   checkpoint=is_coder, tag=("coder" if is_coder else "tool"))

    if is_coder and final_text.startswith("(stopped"):
        snap = _snapshot_wip(messages, user_message)
        if snap:
            final_text += f"\n[Xorics snapshotted the in-progress design to: {snap}]"
    out = _ToolResult(final_text)        # str-compatible; carries built_path for the REPL save
    out.built_path = built_path
    return out


# ---- REPL ---------------------------------------------------------------------
if __name__ == "__main__":
    if "--voice" in sys.argv:
        from voice import voice_loop
        voice_loop(ask)
        sys.exit()

    print(f"{NAME} — local AI. The manager delegates coding to the coder automatically.")
    print("commands: /code (drive coder directly)  /chat (manager)  Ctrl+C quit")
    print(f"coder pauses every {CHECKPOINT_EVERY} steps to check in (no cap); backstop {CODER_BACKSTOP} when unattended.\n")
    while True:
        try:
            tag = "code" if BRAIN == CODER else "chat"
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
            ans = ask(q)
            print(f"\n{NAME.lower()}>", ans, "\n")
            if BRAIN == CODER:                       # direct-drive: save the deliverable too
                bp = getattr(ans, "built_path", None)
                if bp:                               # BUILT came from an existing file — don't re-save
                    print(f"  [verified] {bp}\n")
                else:
                    p = _save_deliverable(ans, q)
                    if p:
                        print(f"  [saved] {p}\n")
        except (KeyboardInterrupt, EOFError):
            print(f"\n{NAME} signing off.")
            break
