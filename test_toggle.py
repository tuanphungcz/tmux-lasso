#!/usr/bin/env python3
"""Smoke tests for toggle.sh -- the bash control plane has no unit framework, so
these run `sh -n` (parse) and the script's own `__selftest` (pure-function
checks) via subprocess, catching regressions like a broken sidebar-width floor."""
import os
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOGGLE = os.path.join(HERE, "toggle.sh")


class ToggleSmokeTests(unittest.TestCase):
    def test_sh_syntax(self):
        r = subprocess.run(["sh", "-n", TOGGLE], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_selftest_passes(self):
        # desktop_width width-floor checks -- pure, no tmux needed.
        r = subprocess.run([TOGGLE, "__selftest"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ok", r.stdout)


if __name__ == "__main__":
    unittest.main()
