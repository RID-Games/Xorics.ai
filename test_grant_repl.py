#!/usr/bin/env python3
"""test_grant_repl.py — house check for the B3 /grant /revoke /grants REPL surface
(XORICS-FEATURE: tool-permissions). Hermetic: imports xorics, no GPU, no toolchain.

Checks (PASS/FAIL counters):
  SRC.1  src contains the exact branch opener 'q == "/grants"'
  SRC.2  src contains the exact branch opener 'q.startswith("/grant ")'
  SRC.3  src contains the exact branch opener 'q.startswith("/revoke ")'
  SRC.4  src contains the help-line substring '/grant /revoke /grants'
  BEH.1  _grant_tool("str_replace") is True
  BEH.2  _is_granted("str_replace") is True (after grant)
  BEH.3  _revoke_tool("str_replace") is True
  BEH.4  _is_granted("str_replace") is False (after revoke)

Behavior re-checks start from xorics._TOOL_GRANTS.clear() so a prior grant from another
suite / prior run can't make BEH.3 a false positive (otherwise a leftover grant makes
revoke 'nothing to revoke' and BEH.3 fails for the wrong reason).
"""
import sys
import xorics


PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")


def main():
    src = open(xorics.__file__).read()

    # ---- SRC: REPL surface text is present in xorics.py --------------------
    check("SRC.1 src has 'q == \"/grants\"' branch opener",
          'q == "/grants"' in src)
    check("SRC.2 src has 'q.startswith(\"/grant \")' branch opener",
          'q.startswith("/grant ")' in src)
    check("SRC.3 src has 'q.startswith(\"/revoke \")' branch opener",
          'q.startswith("/revoke ")' in src)
    check("SRC.4 src has '/grant /revoke /grants' in the help line",
          "/grant /revoke /grants" in src)

    # ---- BEH: grant/grant-check/revoke/revoke-check round trip --------------
    # start clean so a leftover grant can't make BEH.3 a false positive
    xorics._TOOL_GRANTS.clear()

    check("BEH.1 _grant_tool('str_replace') returns True",
          xorics._grant_tool("str_replace") is True)
    check("BEH.2 _is_granted('str_replace') is True after grant",
          xorics._is_granted("str_replace") is True)
    check("BEH.3 _revoke_tool('str_replace') returns True",
          xorics._revoke_tool("str_replace") is True)
    check("BEH.4 _is_granted('str_replace') is False after revoke",
          xorics._is_granted("str_replace") is False)

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()