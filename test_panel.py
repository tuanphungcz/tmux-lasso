#!/usr/bin/env python3
import os
import tempfile
import unittest

import panel


class WatchedPyParsesTests(unittest.TestCase):
    """The hot-reload must not re-exec into a file caught mid-save, or the panel
    crashes and the pane closes."""

    def setUp(self):
        self._watch = panel.WATCH

    def tearDown(self):
        panel.WATCH = self._watch

    def _mk(self, content):
        fd, p = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w") as f:
            f.write(content)
        return p

    def test_true_when_all_watched_py_parse(self):
        good = self._mk("x = 1\n")
        try:
            panel.WATCH = [good, "/nope/toggle.sh"]   # non-.py is ignored
            self.assertTrue(panel.watched_py_parses())
        finally:
            os.unlink(good)

    def test_false_on_midsave_syntax_error(self):
        bad = self._mk("def f(:\n")                   # half-written edit
        try:
            panel.WATCH = [bad]
            self.assertFalse(panel.watched_py_parses())
        finally:
            os.unlink(bad)

    def test_missing_files_do_not_block_reload(self):
        panel.WATCH = ["/nope/x.py", "/nope/toggle.sh"]
        self.assertTrue(panel.watched_py_parses())


if __name__ == "__main__":
    unittest.main()
