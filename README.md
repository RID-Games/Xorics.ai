# Xorics

A fully self-hosted, modular local AI assistant for embedded / PCB engineering — built from
scratch on a single Arch Linux box, with no external agent framework. A generalist **manager**
brain holds the conversation and delegates coding to a specialist **coder** brain; the two are
hot-swapped on one GPU. The coder researches, writes, validates against real graders (Arduino
compile for firmware, SKiDL + ERC + a power-topology check for PCBs), and saves a finished
deliverable.

This is a personal learning project: every layer is built by hand and proven before the next is
added. Xorics itself is **AGPL-3.0** (strong copyleft — modifications, including hosted ones, must
share source); the dependencies it builds on are permissive (gpt-oss/Qwen Apache-2.0, SKiDL MIT).

## Why from scratch

The point isn't to ship the shortest path to a working agent — it's to understand the *why* of
every architectural decision. So the agent loop, the externalized coder memory ("notebook"), the
category-aware part resolver, and the layered graders are all native code rather than a forked
framework.

## Hardware / stack

Targets a single workstation (`RIDGames`), driven remotely from a phone over Tailscale + tmux:

- **GPU:** AMD Radeon RX 9060 XT (16 GB, RDNA4, gfx1200/1201)
- **CPU/RAM:** Ryzen 7 3700X, ~31 GB
- **OS:** Arch Linux, KDE/Wayland
- **Inference:** [llama.cpp](https://github.com/ggml-org/llama.cpp) built from source with the
  **Vulkan** backend (the correct path on gfx1200 — Ollama falls back to CPU there), hot-swapped
  via [llama-swap](https://github.com/mostlygeek/llama-swap) on `:9090`

## Architecture

Two brains share the GPU through llama-swap; CPU specialists stay always-on:

| Role | Model | Endpoint | License |
|------|-------|----------|---------|
| Manager (routes + delegates) | gpt-oss | llama-swap `:9090` | Apache-2.0 |
| Coder (writes/validates/saves) | Qwen3-Coder-30B-A3B | llama-swap `:9090` | Apache-2.0 |
| Vision | Gemma-3-4B (VLM) | `:8081` | — |
| Embeddings (RAG) | — | `:8082` | — |
| Speech (Whisper) | — | `:8084` | — |

**Delegation flow:** `you → manager → delegate_to_coder(task)` [swap in coder] → coder
researches/writes/validates/saves → returns summary + path [swap back] → manager summarizes.
You can also drive the coder directly with `/code <task>`.

### Coder loop

A generic tool loop (no framework). Key pieces:

- **Notebook** — externalized, context-trim-proof memory pinned into the system message every
  turn. Auto-records resolved lookups and hard-refuses identical repeats, so the coder can't spin
  re-fetching parts it already found. Seeded with a **house-parts** block of canonical staples.
- **Checkpoints** — no hard step cap; an interactive run pauses every few steps for a human
  go/stop, with an unattended backstop so an automated run can't loop forever.
- **History trimming** — old tool bodies are compressed each turn to stay under the coder's 32K
  window without orphaning tool-call pairs.

### PCB pipeline (SKiDL)

KiCad symbol libraries aren't parametric, so parts are chosen by **category**, not name:

- **`find_part`** — searches, then *validates each hit by instantiation* (search ≠ loadable),
  and orders by category read off each symbol's `ref_prefix`/keywords/description. Generic headers
  resolve by **geometry** (`Header 2x14` → `Conn_02x14_Odd_Even`); a bare `Header` query asks for
  the size instead of surfacing a specialty (JTAG) header.
- **`part_pins`** — real KiCad pin names (which differ from datasheet names), so the script
  connects to pins that exist.
- **`check_circuit` / `check_circuit_file`** — runs the SKiDL script in an isolated venv, executes
  ERC, generates a netlist, then applies a **power-topology grader** (catches merged voltage
  domains, a regulator shorted input-to-output, a rail shorted to ground) that ERC can't. Returns
  a machine-readable BUILT/FAILED status.

## Layout

```
xorics.py          # entry point: REPL, manager/coder brains, agent loop, tool registry
notebook.py        # externalized coder memory (auto-record, dedup guard, house-parts seed)
pcb_tools.py       # SKiDL runner, find_part category resolver, ERC + power graders
firmware_tools.py  # arduino-cli compile_check + sketch saving
datasheet_rag.py   # local datasheet/RAG retrieval (:8082)
web_datasheets.py  # fetch a datasheet PDF from the web and index it
voice.py           # optional Whisper + TTS wrapper (--voice)
apply-*.sh         # self-contained, idempotent, plan-by-default patch scripts (reproducibility)
circuits/          # saved SKiDL designs (curated *_BUILT.py boards tracked; per-run dirs ignored)
```

## Running

```bash
cd ~/xorics-ai && source venv/bin/activate && python xorics.py
```

Commands inside the REPL: `/chat <msg>` (manager), `/code <task>` (drive the coder directly),
`Ctrl+C` to quit. Add `--voice` to wrap the loop in speech.

## Status

Early but working. A design has run end-to-end to a verified BUILT (script → ERC → netlist →
power-topology pass). Active work: hardening the part resolver and graders, a stronger
IPC-2221 physics grader, the voice pipeline, and a compile-check tool for ESP-IDF/arduino-cli
firmware. Longer term: AMD RDNA4 inference/training contribution work (Vulkan/ROCm) and a PCB
trace-routing subsystem.

## License

Xorics is licensed under the **GNU Affero General Public License v3.0 or later** — see
[LICENSE](LICENSE). Strong copyleft: anyone who distributes a modified version, **or runs one as a
network service**, must make the corresponding source available under the same terms.

**Output exception.** Designs Xorics *produces* — generated SKiDL scripts, netlists, schematics,
and board layouts — are **not** covered by the AGPL, nor are template/parts fragments Xorics embeds
into that output. You may license your generated designs however you wish, including proprietary
terms. This is an explicit additional permission under AGPLv3 §7; see
[LICENSE-EXCEPTION](LICENSE-EXCEPTION). (The exception covers *output*, not the Program itself —
modifying Xorics' own source stays under the AGPL.)

**Keeping proprietary code separate.** The copyleft binds only code that *incorporates or derives
from* Xorics' source. Separate proprietary programs may *use* Xorics as an arm's-length tool —
running it as its own process and consuming its output — without becoming subject to the AGPL. The
bright line: do **not** `import` Xorics modules into closed code, and do not paste Xorics source
into it. Closed apps should live in their **own repositories**, never inside this tree.

## Contributing

Contributions are welcome under a contributor-friendly arrangement: **you keep your copyright**, the
public gets your work under the AGPL, and the maintainer gets a standing license to use and
relicense it. Every commit must carry a Developer Certificate of Origin sign-off (`git commit -s`).
See [CONTRIBUTING.md](CONTRIBUTING.md) and [CLA.md](CLA.md).
