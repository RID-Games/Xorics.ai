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
pcb_tools.py — run + package SKiDL circuit designs for Xorics' PCB side.

check_circuit() : write the coder's SKiDL script, run it in the isolated skidl venv with
                  KiCad symbols on the path, and report the verdict — did it build, did a
                  netlist generate, what did ERC say. The compile_check analog for circuits
                  (ERC is the grader; it's weaker than a compiler, which is why a physics
                  calculator comes later as the strong grader).
save_circuit()  : save a finished SKiDL script as a .py deliverable.

The coder's script is expected to end with ERC() and generate_netlist().
"""
from __future__ import annotations
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
import netlist_query as nq

SKIDL_PYTHON = os.environ.get(
    "XORICS_SKIDL_PYTHON", str(Path.home() / "xorics-ai" / "skidl-venv" / "bin" / "python"))
KICAD_SYMBOL_DIR = os.environ.get("KICAD_SYMBOL_DIR", "/usr/share/kicad/symbols")
RUN_TIMEOUT = 180
MAX_OUTPUT = 4000
CIRCUIT_DIR = Path(os.environ.get("XORICS_CIRCUITS", Path.home() / "xorics-ai" / "circuits"))

# SKiDL prints these harmless warnings on every part lookup (footprints aren't needed for
# ERC/netlist, and we only set one symbol-dir var). They were burying the real traceback /
# ERC error in check_circuit's output, so the coder debugged blind. Strip them.
_SKIDL_NOISE = ("fp-lib-table", "SYMBOL_DIR environment variable is missing")


def _strip_skidl_noise(text: str) -> str:
    """Drop SKiDL's footprint/symbol-dir warning lines so the real error stays visible."""
    kept, prev_blank = [], False
    for ln in str(text).splitlines():
        if any(n in ln for n in _SKIDL_NOISE):
            continue
        blank = not ln.strip()
        if blank and prev_blank:          # collapse the gaps the removed lines leave behind
            continue
        kept.append(ln)
        prev_blank = blank
    return "\n".join(kept).strip()


class CheckResult(str):
    """A check_circuit result that reads as its normal text for the coder, but also carries a
    machine-readable .status ('built' | 'failed' | 'timeout' | 'no_skidl') for the agent loop.

    This is the single source of truth for 'did it build' — the loop inspects .status instead of
    string-matching the human text, so reword the messages freely without breaking control flow.
    """
    def __new__(cls, text, status):
        obj = super().__new__(cls, text)
        obj.status = status
        return obj


# Appended to the coder's script and run in the SAME skidl process AFTER generate_netlist(), so it
# can walk the live circuit (full pin names/parts) and emit one machine-readable line. Wrapped so it
# can NEVER break a passing build — if anything goes wrong it prints an _ERR line and we skip the
# power check (degrade, never regress). Uses _x-prefixed names so it can't clobber the coder's vars.
_POWER_INSPECTOR = '''

# ===== XORICS power-topology inspector (auto-appended — do not edit the design above) =====
try:
    import json as _xj
    import skidl as _xsk
    _xc = None
    try:
        from skidl import default_circuit as _xc      # classic SKiDL
    except Exception:
        _xc = (getattr(_xsk, "default_circuit", None)
               or getattr(getattr(_xsk, "circuit", None), "default_circuit", None))
    if _xc is None:
        _xc = _xsk.Net().circuit          # any net carries its (default) circuit — version-proof
    _xnets = []
    for _n in _xc.nets:
        _nm = str(getattr(_n, "name", "") or "")
        _nd = []
        for _pp in _n.pins:
            _nd.append([str(getattr(getattr(_pp, "part", None), "ref", "?")),
                        str(getattr(_pp, "num", "")), str(getattr(_pp, "name", ""))])
        _xnets.append([_nm, _nd])
    _xparts = []
    for _p in _xc.parts:
        _xlib = (getattr(getattr(_p, "lib", None), "filename", "")
                 or getattr(getattr(_p, "lib", None), "name", "") or "")
        _xfp = str(getattr(_p, "footprint", "") or "")
        _xparts.append([str(getattr(_p, "ref", "?")), str(getattr(_p, "name", "")),
                        str(_xlib),
                        [[str(getattr(_pp, "num", "")), str(getattr(_pp, "name", ""))] for _pp in _p.pins],
                        _xfp])
    print("XORICS_POWER_JSON:" + _xj.dumps({"nets": _xnets, "parts": _xparts}))
except Exception as _xe:
    print("XORICS_POWER_ERR:" + repr(_xe))
'''

# The strong-grader checks below are composed from the Layer-1 netlist_query
# primitives (one tested join, no duplicated pin-matching). netlist_query also
# owns the voltage-token and non-electrical logic, so the fragile name-only
# checkers that used to live here — which the [num,name] inspector shape broke —
# are gone.

def _floating_faults(data) -> list:
    """Hard fault: a multi-pin electrical part with EVERY pin floating — placed but
    never wired (e.g. an unconnected crystal). Unambiguous bug; fails the build."""
    if not data:
        return []
    return [f"{ref} ({name}) has NONE of its {n} pins connected — it was placed but "
            f"never wired into the circuit."
            for ref, name, n in nq.fully_floating_parts(data)]


def _floating_warnings(data) -> list:
    """Surfaced, not failed: individual open pins on an otherwise-wired part (an
    unused GPIO or NC pin is legitimate; a floating AREF usually is not)."""
    if not data:
        return []
    out = []
    for ref, name, labels in nq.partially_floating_parts(data):
        shown = ", ".join(labels[:8]) + (" …" if len(labels) > 8 else "")
        out.append(f"{ref} ({name}): unconnected pin(s) {shown}")
    return out


# A USB-C receptacle whose CC1/CC2 pins float can't enumerate or negotiate power: a
# sink needs Rd (5.1k to GND) on CC, a source needs Rp. The generic floating-pin check
# (_floating_warnings) treats individual open pins as benign — an unused GPIO/NC pin is
# legitimate — so CC needs its own rule that knows CC is never benign on a USB-C port.
# Default: surface as a WARNING so the rule can be watched against real boards first;
# flip True to make floating CC a hard (build-failing) fault once it's trusted.
FAIL_ON_FLOATING_USB_CC = False


def _is_usb_c_part(name, lib, pins):
    """A USB-C receptacle: named like one, or exposing the CC pins (covers renamed
    symbols and any USB-C-style connector)."""
    blob = f"{name} {lib}".lower()
    if "usb_c" in blob or "usb-c" in blob or "usbc" in blob:
        return True
    pnames = {nm.strip().upper() for (_n, nm) in pins}
    return "CC1" in pnames and "CC2" in pnames


def _usb_cc_faults(data):
    """(faults, warnings): a USB-C receptacle with a floating CC pin. Reuses
    nq.floating_pins, so 'floating' means exactly what it does everywhere else in the
    grader. Loose first cut — it flags CC floating; it does NOT yet verify there is a
    ~5.1k Rd or that CC reaches GND (those are the stricter tiers). Whether a hit is a
    fault or a warning is controlled by FAIL_ON_FLOATING_USB_CC (default: warning)."""
    if not data:
        return [], []
    faults, warns = [], []
    for ref, name, lib, pins in nq.parts(data):
        if not _is_usb_c_part(name, lib, pins):
            continue
        cc_open = sorted({nm.strip().upper() for (_n, nm) in nq.floating_pins(data, ref)
                          if nm.strip().upper() in ("CC", "CC1", "CC2")})
        if not cc_open:
            continue
        msg = (f"{ref} ({name}): USB-C {', '.join(cc_open)} floating — the port can't "
               f"enumerate or negotiate power without Rd (5.1k to GND) on CC.")
        (faults if FAIL_ON_FLOATING_USB_CC else warns).append(msg)
    return faults, warns


def _power_topology_faults(data) -> list:
    """Hard, high-confidence power faults ERC can't catch ([] = sane). Conservative
    on purpose so a PASS means something:
      A) a regulator with input and output on the SAME net (shorted/bypassed),
      B) two voltage domains merged onto one net (e.g. 5V touching the 3V3 rail),
      C) a positive rail shorted to ground on one net.
    """
    if not data:
        return []
    errors = []
    nets = nq.nets(data)

    # Rule B: one net carrying two recognized voltages = merged domains.
    for nm, nodes in nets:
        carriers = {}
        for v in nq.net_voltage_tokens(nm):
            carriers.setdefault(v, f"net name '{nm}'")
        for ref, _num, pname in nodes:
            for v in nq.net_voltage_tokens(pname):
                carriers.setdefault(v, f"{ref}.{pname}")
        if len(carriers) >= 2:
            vs = ", ".join(f"{v:g}V ({carriers[v]})" for v in sorted(carriers))
            errors.append(f"Net '{nm}' merges different voltage domains: {vs}. "
                          f"Each voltage must be its own net.")

    # Rule C: positive rail shorted to ground on the same net.
    for nm, nodes in nets:
        volts = set(nq.net_voltage_tokens(nm))
        for _ref, _num, pname in nodes:
            volts |= nq.net_voltage_tokens(pname)
        names_here = [nm.upper()] + [p.upper() for _r, _n, p in nodes if p]
        has_gnd = any(g in n for n in names_here for g in ("GND", "VSS", "GROUND"))
        if volts and has_gnd:
            errors.append(f"Net '{nm}' shorts a {max(volts):g}V rail to ground "
                          f"(same net carries both a supply and a ground pin).")

    # Rule A: regulator input net == output net.
    for ref, name, in_pin, out_pin in nq.regulators(data):
        in_net, out_net = nq.regulator_io_nets(data, ref, in_pin, out_pin)
        if in_net and out_net and in_net == out_net:
            errors.append(f"Regulator {ref} ({name}): input '{in_pin}' and output '{out_pin}' are on "
                          f"the SAME net '{in_net}' — it's shorted/bypassed, so the input voltage "
                          f"passes straight through to the output.")

    return list(dict.fromkeys(errors))


# --- footprint sanity (netlist-level, no layout/DRC needed) -----------------
# Checks the pin<->pad NUMBER correspondence the netlist already determines, which
# catches a footprint whose pads don't match the symbol — WITHOUT a placed board:
#   fault   = a symbol pin with no landing pad (those pins would be unconnected;
#             unambiguous, zero false positives).
#   warning = the footprint carries numbered pads with no matching pin (usually the
#             WRONG footprint — e.g. an AMS1117, a 3-pin SOT-223, given an 8-pad
#             SOIC-8 — but occasionally legit, so surfaced, not failed).
# GEOMETRIC correctness (pad shape/spacing, clearances) still belongs to KiCad DRC;
# this is the cheap netlist-tier check that needs no layout.

def _footprint_dirs() -> list:
    dirs = [os.environ[v] for v in ("KICAD9_FOOTPRINT_DIR", "KICAD8_FOOTPRINT_DIR",
                                    "KICAD7_FOOTPRINT_DIR", "KICAD6_FOOTPRINT_DIR",
                                    "KICAD_FOOTPRINT_DIR") if os.environ.get(v)]
    dirs.append("/usr/share/kicad/footprints")
    return dirs


def _resolve_footprint(footprint: str):
    """Resolve a 'Lib:Name' footprint against the footprint dirs. Returns (status, path):

      'ok'        the .kicad_mod exists; `path` is its full filename.
      'bad_name'  the Lib.pretty dir IS present but Name.kicad_mod is not in it — the
                  library is installed, the footprint name simply isn't real. A
                  hallucinated/mistyped name; this will NOT import into KiCad (fatal).
      'unknown'   no Lib.pretty dir in any footprint dir — library not installed, or we
                  are looking in the wrong place. Unverifiable -> degrade, never fail.
      'malformed' not 'Lib:Name' shaped.

    `path` is None unless status == 'ok'. This is the resolution half that the pad check
    (_footprint_pad_numbers) and the name check (_footprint_mismatches) both build on."""
    if not footprint or ":" not in footprint:
        return "malformed", None
    lib, name = footprint.split(":", 1)
    if not lib.strip() or not name.strip():
        return "malformed", None
    lib_dir_seen = False
    for base in _footprint_dirs():
        pdir = os.path.join(base, lib + ".pretty")
        if os.path.isdir(pdir):
            lib_dir_seen = True
            path = os.path.join(pdir, name + ".kicad_mod")
            if os.path.exists(path):
                return "ok", path
    return ("bad_name" if lib_dir_seen else "unknown"), None


def _footprint_pad_numbers(footprint: str):
    """Set of non-empty pad NUMBERS in a 'Lib:Name' footprint, or None if the
    .kicad_mod can't be found/parsed — so the PAD check degrades and never fails a
    build it could not actually verify. (Name resolution — a fake-but-plausible name
    that points at no real file — is handled separately by _resolve_footprint /
    _footprint_mismatches; here a non-resolving name simply yields None.)"""
    status, path = _resolve_footprint(footprint)
    if status != "ok":
        return None
    try:
        txt = Path(path).read_text(errors="ignore")
    except Exception:
        return None
    nums = {m.group(1).strip() for m in re.finditer(r'\(pad\s+"([^"]*)"', txt)
            if m.group(1).strip()}
    return nums or None      # zero pads parsed -> unverifiable, skip


def _sortnums(s):
    return sorted(s, key=lambda x: (0, int(x)) if x.isdigit() else (1, x))


# A footprint whose LIBRARY isn't found in any footprint dir can't be checked. By
# default that case degrades silently (on a correctly-configured box it just means the
# library isn't installed). Flip this True to surface it as a warning instead — useful
# if you suspect the coder is inventing library names too, at the cost of some noise.
# A footprint whose library IS present but whose NAME doesn't resolve is ALWAYS a hard
# fault regardless of this flag (unambiguous — it won't import into KiCad).
WARN_ON_UNKNOWN_FOOTPRINT_LIB = False


def _footprint_resolution_faults(data):
    """(faults, warnings) from NAME RESOLUTION alone: does each part's 'Lib:Name'
    footprint point at a real .kicad_mod? A name whose library exists but whose file
    does not is the coder hallucinating a plausible-but-fake footprint — the same
    failure mode that sank atopile, now for footprints. This is independent of, and
    runs alongside, the pad/pin match in _footprint_mismatches (a non-resolving name
    can't also produce a pad fault — _footprint_pad_numbers returns None for it)."""
    if not data:
        return [], []
    faults, warns = [], []
    fps = nq.footprints(data)
    for ref, name, _lib, _pins in nq.parts(data):
        fp = fps.get(ref, "")
        if not fp:
            continue                          # a missing footprint is the netlist-error gate's job
        status, _path = _resolve_footprint(fp)
        if status == "bad_name":
            lib, fpname = fp.split(":", 1)
            faults.append(f"{ref} ({name}): footprint '{fp}' does not exist — library "
                          f"'{lib}' is installed but has no '{fpname}.kicad_mod' "
                          f"(hallucinated or mistyped name; it will NOT import into KiCad).")
        elif status == "malformed":
            faults.append(f"{ref} ({name}): footprint '{fp}' is not in 'Library:Footprint' "
                          f"form — it won't resolve in KiCad.")
        elif status == "unknown" and WARN_ON_UNKNOWN_FOOTPRINT_LIB:
            lib = fp.split(":", 1)[0]
            warns.append(f"{ref} ({name}): footprint library '{lib}' not found in any "
                         f"footprint dir — can't verify '{fp}' (is the library installed?).")
        # 'ok' -> resolves; 'unknown' with the flag off -> degrade silently.
    return faults, warns


def _footprint_mismatches(data):
    """(faults, warnings) from comparing each part's symbol pin numbers against its
    footprint's pad numbers. Only footprints that resolve to a real file are checked
    (_footprint_pad_numbers returns None otherwise); a fake NAME is caught upstream by
    _footprint_resolution_faults. See the section comment above for the fault/warning
    split."""
    if not data:
        return [], []
    faults, warns = [], []
    fps = nq.footprints(data)
    for ref, name, _lib, pins in nq.parts(data):
        fp = fps.get(ref, "")
        if not fp:
            continue                          # a missing footprint is the netlist-error gate's job
        pads = _footprint_pad_numbers(fp)
        if pads is None:
            continue                          # couldn't verify -> skip (degrade, never regress)
        pin_nums = {n for (n, _nm) in pins if n}
        if not pin_nums:
            continue
        missing = pin_nums - pads
        extra = pads - pin_nums
        if missing:
            faults.append(f"{ref} ({name}): footprint '{fp}' has no pad for pin(s) "
                          f"{_sortnums(missing)} — those pins would be unconnected on the board "
                          f"(symbol pins {_sortnums(pin_nums)} vs footprint pads {_sortnums(pads)}).")
        elif extra:
            warns.append(f"{ref} ({name}): footprint '{fp}' has {len(extra)} numbered pad(s) "
                         f"{_sortnums(extra)} with no matching symbol pin — likely the wrong "
                         f"footprint for this part.")
    return faults, warns


# When True, errors SKiDL reports while generating the netlist (most commonly a
# missing footprint on a part) FAIL the build — a netlist you can't lay out isn't
# really "built". Set False to surface them loudly as a warning instead, e.g. while
# footprints are still assigned later in KiCad layout rather than in the SKiDL script.
FAIL_ON_NETLIST_ERRORS = True


def _netlist_errors(raw_text: str):
    """Parse the errors SKiDL reports during netlist generation from its RAW output
    (must be the un-stripped stdout+stderr — _strip_skidl_noise removes these lines).

    SKiDL prints each as 'ERROR: ...' and a summary 'N errors found while generating
    netlist.' WARNINGS (e.g. 'Missing tag ... Random tag generated') are NOT counted —
    those are benign and auto-resolved. Returns (count, sample_error_lines)."""
    text = raw_text or ""
    err_lines = [l.strip() for l in text.splitlines() if l.strip().startswith("ERROR:")]
    m = re.search(r"(\d+)\s+error[s]?\s+found\s+while\s+generating\s+netlist", text)
    count = int(m.group(1)) if m else len(err_lines)
    seen, uniq = set(), []
    for l in err_lines:
        if l not in seen:
            seen.add(l)
            uniq.append(l)
    return count, uniq[:12]


def _decide(returncode: int, net_present: bool, power_data, raw_output: str,
            erc_text: str, visible_log: str) -> "CheckResult":
    """Pure grading decision (no subprocess), so the BUILT/FAILED verdict is testable.

    Layered grader: ERC + netlist must succeed (Layer 1); then the strong checks —
    electrical topology + no fully-unwired part (Layer 2) — plus SKiDL's own netlist
    errors (footprints), which a clean-looking board can still carry. Any of these is
    a hard fault, so BUILT-stop can't lock in a board that's broken or unbuildable."""
    if not (returncode == 0 and net_present):
        return CheckResult(f"CIRCUIT FAILED (exit {returncode})\n{visible_log or '(no output)'}",
                           "failed")

    power_errors = _power_topology_faults(power_data)
    float_errors = _floating_faults(power_data)
    float_warnings = _floating_warnings(power_data)
    fp_faults, fp_warnings = _footprint_mismatches(power_data)
    fp_res_faults, fp_res_warnings = _footprint_resolution_faults(power_data)
    fp_faults = fp_res_faults + fp_faults          # name-doesn't-resolve before pad mismatch
    fp_warnings = fp_res_warnings + fp_warnings
    usb_faults, usb_warnings = _usb_cc_faults(power_data)   # floating CC1/CC2 on a USB-C port
    nl_count, nl_lines = _netlist_errors(raw_output)

    electrical = power_errors + float_errors + usb_faults
    netlist_fatal = nl_lines if (FAIL_ON_NETLIST_ERRORS and nl_count) else []

    if electrical or fp_faults or netlist_fatal:
        msg = ["CIRCUIT FAILED — ERC passed and a netlist was generated, but the DESIGN "
               "CHECK found faults:"]
        if electrical:
            msg.append("Electrical faults (make the board non-functional):")
            msg += [f"  ✗ {e}" for e in electrical]
        if fp_faults:
            msg.append("Footprint faults (won't import into KiCad — unbuildable):")
            msg += [f"  ✗ {e}" for e in fp_faults]
        if netlist_fatal:
            msg.append(f"Netlist errors ({nl_count}) — parts not buildable as-is "
                       "(usually a missing footprint):")
            msg += [f"  ✗ {e}" for e in netlist_fatal]
        msg.append("Fix these, then re-check. Every part must be wired in (no part with all pins "
                   "floating); each voltage is its OWN net; a regulator's input and output are "
                   "DIFFERENT nets; and every part needs a footprint whose pads match its pins "
                   "(Part(..., footprint='Library:Footprint')).")
        return CheckResult("\n".join(msg), "failed")

    parts = ["CIRCUIT BUILT — the script ran, ERC executed, a netlist was generated, and the "
             "design checks (power topology + no fully-unwired parts + footprint pads match pins "
             "+ no netlist errors) passed.",
             "Review the ERC report and fix any ERROR lines (warnings may be acceptable)."]
    if float_warnings:
        parts.append("⚠ Unconnected pins — not fatal, but confirm each is intentional (an "
                     "unused GPIO is fine; a floating AREF or signal pin usually means a "
                     "missing net):\n" + "\n".join(f"  • {w}" for w in float_warnings))
    if usb_warnings:
        parts.append("⚠ USB-C CC pin(s) floating — the port won't enumerate or draw power "
                     "without Rd (5.1k to GND) on CC; confirm this is intended:\n"
                     + "\n".join(f"  • {w}" for w in usb_warnings))
    if fp_warnings:
        parts.append("⚠ Footprint may be wrong — pads don't fully match the symbol; verify the "
                     "part's package:\n" + "\n".join(f"  • {w}" for w in fp_warnings))
    if not FAIL_ON_NETLIST_ERRORS and nl_count:
        parts.append(f"⚠ Netlist reported {nl_count} error(s) (e.g. missing footprints) — "
                     "surfaced, not blocking:\n" + "\n".join(f"  • {e}" for e in nl_lines))
    if erc_text:
        parts.append("ERC report:\n" + erc_text)
    if visible_log:
        parts.append("Log:\n" + visible_log)
    return CheckResult("\n\n".join(parts), "built")


def check_circuit(code: str) -> str:
    """Run a SKiDL script (should call ERC() + generate_netlist()) and report the result."""
    if not Path(SKIDL_PYTHON).exists():
        return CheckResult(
            f"SKiDL Python not found at {SKIDL_PYTHON}. Run setup-skidl.sh, or set "
            f"XORICS_SKIDL_PYTHON to a venv that has skidl installed.", "no_skidl")

    workdir = tempfile.mkdtemp(prefix="xorics_skidl_")
    script = os.path.join(workdir, "circuit.py")
    with open(script, "w") as f:
        f.write(code + _POWER_INSPECTOR)   # epilogue runs after the design, emits topology JSON
    env = dict(os.environ, KICAD_SYMBOL_DIR=KICAD_SYMBOL_DIR)
    # SKiDL looks for version-numbered vars, not the plain one. KiCad keeps the .kicad_sym
    # format stable across versions, so pointing the KiCad9 var (its highest known) at the
    # KiCad 10 symbol dir works. Set 6-9 so it resolves regardless of which SKiDL checks.
    for var in ("KICAD6_SYMBOL_DIR", "KICAD7_SYMBOL_DIR", "KICAD8_SYMBOL_DIR", "KICAD9_SYMBOL_DIR"):
        env[var] = KICAD_SYMBOL_DIR

    try:
        proc = subprocess.run([SKIDL_PYTHON, script], capture_output=True, text=True,
                              timeout=RUN_TIMEOUT, cwd=workdir, env=env)
    except subprocess.TimeoutExpired:
        shutil.rmtree(workdir, ignore_errors=True)
        return CheckResult(f"CIRCUIT TIMEOUT: the SKiDL run exceeded {RUN_TIMEOUT}s.", "timeout")

    # Pull the inspector's topology line out of stdout, then hide all XORICS_POWER_* lines from the
    # coder-facing log (they're machine plumbing, not part of the design's output).
    raw_stdout = proc.stdout or ""
    power_data = None
    for line in raw_stdout.splitlines():
        if line.startswith("XORICS_POWER_JSON:"):
            try:
                power_data = json.loads(line[len("XORICS_POWER_JSON:"):])
            except Exception:
                power_data = None
    visible_stdout = "\n".join(l for l in raw_stdout.splitlines()
                               if not l.startswith("XORICS_POWER_"))

    # Keep the raw (un-stripped) output for netlist-error parsing; strip noise for display.
    raw_combined = raw_stdout + (proc.stderr or "")
    out = _strip_skidl_noise(visible_stdout + (proc.stderr or ""))
    nets = [f for f in os.listdir(workdir) if f.endswith(".net")]
    erc_files = [f for f in os.listdir(workdir) if f.endswith(".erc")]
    erc_text = ""
    if erc_files:
        try:
            erc_text = (Path(workdir) / erc_files[0]).read_text().strip()
        except Exception:
            pass
    shutil.rmtree(workdir, ignore_errors=True)

    if len(out) > MAX_OUTPUT:
        out = "...(truncated)...\n" + out[-MAX_OUTPUT:]

    return _decide(proc.returncode, bool(nets), power_data, raw_combined, erc_text, out)


def _find_part_helper_src() -> str:
    """Subprocess (runs in the skidl venv): search, then VALIDATE each candidate by actually
    instantiating it. Emits one XORICS_JSON line so the parent can read structured results."""
    return r'''
import sys, io, json, re, contextlib
from skidl import search, Part

query = sys.argv[1]
buf = io.StringIO()
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
    try:
        search(query)
    except Exception:
        pass
raw = buf.getvalue()

# Parse loose (lib, name) candidates from skidl's "Library: PartName ..." search output.
# Loose parsing is fine because every candidate is validated below — junk just fails to load.
candidates, seen = [], []
for line in raw.splitlines():
    m = re.match(r"\s*([A-Za-z0-9_.\-]+):\s*([A-Za-z0-9_.\-]+)", line)
    if not m:
        continue
    lib = m.group(1).rsplit(".kicad_sym", 1)[0]
    name = m.group(2)
    if (lib, name) not in seen:
        seen.append((lib, name))
        candidates.append((lib, name))

verified, failed, pins, meta = [], [], {}, {}
for lib, name in candidates[:25]:
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            part = Part(lib, name)   # throwaway subprocess: no need for dest=TEMPLATE
        verified.append([lib, name])
        # We already have the part object — grab its pins for free so the coder gets real
        # pin NAMES (not datasheet guesses). [num, name] pairs; names can repeat (multi-GND).
        plist = []
        try:
            for pin in getattr(part, "pins", []) or []:
                plist.append([str(getattr(pin, "num", "") or ""),
                              str(getattr(pin, "name", "") or "")])
        except Exception:
            pass
        pins[lib + ":" + name] = plist
        # Category metadata, straight off the symbol: ref_prefix IS KiCad's category (R/C/Y/D/U/J),
        # keywords/description carry the semantics. Lets us filter by category, not guess by name.
        meta[lib + ":" + name] = {
            "prefix": str(getattr(part, "ref_prefix", "") or ""),
            "kw": str(getattr(part, "keywords", "") or ""),
            "desc": str(getattr(part, "description", "") or ""),
        }
    except Exception:
        failed.append([lib, name])

print("XORICS_JSON:" + json.dumps({
    "verified": verified, "failed": failed, "pins": pins, "meta": meta, "n": len(candidates),
    "raw_tail": "\n".join(raw.splitlines()[-25:]),
}))
'''


def _pins_helper_src() -> str:
    """Subprocess (runs in the skidl venv): load ONE symbol and dump its pins as XORICS_JSON."""
    return r'''
import sys, io, json, contextlib
from skidl import Part

lib, name = sys.argv[1], sys.argv[2]
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        part = Part(lib, name)
    plist = []
    for pin in getattr(part, "pins", []) or []:
        plist.append([str(getattr(pin, "num", "") or ""),
                      str(getattr(pin, "name", "") or "")])
    print("XORICS_JSON:" + json.dumps({"ok": True, "pins": plist}))
except Exception as e:
    print("XORICS_JSON:" + json.dumps({"ok": False, "error": str(e)}))
'''


def _format_pins(lib: str, name: str, pin_pairs: list) -> str:
    """Render a part's pins grouped by NAME (with their numbers), so the coder connects to a name
    that exists — part['VI'] — instead of guessing a datasheet name like 'VIN'. Pins with no name
    (or '~') are listed by number, since those must be connected as part[<num>]."""
    groups, order, unnamed = {}, [], []
    for num, pname in pin_pairs:
        pn = (pname or "").strip()
        if not pn or pn == "~":
            unnamed.append(str(num))
            continue
        if pn not in groups:
            groups[pn] = []
            order.append(pn)
        groups[pn].append(str(num))
    head = (f"Pins for Part('{lib}', '{name}') — connect by NAME exactly as shown (e.g. part['{order[0]}']):"
            if order else f"Pins for Part('{lib}', '{name}'):")
    lines = [head]
    for pn in order:
        nums = groups[pn]
        tag = f"(pin {nums[0]})" if len(nums) == 1 else f"(pins {', '.join(nums)})"
        lines.append(f"  {pn:<12} {tag}")
    if unnamed:
        lines.append("  unnamed (connect by number): " + ", ".join(f"part[{n}]" for n in unnamed))
    return "\n".join(lines)


def _tokenize(name: str) -> list[str]:
    """Lowercase tokens, splitting on separators AND camelCase humps so 'ESP32-C3-DevKitM-1'
    -> ['esp32','c3','dev','kit','m','1'] while keeping 'esp32'/'usb2' intact (no letter-digit
    split). This lets a query token like 'mini' match 'WEMOS_C3_mini' on equal footing."""
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    return [p.lower() for p in re.split(r"[^A-Za-z0-9]+", name) if p]


def _rank_candidates(query: str, items: list, corpus_names: list) -> list:
    """Rank (lib, name) items by name-similarity to the query: IDF-weighted token overlap (bare
    numerics down-weighted) with SequenceMatcher as a tiebreak. IDF is built from corpus_names, so a
    token that's ubiquitous across the candidate set carries less signal and the distinctive noun
    (e.g. 'crystal') dominates. Works on a validated list OR a raw search union (pre-validation)."""
    N = len(corpus_names) or 1
    df: dict[str, int] = {}
    for nm in corpus_names:
        for t in set(_tokenize(nm)):
            df[t] = df.get(t, 0) + 1

    def idf(t: str) -> float:
        base = math.log((N + 1) / (df.get(t, 0) + 1)) + 1.0
        return base * (0.25 if t.isdigit() else 1.0)

    q_tokens = set(_tokenize(query))
    q_norm = "".join(_tokenize(query))

    def sort_key(item):
        _, name = item
        overlap = sum(idf(t) for t in (q_tokens & set(_tokenize(name))))
        seq = SequenceMatcher(None, q_norm, "".join(_tokenize(name))).ratio()
        return (overlap, seq, -len(name), name)

    return sorted(items, key=sort_key, reverse=True)


def _rank_verified(query: str, verified: list, failed: list) -> list:
    """Rank the loadable parts so the closest to the query is first (the coder takes verified[0])."""
    return _rank_candidates(query, verified, [n for _, n in verified] + [n for _, n in failed])


def _render_find_part(query: str, data: dict) -> str:
    """Turn the helper's JSON into the coder-facing text: ranked verified parts, ignored
    near-misses, and — for the TOP pick — its real pin names inline (the common path needs no
    second call). Pure function of `data`, so it's unit-testable without skidl."""
    verified = data.get("verified", [])
    failed = data.get("failed", [])
    pins_map = data.get("pins", {}) or {}

    if verified:
        if not data.get("_ordered"):
            verified = _rank_verified(query, verified, failed)  # closest loadable part first
        lines = [f"Part search for '{query}' — VERIFIED loadable (use EXACTLY as written):"]
        lines += [f"  Part('{lib}', '{name}')" for lib, name in verified]
        if failed:
            nm = ", ".join(f"{l}:{n}" for l, n in failed[:8])
            lines.append(f"(Ignored {len(failed)} name-match(es) that do NOT load: {nm})")
        # Inline the top pick's pins so the coder connects to real names, not datasheet guesses.
        top_lib, top_name = verified[0]
        top_pins = pins_map.get(f"{top_lib}:{top_name}")
        if top_pins:
            lines.append("")
            lines.append(_format_pins(top_lib, top_name, top_pins))
            if len(verified) > 1:
                lines.append("(Pins above are for the TOP match. For another listed part, "
                             "call part_pins(library, name).)")
        return "\n".join(lines)

    if failed:
        nm = ", ".join(f"{l}:{n}" for l, n in failed[:12])
        return (f"No LOADABLE part matched '{query}'. These names matched text but will NOT "
                f"instantiate — do not use them: {nm}.\nTry a related part/keyword you know exists.")

    return (f"No parts found matching '{query}' ({data.get('n', 0)} parseable candidates). "
            f"Recent search output:\n{(data.get('raw_tail') or '')[:1200]}\n"
            f"Try a broader term (e.g. 'ESP32-C3', 'AMS1117', 'USB_C', 'regulator linear').")


def _kicad_env() -> dict:
    """Environment with the KiCad symbol dir exported under every version-numbered var SKiDL checks."""
    env = dict(os.environ, KICAD_SYMBOL_DIR=KICAD_SYMBOL_DIR)
    for var in ("KICAD6_SYMBOL_DIR", "KICAD7_SYMBOL_DIR", "KICAD8_SYMBOL_DIR", "KICAD9_SYMBOL_DIR"):
        env[var] = KICAD_SYMBOL_DIR
    return env


def _run_helper_src(src: str, args: list, env: dict, timeout: int = 240):
    """Run a helper script in the skidl venv and parse its single XORICS_JSON line.
    Returns the parsed dict, or None if it timed out / didn't report."""
    try:
        proc = subprocess.run([SKIDL_PYTHON, "-c", src, *args],
                              capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return None
    for line in (proc.stdout or "").splitlines():
        if line.startswith("XORICS_JSON:"):
            try:
                return json.loads(line[len("XORICS_JSON:"):])
            except Exception:
                return None
    return None


def _validate_helper_src() -> str:
    """Subprocess: validate a JSON list of [lib, name] by instantiating each, return pins + category
    metadata (ref_prefix / keywords / description) too. Used for canonical staples and the broad-path
    top slice."""
    return r'''
import sys, io, json, contextlib
from skidl import Part

pairs = json.loads(sys.argv[1])
verified, failed, pins, meta = [], [], {}, {}
for lib, name in pairs:
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            part = Part(lib, name)
        verified.append([lib, name])
        plist = []
        try:
            for pin in getattr(part, "pins", []) or []:
                plist.append([str(getattr(pin, "num", "") or ""),
                              str(getattr(pin, "name", "") or "")])
        except Exception:
            pass
        pins[lib + ":" + name] = plist
        meta[lib + ":" + name] = {
            "prefix": str(getattr(part, "ref_prefix", "") or ""),
            "kw": str(getattr(part, "keywords", "") or ""),
            "desc": str(getattr(part, "description", "") or ""),
        }
    except Exception:
        failed.append([lib, name])
print("XORICS_JSON:" + json.dumps({"verified": verified, "failed": failed, "pins": pins,
                                   "meta": meta, "n": len(pairs)}))
'''


def _broad_helper_src() -> str:
    """Subprocess: split the query into tokens, run search() on EACH, and return the UNION of
    (lib, name) candidates — unvalidated. The parent ranks this union against the full query and
    validates only the top slice, so we never have to guess which words are 'the part' vs noise."""
    return r'''
import sys, io, json, re, contextlib
from skidl import search

query = sys.argv[1]
tokens = [t for t in re.split(r"[^A-Za-z0-9]+", query) if len(t) >= 2]
if not tokens:
    tokens = [query]

seen, candidates = set(), []
for tok in tokens:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        try:
            search(tok)
        except Exception:
            pass
    for line in buf.getvalue().splitlines():
        m = re.match(r"\s*([A-Za-z0-9_.\-]+):\s*([A-Za-z0-9_.\-]+)", line)
        if not m:
            continue
        lib = m.group(1).rsplit(".kicad_sym", 1)[0]
        name = m.group(2)
        if (lib, name) not in seen:
            seen.add((lib, name))
            candidates.append([lib, name])

print("XORICS_JSON:" + json.dumps({"candidates": candidates}))
'''


# ---- Category resolver -------------------------------------------------------------------------
# XORICS-FEATURE: category-resolver-v2 (tiered ref_prefix vs term membership)
# KiCad symbol libraries are NOT parametric: you pick the right *symbol family* by category, then set
# value/package on the instance. The coder, though, searches by spec ('Resistor', 'low dropout
# regulator') and text search happily returns wrong-family junk first (R_Network08 array for
# 'Resistor', diodes for a regulator search). So we read the category straight off each validated
# Part — ref_prefix (R/C/L/Y/D/U/J = KiCad's own category), plus keywords/description — and use it to
# (a) inject the canonical symbol for staples and (b) filter+order within the right family.
#
# Each category: query words that trigger it; the ref_prefix(es) that ARE this category; a canonical
# symbol to guarantee for simple staples (None where there's no single right answer, e.g. regulators);
# and free-text terms that confirm membership via keywords/description.
_CATEGORIES = [
    {"names": ("resistor",),                  "prefixes": ("R",),  "canonical": ("Device", "R"),           "terms": ("resistor",)},
    {"names": ("capacitor", "cap"),           "prefixes": ("C",),  "canonical": ("Device", "C"),           "terms": ("capacitor",)},
    {"names": ("inductor", "choke"),          "prefixes": ("L",),  "canonical": ("Device", "L"),           "terms": ("inductor",)},
    {"names": ("ferrite",),                    "prefixes": ("L", "FB"), "canonical": ("Device", "FerriteBead"), "terms": ("ferrite",)},
    {"names": ("crystal", "xtal"),            "prefixes": ("Y",),  "canonical": ("Device", "Crystal"),     "terms": ("crystal", "quartz")},
    {"names": ("led",),                        "prefixes": ("D",),  "canonical": ("Device", "LED"),         "terms": ("led",)},
    {"names": ("diode",),                      "prefixes": ("D",),  "canonical": ("Device", "D"),           "terms": ("diode",)},
    {"names": ("button", "pushbutton", "tactile", "switch"), "prefixes": ("SW",), "canonical": ("Switch", "SW_Push"), "terms": ("switch", "button")},
    {"names": ("regulator", "ldo", "vreg"),   "prefixes": ("U",),  "canonical": None,                      "terms": ("regulator", "ldo", "low dropout")},
    {"names": ("header", "connector", "conn"), "prefixes": ("J",),  "canonical": None,                      "terms": ("connector", "header")},
]


def _query_category(query: str):
    """Return the category whose trigger word appears in the query (word-boundary match), else None.
    A specific part number ('AMS1117-3.3') matches nothing here, so it bypasses category logic."""
    q = query.lower()
    for cat in _CATEGORIES:
        if any(re.search(r"\b" + re.escape(n) + r"\b", q) for n in cat["names"]):
            return cat
    return None


def _connector_geometry(query: str):
    """Map an explicit header geometry in the query to the canonical KiCad generic-connector
    symbol: '2x16' / '2 x 16' / '02x16' -> Connector_Generic:Conn_02x16_Odd_Even; '1x8' ->
    Conn_01x08. KiCad Conn_* names are zero-padded; double-row uses the _Odd_Even pin-numbering
    variant, single-row has no suffix. Returns ('Connector_Generic', '<symbol>') or None.
    Geometry must be EXPLICIT -- a bare 'header' stays ambiguous and is left to normal ordering.
    XORICS-FEATURE: connector-geometry
    """
    q = query.lower()
    m = re.search(r"\b(\d{1,2})\s*x\s*(\d{1,3})\b", q)
    if not m:
        # bare pin-count form: '2 pin', '2-pin', '10 position', '2 way' -> single-row 1xN header.
        # A bare count is single-row by convention; two-row needs the explicit NxM form above.
        # XORICS-FEATURE: connector-geometry-count
        c = re.search(r"\b(\d{1,3})\s*[- ]?\s*(?:pin|pins|pos|position|positions|way|ways|contact|contacts)\b", q)
        if c and int(c.group(1)) >= 1:
            return ("Connector_Generic", f"Conn_01x{int(c.group(1)):02d}")
        return None
    rows, per = int(m.group(1)), int(m.group(2))
    if rows < 1 or per < 1:
        return None
    if rows == 1:
        return ("Connector_Generic", f"Conn_01x{per:02d}")
    if rows == 2:
        return ("Connector_Generic", f"Conn_02x{per:02d}_Odd_Even")
    return ("Connector_Generic", f"Conn_{rows:02d}x{per:02d}")


def _is_bare_connector(query: str) -> bool:
    """True only for a GENERIC header/connector request with no geometry and no specific type
    (e.g. 'header', 'pin header', 'connector') -- not 'USB connector' or 'JST'. KiCad's
    J-prefix space is undiscriminated (2-pin through 40-pin Pi-hats, JTAG, ...), so for the bare
    case name-ranking surfaces specialty headers (JTAG) first; we ask for the geometry instead.
    XORICS-FEATURE: connector-bare-guard"""
    filler = {"header", "headers", "connector", "connectors", "conn", "pin", "pins",
              "male", "female", "generic", "breakout", "a", "an", "the",
              "single", "dual", "double", "row", "rows", "way", "ways"}
    leftover = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if t and t not in filler]
    return not leftover


def _connector_geometry_prompt(query: str) -> str:
    """Bare 'header'/'connector' query: return the canonical generic forms + how to re-query by
    size, instead of a mis-ranked part list that puts a JTAG header first.
    XORICS-FEATURE: connector-bare-guard"""
    return (
        f"'{query}' is ambiguous -- KiCad generic headers are picked by GEOMETRY, not by name "
        f"(a bare header search ranks specialty headers like JTAG first, which is wrong). "
        f"Re-query with the size:\n"
        f"  find_part('Header 2x14')  -> Connector_Generic:Conn_02x14_Odd_Even  (dual-row, all I/O)\n"
        f"  find_part('Header 1x8')   -> Connector_Generic:Conn_01x08           (single-row)\n"
        f"  find_part('Header 2 Pin') -> Connector_Generic:Conn_01x02           (power)\n"
        f"  find_part('Header 2x3')   -> Connector_Generic:Conn_02x03_Odd_Even  (ISP)\n"
        f"Generic Conn_* pins are NUMERIC: header[1], header[2], ... (no pin names)."
    )


def _order_by_category(query: str, cat: dict, data: dict) -> list:
    """Filter verified parts to the target category and order them. Membership is tiered: a true
    ref_prefix match (KiCad's own category designator — R/C/Y/U/J) is strong; the category word merely
    appearing in keywords/description is weak. We keep the strong matches and fall back to weak ones
    only if no strong match exists — that's what keeps bias-resistor transistors and heaters out of a
    'Resistor' search. Order within the kept set: canonical first, then ref_prefix members, then
    non-arrays, then fewest pins, then the name-similarity ranker as the final tiebreak for the last
    few, then shortest name."""
    verified = data.get("verified", [])
    meta = data.get("meta", {}) or {}
    pins = data.get("pins", {}) or {}
    canon = cat.get("canonical")
    prefixes, terms = cat.get("prefixes", ()), cat.get("terms", ())

    def info(item):
        lib, name = item
        m = meta.get(lib + ":" + name, {})
        prefix = m.get("prefix") or ""
        text = ((m.get("kw") or "") + " " + (m.get("desc") or "") + " " + name).lower()
        in_prefix = prefix in prefixes                    # strong: KiCad's own category designator
        in_term = any(t in text for t in terms)           # weak: the word appears somewhere in text
        is_array = any(w in text for w in ("network", "array", "pack"))
        pin_count = len(pins.get(lib + ":" + name, []) or [])
        is_canon = canon is not None and [lib, name] == list(canon)
        return in_prefix, in_term, is_array, pin_count, is_canon

    # Prefer true ref_prefix members. Only if NONE exist do we fall back to weak term matches (the
    # word appearing in keywords/description), and only if neither exists do we keep everything. This
    # is what stops bias-resistor transistors / heaters (prefix Q, "resistor" in their text) from
    # showing up in a 'Resistor' search.
    prefix_hits = [it for it in verified if info(it)[0]]
    term_hits = [it for it in verified if info(it)[1]]
    kept = prefix_hits or term_hits or list(verified)

    # Name-similarity order over the kept set; used only as a tail tiebreak below.
    ranked = _rank_candidates(query, kept, [n for _, n in kept])
    rankpos = {(l, n): i for i, (l, n) in enumerate(ranked)}

    def sort_key(it):
        in_prefix, in_term, is_array, pin_count, is_canon = info(it)
        return (0 if is_canon else 1,            # canonical staple wins outright
                0 if in_prefix else 1,            # true ref_prefix member beats term-only matches
                0 if not is_array else 1,         # arrays/networks to the back
                pin_count,                        # prefer the simplest member
                rankpos.get((it[0], it[1]), 1 << 30),  # ranker breaks remaining ties
                len(it[1]), it[1])

    return sorted(kept, key=sort_key)


def _merge_data(data: dict, vd: dict) -> dict:
    """Fold a validate-helper result into data: append any new verified parts, merge pins+meta."""
    out = dict(data)
    out["verified"] = list(out.get("verified", []))
    have = {tuple(p) for p in out["verified"]}
    for p in vd.get("verified", []) or []:
        if tuple(p) not in have:
            out["verified"].append(p)
            have.add(tuple(p))
    out["pins"] = {**(out.get("pins", {}) or {}), **(vd.get("pins", {}) or {})}
    out["meta"] = {**(out.get("meta", {}) or {}), **(vd.get("meta", {}) or {})}
    return out


def _finalize(query: str, data: dict, env: dict) -> str:
    """Single choke point for category awareness before rendering. If the query names a category:
    guarantee that category's canonical symbol is present (validate+inject if text search missed it),
    then order verified by category rules. Every find_part path funnels through here, so the happy
    path and the recovery paths get identical treatment."""
    if not (data and data.get("verified")):
        return _render_find_part(query, data if data is not None else {})
    cat = _query_category(query)
    if not cat:
        return _render_find_part(query, data)        # specific part number — leave name-ranking alone
    if "J" in cat.get("prefixes", ()):               # connector: honor an explicit NxM geometry
        geo = _connector_geometry(query)
        if geo:
            cat = {**cat, "canonical": geo}           # pin the generic header symbol to the top
        elif _is_bare_connector(query):              # bare 'header'/'connector': no size, no type --
            return _connector_geometry_prompt(query)  # ask for geometry, do not rank JTAG to the top
    canon = cat.get("canonical")
    if canon:
        have = {l + ":" + n for l, n in data["verified"]}
        if (canon[0] + ":" + canon[1]) not in have:
            vd = _run_helper_src(_validate_helper_src(), [json.dumps([list(canon)])], env)
            if vd and vd.get("verified"):
                data = _merge_data(data, vd)
    data = dict(data)
    data["verified"] = _order_by_category(query, cat, data)
    data["_ordered"] = True                          # _render_find_part: don't re-rank by name
    return _render_find_part(query, data)


def find_part(query: str) -> str:
    """Search the KiCad symbol libraries AND verify each hit actually instantiates.

    Returns only loadable parts as exact Part('lib','name') calls, with the top pick's pins inline.
    Category-aware: KiCad libs aren't parametric, so the right symbol is chosen by *category* (read
    off each Part's ref_prefix/keywords/description), not by name-matching the spec. Staples
    (resistor/cap/crystal/LED/...) resolve to their canonical symbol; families (regulator, connector)
    are filtered to the right ref_prefix so wrong-family hits drop out. Value/package go on the
    instance (value=/footprint=), never in the search.
    """
    if not Path(SKIDL_PYTHON).exists():
        return f"SKiDL Python not found at {SKIDL_PYTHON}. Run setup-skidl.sh."
    env = _kicad_env()

    # 1) Literal search + validate, then category-finalize.
    data = _run_helper_src(_find_part_helper_src(), [query], env)
    if data and data.get("verified"):
        return _finalize(query, data, env)

    # 2) Broad recovery: search EACH token, union the candidates, rank the union against the FULL
    #    query, validate only the top slice (validation = instantiation, the expensive step). No
    #    guessing which words are 'the part' — ranking decides — then category-finalize.
    union = _run_helper_src(_broad_helper_src(), [query], env)
    cands = [tuple(c) for c in (union.get("candidates", []) if union else [])]
    if cands:
        ranked = _rank_candidates(query, cands, [n for _, n in cands])
        top = [list(c) for c in ranked[:25]]
        vdata = _run_helper_src(_validate_helper_src(), [json.dumps(top)], env)
        if vdata and vdata.get("verified"):
            note = (f"No symbol literally named '{query}'; searched each term, ranked the matches, "
                    f"and verified these load (set value/package with value=/footprint=):\n")
            return note + _finalize(query, vdata, env)

    # 3) Category fallback: query names a staple but nothing loadable was found — offer the canonical
    #    symbol directly (e.g. 'a 16 MHz crystal' when no symbol is literally named that).
    cat = _query_category(query)
    if cat and cat.get("canonical"):
        vd = _run_helper_src(_validate_helper_src(), [json.dumps([list(cat["canonical"])])], env)
        if vd and vd.get("verified"):
            note = (f"No symbol literally named like '{query}', but that's a standard part — use this "
                    f"(set the value/package with value=/footprint=, not in the name):\n")
            return note + _finalize(query, vd, env)

    # 4) Nothing recovered — return the original message (or a degraded note).
    if data is not None:
        return _render_find_part(query, data)
    return (f"Part search for '{query}' could not be validated (helper didn't report; the first "
            f"search also builds a cache — try once more). Treat any names as UNVERIFIED.")


def part_pins(library: str, name: str) -> str:
    """Report the actual pin names + numbers of a verified KiCad symbol, so a SKiDL script connects
    to pins that exist. KiCad symbol pin names differ from datasheet names (the AMS1117 input is
    'VI', not 'VIN'; an ESP32-C3 pin may be 'IO0', not 'GPIO0'), which is why guessing from a
    datasheet fails check_circuit with 'No pins found'. Pass library/name exactly as find_part gave.
    """
    if not Path(SKIDL_PYTHON).exists():
        return f"SKiDL Python not found at {SKIDL_PYTHON}. Run setup-skidl.sh."
    data = _run_helper_src(_pins_helper_src(), [library, name], _kicad_env(), timeout=180)
    if not data or not data.get("ok"):
        err = (data or {}).get("error") if data else None
        return (f"Could not load Part('{library}', '{name}') to read pins: {err or '(no report; the '
                f'first lookup also builds a cache — try once more)'}. Verify it with find_part first.")
    pin_pairs = data.get("pins", [])
    if not pin_pairs:
        return f"Part('{library}', '{name}') loaded but reported no pins."
    return _format_pins(library, name, pin_pairs)


def check_circuit_file(path: str) -> str:
    """Validate a SKiDL script that is already SAVED on disk, by path: read the file and run
    check_circuit on its contents. Lets the coder repair an existing circuits/<name>/<name>.py
    without the whole script being pasted back in. Returns whatever check_circuit returns (the
    status-carrying result), so the agent loop's BUILT detection still works.
    XORICS-FEATURE: check-circuit-file
    """
    p = Path(path).expanduser()
    if not p.exists():
        return (f"No circuit file at {path}. Pass the full path to a saved script, e.g. "
                f"~/xorics-ai/circuits/<name>/<name>.py (list them with: ls circuits/*/*.py).")
    try:
        code = p.read_text()
    except Exception as e:
        return f"Could not read {path}: {e}"
    return check_circuit(code)


def save_circuit(code: str, name: str = "circuit") -> str:
    """Save a SKiDL script as circuits/<slug>/<slug>.py and return the path."""
    slug = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")[:40] or "circuit"
    d = CIRCUIT_DIR / slug
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slug}.py"
    path.write_text(code)
    return str(path)


def _all_footprints():
    """[(lib, name), ...] for every installed footprint (<dir>/<lib>.pretty/<name>.kicad_mod).
    Names only — no file reads — so this stays cheap across thousands of footprints. First
    footprint dir wins on a duplicate lib name (matches _resolve_footprint's search order)."""
    seen, out = set(), []
    for base in _footprint_dirs():
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for d in entries:
            if not d.endswith(".pretty"):
                continue
            lib = d[:-len(".pretty")]
            if lib in seen:
                continue
            seen.add(lib)
            try:
                files = os.listdir(os.path.join(base, d))
            except OSError:
                continue
            out += [(lib, fn[:-len(".kicad_mod")]) for fn in files if fn.endswith(".kicad_mod")]
    return out


def find_footprint(query: str, pins: int = 0) -> str:
    """Search the installed KiCad footprint libraries for REAL, loadable footprints matching
    `query`, returned as exact 'Library:Footprint' strings — what goes straight into
    Part(..., footprint='Library:Footprint'). The footprint twin of find_part: look the name
    up here instead of guessing it (a guessed name that doesn't exist fails the build). Pure
    filesystem lookup, no SKiDL needed.

    If `pins` > 0, footprints whose pad COUNT equals it are listed first — the footprint's pads
    must cover the part's pins, so this points right at the usable choices."""
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return ("find_footprint needs a search term, e.g. find_footprint('SOT-223') or "
                "find_footprint('0603').")
    cands = _all_footprints()
    if not cands:
        return ("No footprint libraries found (looked in: " + ", ".join(_footprint_dirs())
                + "). Is KiCad's footprint set installed?")
    q_norm = "".join(_tokenize(query))

    pool = []
    for lib, name in cands:
        toks = set(_tokenize(f"{lib} {name}"))
        if (q_tokens & toks) or (q_norm in "".join(_tokenize(f"{lib} {name}"))):
            pool.append((f"{lib}:{name}", f"{lib} {name}"))
    if not pool:
        return (f"No footprint matched '{query}'. Try a footprint-family term like 'SOT-223', "
                f"'0603', 'SOIC-8', 'USB_C', or 'Conn_01x04'.")

    ranked = _rank_candidates(query, pool, [c for _id, c in pool])
    top = [fid for fid, _c in ranked[:12]]
    padcount = {fid: (len(_footprint_pad_numbers(fid) or ()) or None) for fid in top}

    def fmt(fid):
        n = padcount.get(fid)
        return f"  {fid}" + (f"   ({n} pads)" if n else "")

    if pins > 0:
        match = [f for f in top if padcount.get(f) == pins]
        rest = [f for f in top if f not in match]
        lines = [f"Footprints for '{query}' (need {pins} pads — pads must cover the part's pins):"]
        lines += ([fmt(f) for f in match] if match
                  else [f"  (none with exactly {pins} pads among the top matches — nearest below)"])
        if rest:
            lines.append("Other close matches:")
            lines += [fmt(f) for f in rest[:6]]
    else:
        lines = [f"Footprints for '{query}' (use EXACTLY as written in footprint='...'):"]
        lines += [fmt(f) for f in top]
    lines.append("Then set it on the part: Part('<lib>','<name>', footprint='<Library:Footprint>').")
    return "\n".join(lines)
