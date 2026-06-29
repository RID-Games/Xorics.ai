#!/usr/bin/env python3
# Xorics — test: read_file resolves a repo file even when handed a wrong path (the
# REPO_ROOT/<basename> fallback), while still erroring on a genuinely missing file and
# no longer printing the old misleading example. Hermetic; imports xorics and calls
# read_file directly — no model, no network, no GPU.

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # so it runs from any cwd
import xorics


class ReadFileFallback(unittest.TestCase):
    KNOWN = "run_tests.sh"   # a file that is always at the repo root

    def test_wrong_absolute_path_resolves_to_repo_file(self):
        # A real repo file named via a wrong home dir must resolve through the fallback.
        known = os.path.join(xorics.REPO_ROOT, self.KNOWN)
        self.assertTrue(os.path.exists(known))                    # sanity: known file present
        out = xorics.read_file("/home/someone-not-me/xorics-ai/" + os.path.basename(known))
        self.assertTrue(out.startswith("----- contents of "))
        self.assertNotIn("No file at", out)

    def test_correct_absolute_path_still_resolves(self):
        out = xorics.read_file(os.path.join(xorics.REPO_ROOT, self.KNOWN))
        self.assertTrue(out.startswith("----- contents of "))

    def test_genuinely_missing_file_still_errors(self):
        # A basename with no repo match must still error, with the new (non-misleading) message.
        out = xorics.read_file("/home/nobody/xorics-ai/" + self.KNOWN + ".definitely_not_here")
        self.assertTrue(out.startswith("No file at "))
        self.assertNotIn("prompts/<name>.md", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
