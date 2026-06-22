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
firmware_tools.py - the compile-check engine for Xorics' coding mode.

compile_check() is a normal CPU-bound tool (same species as web_search): it takes
source the coder wrote, runs the REAL toolchain on it, and returns the verdict -
pass with flash/RAM usage, or the actual compiler errors. Feeding that verdict
back into the coder's loop is what closes the plausible-vs-correct gap: the model
can't hand you firmware that only looks right, because the compiler grades it first.

Wire into xorics.py like the other tools:
    from firmware_tools import compile_check, save_sketch   # import
    TOOLS.append({ ...schema... })                          # one TOOLS entry
    TOOL_IMPLS["compile_check"] = compile_check             # one TOOL_IMPLS line

Prereqs (one-time):
    arduino-cli core update-index
    arduino-cli core install esp32:esp32
"""

from __future__ import annotations
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

DEFAULT_FQBN = "esp32:esp32:esp32c3"   # generic ESP32-C3 dev module
BUILD_TIMEOUT = 300                    # seconds; first build is the slow one
MAX_OUTPUT = 4000                      # cap returned text; errors live at the end

# Where save_sketch() writes finished firmware. Override with XORICS_SKETCHES.
SKETCH_DIR = Path(os.environ.get("XORICS_SKETCHES", Path.home() / "xorics-ai" / "sketches"))


class CompileResult(str):
    """A compile_check result that reads as its normal text for the coder, but also carries a
    machine-readable .status ('built' | 'failed' | 'timeout' | 'no_toolchain') for the agent loop.

    Same contract as pcb_tools.CheckResult: the loop inspects .status instead of string-matching the
    human text, so a compiled sketch (status 'built') triggers the BUILT-stop and gets written to the
    honesty-gate deliverable manifest exactly like a built board. Reword the messages freely.
    """
    def __new__(cls, text, status):
        obj = super().__new__(cls, text)
        obj.status = status
        return obj


def compile_check(code: str, fqbn: str = DEFAULT_FQBN) -> "CompileResult":
    """
    Compile an Arduino-framework sketch with arduino-cli and report the result.
    Returns a CompileResult (reads as text, carries .status): 'built' on success (with flash/RAM
    usage), 'failed' with compiler errors, 'timeout', or 'no_toolchain'. The .status is what lets
    xorics' loop treat a compiled sketch like a built board (BUILT-stop + deliverable manifest).
    """
    if shutil.which("arduino-cli") is None:
        return CompileResult("arduino-cli not found. Install it, then the ESP32 core:\n"
                             "  arduino-cli core update-index\n"
                             "  arduino-cli core install esp32:esp32", "no_toolchain")

    # arduino-cli expects sketch.ino inside a folder named 'sketch'
    workdir = tempfile.mkdtemp(prefix="xorics_build_")
    sketch_dir = os.path.join(workdir, "sketch")
    os.makedirs(sketch_dir, exist_ok=True)
    with open(os.path.join(sketch_dir, "sketch.ino"), "w") as f:
        f.write(code)

    try:
        proc = subprocess.run(
            ["arduino-cli", "compile", "--fqbn", fqbn, "--warnings", "all", sketch_dir],
            capture_output=True, text=True, timeout=BUILD_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return CompileResult(f"COMPILE TIMEOUT: build exceeded {BUILD_TIMEOUT}s.", "timeout")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if len(out) > MAX_OUTPUT:                       # keep the tail; that's where errors are
        out = "...(truncated)...\n" + out[-MAX_OUTPUT:]

    if proc.returncode == 0:
        # On success arduino-cli prints flash/RAM usage - ground-truth size info.
        return CompileResult("COMPILE OK\n" + out, "built")
    return CompileResult(f"COMPILE FAILED (exit {proc.returncode})\n" + out, "failed")


def extract_code(text: str) -> str | None:
    """Return the longest fenced code block from the coder's final message (or None)."""
    blocks = re.findall(r"```(?:cpp|c\+\+|c|ino|arduino)?\s*\n?(.*?)```", text, re.DOTALL)
    blocks = [b.strip() for b in blocks if b.strip()]
    return max(blocks, key=len) if blocks else None


def save_sketch(code: str, name: str = "sketch") -> str:
    """Write code as a proper Arduino sketch (sketches/<slug>/<slug>.ino); return the path."""
    slug = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")[:40] or "sketch"
    d = SKETCH_DIR / slug
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slug}.ino"
    path.write_text(code)
    return str(path)


if __name__ == "__main__":
    # self-test the packaging path (no toolchain needed)
    sample = "Here is the sketch:\n```cpp\nvoid setup(){}\nvoid loop(){}\n```\nDone."
    code = extract_code(sample)
    print("extracted:", repr(code))
    print("saved to:", save_sketch(code or "// empty", "blink test C3"))
