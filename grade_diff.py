# Xorics — grade_diff.py  (diagnostic, not shipped product)
#
# Points an instrument at the circuit that actually false-BUILT instead of
# guessing. Runs the REAL board, builds power_data the way the inspector does,
# then runs BOTH the old name-only floating check and the new Layer-1 check —
# under the BUGGY lib serialization (str(part.lib) == whole library dump) and a
# CLEAN one (library name) — and prints the verdicts side by side.
#
# Run on the box:
#   ~/xorics-ai/skidl-venv/bin/python ~/xorics-ai/grade_diff.py [path/to/script.py]
#
# What each cell answers:
#   old-shape + raw-lib  -> what the SHIPPED grader saw when it false-BUILT
#   new-shape + raw-lib  -> does Layer-1 ALONE fix it (lib still buggy)?
#   *        + clean-lib -> does fixing the inspector's lib field fix it?

import os
import sys
import tempfile

# KiCad libs must be findable BEFORE skidl loads (same as capture_real_powerdata).
_SYMDIR = os.environ.get("XORICS_KICAD_SYMBOL_DIR", "/usr/share/kicad/symbols")
for _v in ("KICAD6_SYMBOL_DIR", "KICAD7_SYMBOL_DIR",
           "KICAD8_SYMBOL_DIR", "KICAD9_SYMBOL_DIR"):
    os.environ.setdefault(_v, _SYMDIR)

XROOT = os.path.expanduser("~/xorics-ai")
sys.path.insert(0, XROOT)

DEFAULT_SCRIPT = os.path.join(
    XROOT, "circuits", "build_an_atmega328p_breakout_board_in_sk",
    "build_an_atmega328p_breakout_board_in_sk.py")

import netlist_query as nq   # noqa: E402

# Token list MUST match the shipped checkers (pcb_tools + netlist_query).
_NONELECTRICAL = ("mountinghole", "fiducial", "testpoint", "test_point", "logo",
                  "graphic", "net_tie", "nettie", "solderjumper")


# --------------------------------------------------------------------------
# pure analysis (testable WITHOUT skidl)
# --------------------------------------------------------------------------

def old_fully_floating(data):
    """Faithful repro of the SHIPPED _check_unconnected hard-fault path, which
    saw the OLD inspector shape: part pins were bare NAME strings."""
    nets = data.get("nets", []) or []
    parts = data.get("parts", []) or []
    connected = {}
    for nm, nodes in nets:
        if len(nodes) < 2:
            continue
        for ref, num, pname in nodes:
            if pname:
                connected.setdefault(ref, set()).add(pname)
    hard = []
    for ref, name, lib, pnames in parts:
        if any(tok in f"{name} {lib}".lower() for tok in _NONELECTRICAL):
            continue
        pins = [p for p in pnames if p]
        if not pins:
            continue
        conn = connected.get(ref, set())
        floating = [p for p in pins if p not in conn]
        if len(pins) >= 2 and len(floating) == len(pins):
            hard.append(ref)
    return hard


def to_old_shape(pd):
    """Project new-shape part pins [[num,name],...] down to old-shape [name,...]
    so the old name-only checker can be run faithfully on the same board."""
    parts = []
    for ref, name, lib, pins in pd.get("parts", []):
        names = [(p[1] if isinstance(p, (list, tuple)) and len(p) > 1 else p)
                 for p in pins]
        parts.append([ref, name, lib, names])
    return {"nets": pd.get("nets", []), "parts": parts}


def find_part(pd, name_substr):
    for rec in pd.get("parts", []):
        if name_substr.lower() in str(rec[1]).lower():
            return rec
    return None


def nonelectrical_hit(name, lib):
    blob = f"{name} {lib}".lower()
    return [t for t in _NONELECTRICAL if t in blob]


def analyze(pd_raw, pd_clean, target="Crystal"):
    """pd_raw / pd_clean: same board, lib field as dump vs clean name."""
    print("=" * 68)
    print("TARGET PART:", target)
    rec = find_part(pd_raw, target)
    if rec is None:
        print("  !! NOT PRESENT in power_data['parts'] — SKiDL culled it (H2).")
        print("     The lib/join analysis below is moot; the fix is part")
        print("     enumeration, not the checker.")
    else:
        ref, name, lib_raw, pins = rec
        rec_c = find_part(pd_clean, target) or rec
        lib_clean = rec_c[2]
        print(f"  present as ref={ref!r}  name={name!r}")
        print(f"  pins(num,name) = {pins}")
        print(f"  lib field length: raw={len(str(lib_raw))}  clean={len(str(lib_clean))}")
        print(f"  clean lib value : {lib_clean!r}")
        hit_raw = nonelectrical_hit(name, lib_raw)
        hit_clean = nonelectrical_hit(name, lib_clean)
        print(f"  non-electrical match under RAW lib  : {hit_raw or 'none'}")
        print(f"  non-electrical match under CLEAN lib: {hit_clean or 'none'}")
        if hit_raw and not hit_clean:
            print("  >> the RAW lib dump falsely trips the non-electrical filter;")
            print("     a clean lib name does not. THIS is the false-BUILT mechanism.")

    print("-" * 68)
    print(f"{'variant':<22}{'OLD name-only':<22}{'NEW Layer-1'}")
    for label, pd in (("raw-lib", pd_raw), ("clean-lib", pd_clean)):
        old = old_fully_floating(to_old_shape(pd))
        new = [r for r, _n, _c in nq.fully_floating_parts(pd)]
        old_s = "CATCHES " + ",".join(old) if old else "MISSES"
        new_s = "CATCHES " + ",".join(new) if new else "MISSES"
        print(f"{label:<22}{old_s:<22}{new_s}")
    print("=" * 68)


# --------------------------------------------------------------------------
# skidl-dependent: run the real board, build power_data both ways
# --------------------------------------------------------------------------

def _clean_lib(part):
    """Best-effort short library name (the proposed inspector fix). Returns
    (value, source_attr) so we know which attribute to use in pcb_tools."""
    for attr in ("filename", "name"):
        v = getattr(getattr(part, "lib", None), attr, None)
        if isinstance(v, str) and v and "\n" not in v and len(v) < 200:
            return v, f"part.lib.{attr}"
    s = str(getattr(part, "lib", ""))
    return s.split("\n", 1)[0][:120], "str(part.lib) first line (fallback)"


def build_power_data(circuit, lib_mode):
    """Walk the live circuit the way the inspector does. lib_mode: 'raw'|'clean'."""
    nets = []
    for n in circuit.nets:
        nm = str(getattr(n, "name", "") or "")
        nodes = [[str(getattr(getattr(p, "part", None), "ref", "?")),
                  str(getattr(p, "num", "")), str(getattr(p, "name", ""))]
                 for p in n.pins]
        nets.append([nm, nodes])
    parts = []
    for p in circuit.parts:
        if lib_mode == "raw":
            lib = str(getattr(p, "lib", ""))
        else:
            lib = _clean_lib(p)[0]
        pins = [[str(getattr(pp, "num", "")), str(getattr(pp, "name", ""))]
                for pp in p.pins]
        parts.append([str(getattr(p, "ref", "?")), str(getattr(p, "name", "")),
                      lib, pins])
    return {"nets": nets, "parts": parts}


def run_on_board(path):
    os.chdir(tempfile.mkdtemp(prefix="xorics_diff_"))
    import skidl
    ns = {"__name__": "__circuit__"}
    print(f"executing: {path}")
    with open(path) as f:
        src = f.read()
    try:
        exec(compile(src, path, "exec"), ns)
    except SystemExit:
        pass
    except Exception as e:
        print("script raised during build (continuing with whatever built):", repr(e))

    circuit = None
    try:
        from skidl import default_circuit as circuit
    except Exception:
        circuit = getattr(skidl, "default_circuit", None) or skidl.Net().circuit

    pd_raw = build_power_data(circuit, "raw")
    pd_clean = build_power_data(circuit, "clean")

    # report which attr produced the clean lib, for the real fix
    if circuit.parts:
        _v, _src = _clean_lib(circuit.parts[0])
        print(f"clean-lib source attribute: {_src}")
    print(f"parts seen: {len(pd_raw['parts'])}   nets seen: {len(pd_raw['nets'])}")
    analyze(pd_raw, pd_clean, target="Crystal")


if __name__ == "__main__":
    script = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SCRIPT
    if not os.path.exists(script):
        print("script not found:", script)
        sys.exit(1)
    run_on_board(script)
