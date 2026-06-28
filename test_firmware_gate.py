"""Item 1 — firmware as a first-class verified deliverable. Hermetic: stubs xorics's heavy siblings
(but uses the REAL firmware_tools) and monkeypatches the toolchain/model so no GPU or arduino-cli is
needed. Proves compile_check now carries .status, the loop BUILT-stops on a compiled sketch, the
manifest records the right validator, and a firmware build can reach the VERIFIED footer.
    python test_firmware_gate.py        (or: pytest test_firmware_gate.py)
"""
import os
import sys
import json
import types
import tempfile
import subprocess

# --- stub the import-time-heavy siblings (NOT firmware_tools — we want the real one) -----
def _stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m

_noop = lambda *a, **k: ""
class _OpenAI:
    def __init__(self, *a, **k): pass
class _Notebook:
    def __init__(self, *a, **k): pass
    def gate(self, *a, **k): return None
    def record(self, *a, **k): return None
    def render(self): return ""

_stub("openai", OpenAI=_OpenAI)
_stub("datasheet_rag", search_datasheets=_noop)
_stub("web_datasheets", fetch_datasheet=_noop)
_stub("notebook", Notebook=_Notebook)
_stub("pcb_tools", check_circuit=_noop, check_circuit_file=_noop, find_part=_noop,
      find_footprint=_noop, part_pins=_noop, save_circuit=_noop)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import firmware_tools as fw  # noqa: E402  (real module)
import xorics                # noqa: E402


def _isolate():
    d = tempfile.mkdtemp(prefix="xorics_fw_")
    xorics._STATE_DIR = d
    xorics._clear_deliverables()
    return d


# --- compile_check now carries .status (toolchain monkeypatched) -------------------------
class _Proc:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err

def _with_toolchain(run_impl, has_cli=True):
    """Run compile_check with shutil.which / subprocess.run swapped out, restoring after."""
    ow, orr = fw.shutil.which, fw.subprocess.run
    try:
        fw.shutil.which = (lambda *a, **k: "/usr/bin/arduino-cli") if has_cli else (lambda *a, **k: None)
        fw.subprocess.run = run_impl
        return fw.compile_check("void setup(){}\nvoid loop(){}")
    finally:
        fw.shutil.which, fw.subprocess.run = ow, orr

def test_compile_ok_is_built():
    r = _with_toolchain(lambda *a, **k: _Proc(0, "Sketch uses 1234 bytes (3%)"))
    assert r.status == "built", r.status
    assert "COMPILE OK" in r

def test_compile_error_is_failed():
    r = _with_toolchain(lambda *a, **k: _Proc(1, "", "error: 'foo' was not declared in this scope"))
    assert r.status == "failed", r.status
    assert "COMPILE FAILED" in r and "not declared" in r

def test_compile_timeout_is_timeout():
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="arduino-cli", timeout=fw.BUILD_TIMEOUT)
    r = _with_toolchain(_boom)
    assert r.status == "timeout", r.status

def test_missing_toolchain_is_no_toolchain():
    r = _with_toolchain(lambda *a, **k: _Proc(0), has_cli=False)
    assert r.status == "no_toolchain", r.status

def test_compile_result_is_a_plain_string():
    r = _with_toolchain(lambda *a, **k: _Proc(0, "ok"))
    assert isinstance(r, str)            # backward compatible: still reads as text everywhere


# --- the loop BUILT-stops on a compiled sketch (no models) -------------------------------
def _fake_client(create_fn):
    completions = types.SimpleNamespace(create=create_fn)
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))

def _resp_calling(name, arguments):
    fn = types.SimpleNamespace(name=name, arguments=arguments)
    tc = types.SimpleNamespace(id="call_1", type="function", function=fn)
    msg = types.SimpleNamespace(content="", tool_calls=[tc])
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

def test_loop_built_stops_on_firmware_compile():
    calls = {"n": 0}
    def fake_create(model, messages, tools, **kwargs):  # absorb extra_body (reasoning_split) + future kwargs
        calls["n"] += 1
        return _resp_calling("compile_check", json.dumps({"code": "void setup(){}\nvoid loop(){}"}))

    oc, oi = xorics.client, xorics.TOOL_IMPLS["compile_check"]
    try:
        xorics.client = _fake_client(fake_create)
        xorics.TOOL_IMPLS["compile_check"] = lambda code, **k: fw.CompileResult(
            "COMPILE OK\nSketch uses 1234 bytes", "built")
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "write blink"}]
        final_text, _msgs, built_path, outcome = xorics._agent_loop(
            xorics.CODER, msgs, [], checkpoint=False, tag="coder")
        assert outcome["built"] is True, outcome
        assert outcome["design_attempt"] is True, outcome      # compile_check is a design tool
        assert built_path is None                              # inline code, not a file path
        assert "void setup" in final_text                      # the verified sketch was captured
        assert calls["n"] == 1                                 # BUILT-stop fired; no runaway grind
    finally:
        xorics.client, xorics.TOOL_IMPLS["compile_check"] = oc, oi


# --- run_coder records the deliverable under the correct validator -----------------------
def _run_coder_recording(path):
    """Drive run_coder with a canned BUILT outcome + a fake saved path; return the manifest record."""
    ol, osv = xorics._agent_loop, xorics._save_deliverable
    try:
        xorics._agent_loop = lambda *a, **k: ("done\n```cpp\nvoid setup(){}\n```", [], None,
                                              {"built": True, "design_attempt": True})
        xorics._save_deliverable = lambda text, task: path
        xorics.run_coder("build it")
        recs = xorics._load_deliverables()
        assert len(recs) == 1, recs
        return recs[0]
    finally:
        xorics._agent_loop, xorics._save_deliverable = ol, osv

def test_run_coder_labels_firmware_as_compile_check():
    _isolate()
    rec = _run_coder_recording("/tmp/xorics_fw/blink.ino")
    assert rec["validator"] == "compile_check", rec
    assert rec["path"].endswith("blink.ino")

def test_run_coder_labels_pcb_as_check_circuit():
    _isolate()
    rec = _run_coder_recording("/tmp/xorics_fw/board.py")
    assert rec["validator"] == "check_circuit", rec
    assert rec["path"].endswith("board.py")


# --- end-to-end (hermetic): a firmware build reaches the VERIFIED footer + finalize ------
def test_firmware_build_reaches_verified():
    d = _isolate()
    ino = os.path.join(d, "blink.ino")
    open(ino, "w").write("void setup(){}\nvoid loop(){}\n")
    ol, osv = xorics._agent_loop, xorics._save_deliverable
    try:
        xorics._agent_loop = lambda *a, **k: ("done\n```cpp\nvoid setup(){}\n```", [], None,
                                              {"built": True, "design_attempt": True})
        xorics._save_deliverable = lambda text, task: ino
        before = len(xorics._load_deliverables())              # 0
        xorics.run_coder("blink firmware")                     # records the .ino (compile_check)
        out = xorics._append_manifest_footer("Firmware is complete.", {"design_attempt": True}, before)
        assert "\u2713 VERIFIED" in out and "blink.ino" in out, out
        assert xorics.finalize_design([ino]).status == "verified"
    finally:
        xorics._agent_loop, xorics._save_deliverable = ol, osv


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
