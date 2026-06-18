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

"""
Layer-1 netlist query primitives for the Xorics strong grader.

This is the single, heavily-tested join layer that every electrical invariant
(Layer-2) sits on top of — the "step definitions / World" in BDD terms. The
brittle pin-matching logic lives HERE, written ONCE, so the name-only-join bug
that silently passed a floating crystal (grader gap #6) cannot recur in two
places (it lived in both _check_unconnected and _check_power_topology before).

INPUT CONTRACT (`power_data`, emitted by the inspector appended in check_circuit):

    {
      "nets":  [ [net_name, [ [ref, num, name], ... ]], ... ],
      "parts": [ [ref, name, lib, [ [num, name], ... ] ], ... ],
    }

  - net nodes are [ref, num, name]   (already carry BOTH num and name)
  - part pins are [num, name]        (NEW: previously name-only — see inspector patch)

For rollout safety this layer ALSO accepts the OLD part-pin shape (a bare pin-name
string) and normalizes it to (num="", name) — degrade, never crash. So a stale
inspector during a partial rollout cannot make this module throw; it just loses
the num fallback for that run.

PIN IDENTITY — two needs, two helpers (this is the crux of the #6 fix):
  * pin_key(num, name)   -> MATCHING key.  Prefers NUM (unique within a symbol
                            unit), falls back to name. A blank NAME no longer
                            erases the pin, because num carries the identity.
                            Preferring num also avoids the same-name collapse
                            (e.g. two pins both named "GND") that name-keying hits.
  * pin_label(num, name) -> HUMAN label.   Prefers NAME, falls back to num.

A pin is CONNECTED iff it shares a net (a net with >=2 nodes) with at least one
OTHER pin. A pin alone on its own auto-named net, or on no net, is FLOATING —
even if generate_netlist auto-named it.

This module is PURE: every function is a transform over the dict above. It has no
SKiDL/KiCad dependency, so it is unit-testable against fixtures. IMPORTANT: a
fixture only proves the LOGIC given an assumed input shape; whether the real
inspector emits that shape (notably: blank pin NAMES on passives, which is the
whole reason num-fallback exists) must be confirmed against a REAL SKiDL run, not
assumed. See capture_real_powerdata.py. Do not let green fixtures stand in for
that one real capture — that conflation is exactly what hid #6.
"""

import re

# ---------------------------------------------------------------------------
# normalization + pin identity
# ---------------------------------------------------------------------------

def _s(x):
    """None-safe, stripped str."""
    return ("" if x is None else str(x)).strip()


def pin_key(num, name):
    """Matching identity: num preferred (unique per symbol unit), name fallback.

    Returns "" only when BOTH are blank; such a pin cannot be matched to a net
    node, so it is treated conservatively as floating (fail-loud) rather than
    silently assumed-connected.
    """
    return _s(num) or _s(name)


def pin_label(num, name):
    """Human-readable pin label: name preferred, num fallback."""
    return _s(name) or _s(num)


def _norm_pin(p):
    """Normalize a part pin to (num, name).

    Accepts the NEW shape [num, name] (or a longer list — first two used) and the
    OLD shape: a bare pin-name string -> ("", name).
    """
    if isinstance(p, (list, tuple)):
        if len(p) >= 2:
            return _s(p[0]), _s(p[1])
        if len(p) == 1:
            return "", _s(p[0])
        return "", ""
    return "", _s(p)


# ---------------------------------------------------------------------------
# accessors (tolerant of malformed records — never raise on shape)
# ---------------------------------------------------------------------------

def parts(data):
    """List of (ref, name, lib, [(num, name), ...]) for every part."""
    out = []
    for rec in (data or {}).get("parts", []) or []:
        rec = rec or []
        ref = _s(rec[0]) if len(rec) > 0 else "?"
        name = _s(rec[1]) if len(rec) > 1 else ""
        lib = _s(rec[2]) if len(rec) > 2 else ""
        pins = [_norm_pin(p) for p in (rec[3] if len(rec) > 3 and rec[3] else [])]
        out.append((ref, name, lib, pins))
    return out


def nets(data):
    """List of (net_name, [(ref, num, name), ...]) for every net."""
    out = []
    for rec in (data or {}).get("nets", []) or []:
        rec = rec or []
        nm = _s(rec[0]) if len(rec) > 0 else ""
        nodes = []
        for nd in (rec[1] if len(rec) > 1 and rec[1] else []):
            nd = nd or []
            ref = _s(nd[0]) if len(nd) > 0 else "?"
            num = _s(nd[1]) if len(nd) > 1 else ""
            name = _s(nd[2]) if len(nd) > 2 else ""
            nodes.append((ref, num, name))
        out.append((nm, nodes))
    return out


def part_refs(data):
    return [ref for ref, _n, _l, _p in parts(data)]


def pins_of(data, ref):
    """[(num, name), ...] for the given part ref (empty if not found)."""
    for r, _n, _l, pins in parts(data):
        if r == ref:
            return pins
    return []


def pin_count(data, ref):
    return len(pins_of(data, ref))


# ---------------------------------------------------------------------------
# connectivity — the single join
# ---------------------------------------------------------------------------

def _connected_keys(data):
    """Set of (ref, pin_key) for every pin that shares a >=2-node net with another.

    THE join. Built once, num-keyed. A 1-node net connects its pin to nothing, so
    it does not contribute. Used by every connectivity predicate below.
    """
    keys = set()
    for _nm, nodes in nets(data):
        if len(nodes) < 2:
            continue
        for ref, num, name in nodes:
            keys.add((ref, pin_key(num, name)))
    return keys


def is_connected(data, ref, num="", name=""):
    """Does this specific pin share a net with at least one other pin?"""
    return (ref, pin_key(num, name)) in _connected_keys(data)


def floating_pins(data, ref):
    """[(num, name), ...] pins of `ref` that are connected to nothing."""
    conn = _connected_keys(data)
    return [(n, nm) for (n, nm) in pins_of(data, ref)
            if (ref, pin_key(n, nm)) not in conn]


# Parts that legitimately sit unconnected / stand alone — never "floating".
_NONELECTRICAL = ("mountinghole", "fiducial", "testpoint", "test_point", "logo",
                  "graphic", "net_tie", "nettie", "solderjumper")


def is_nonelectrical(name, lib=""):
    blob = f"{name} {lib}".lower()
    return any(tok in blob for tok in _NONELECTRICAL)


def fully_floating_parts(data):
    """Electrical parts (>=2 pins) with EVERY pin floating — the hard #6 fault.

    Returns [(ref, name, n_pins), ...]. This is the predicate that, name-keyed,
    silently returned [] for a blank-name crystal and let BUILT-stop lock in a
    dead board.
    """
    conn = _connected_keys(data)
    out = []
    for ref, name, lib, pins in parts(data):
        if is_nonelectrical(name, lib) or len(pins) < 2:
            continue
        floating = [p for p in pins if (ref, pin_key(*p)) not in conn]
        if len(floating) == len(pins):
            out.append((ref, name, len(pins)))
    return out


def partially_floating_parts(data):
    """Electrical parts with SOME (not all) pins floating — surfaced as warnings,
    not faults (an unused GPIO or NC pin is legitimate).

    Returns [(ref, name, [pin_label, ...]), ...].
    """
    conn = _connected_keys(data)
    out = []
    for ref, name, lib, pins in parts(data):
        if is_nonelectrical(name, lib) or not pins:
            continue
        floating = [p for p in pins if (ref, pin_key(*p)) not in conn]
        if floating and len(floating) != len(pins):
            out.append((ref, name, [pin_label(n, nm) for (n, nm) in floating]))
    return out


# ---------------------------------------------------------------------------
# voltage / power topology — ported verbatim from pcb_tools so behavior is
# identical; lives here so the regulator/rail invariants share the one join.
# ---------------------------------------------------------------------------

# Net/pin names that name a voltage rail without using digits.
_KNOWN_RAILS = {"VBUS": 5.0, "VUSB": 5.0, "USB5V": 5.0}


def net_voltage_tokens(name):
    """Recognized nominal voltages implied by a net/pin NAME ('3V3'->3.3, 'VBUS'->5.0).
    High precision: only well-formed rail names count, so a domain isn't invented
    from noise."""
    if not name:
        return set()
    u = name.upper()
    volts = set()
    for k, v in _KNOWN_RAILS.items():
        if k in u:
            volts.add(v)
    for m in re.finditer(r"(?<![A-Z0-9])(\d)V(\d)(?![0-9])", u):           # 3V3 -> 3.3
        volts.add(float(f"{m.group(1)}.{m.group(2)}"))
    for m in re.finditer(r"(?<![A-Z0-9])(\d+(?:\.\d+)?)\s*V(?![A-Z0-9])", u):  # 5V -> 5.0
        try:
            volts.add(float(m.group(1)))
        except ValueError:
            pass
    return volts


def pin_net_map(data):
    """{(ref, pin_name): net_name} for nodes that carry a pin NAME. Used by the
    regulator I/O check, which is named-pin by nature (VI/VO are names)."""
    out = {}
    for nm, nodes in nets(data):
        for ref, _num, pname in nodes:
            if pname:
                out[(ref, pname)] = nm
    return out


_REG_IN = {"VI", "VIN", "+VIN"}
_REG_OUT = {"VO", "VOUT", "+VOUT"}


def is_regulator(name, lib, pin_names):
    """Heuristic: lib says 'regulator', OR the part exposes both an input and an
    output supply pin."""
    in_pin = next((p for p in pin_names if p and p.upper() in _REG_IN), None)
    out_pin = next((p for p in pin_names if p and p.upper() in _REG_OUT), None)
    return ("regulator" in (lib or "").lower()) or bool(in_pin and out_pin)


def regulators(data):
    """[(ref, name, in_pin, out_pin), ...] for parts that look like regulators and
    expose a recognizable input AND output pin."""
    out = []
    for ref, name, lib, pins in parts(data):
        names = [nm for (_n, nm) in pins]
        in_pin = next((p for p in names if p and p.upper() in _REG_IN), None)
        out_pin = next((p for p in names if p and p.upper() in _REG_OUT), None)
        looks_reg = ("regulator" in (lib or "").lower()) or (in_pin and out_pin)
        if looks_reg and in_pin and out_pin:
            out.append((ref, name, in_pin, out_pin))
    return out


def regulator_io_nets(data, ref, in_pin, out_pin):
    """(in_net, out_net) for a regulator's input/output pins (None if unrouted)."""
    pn = pin_net_map(data)
    return pn.get((ref, in_pin)), pn.get((ref, out_pin))
