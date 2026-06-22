#!/usr/bin/env python3
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import usage


class UsageTests(unittest.TestCase):
    """The daemon is the sole fetcher; everyone else just reads the shared file.
    snapshot() must never touch the network -- it's a plain file read."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self._cache = usage.CACHE
        usage.CACHE = self.path

    def tearDown(self):
        usage.CACHE = self._cache
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _write(self, data, age=0.0):
        with open(self.path, "w") as f:
            json.dump(data, f)
        if age:
            t = time.time() - age
            os.utime(self.path, (t, t))

    def test_snapshot_reads_file_without_fetching(self):
        self._write({"claude": {"session": 12.0}})
        with mock.patch.object(usage, "fetch_claude", side_effect=AssertionError), \
             mock.patch.object(usage, "fetch_codex", side_effect=AssertionError):
            snap = usage.snapshot()           # must not hit the network
        self.assertEqual(snap.get("claude", {}).get("session"), 12.0)

    def test_snapshot_returns_stale_values_too(self):
        # The daemon owns freshness; snapshot just surfaces whatever's on disk.
        self._write({"claude": {"session": 40.0}}, age=usage.TTL + 30)
        self.assertEqual(usage.snapshot().get("claude", {}).get("session"), 40.0)

    def test_snapshot_empty_when_no_file(self):
        os.unlink(self.path)
        self.assertEqual(usage.snapshot(), {})

    def test_fetch_once_writes_and_keeps_last_known_on_failure(self):
        self._write({"claude": {"session": 10.0}, "codex": {"session": 50.0}})
        with mock.patch.object(usage, "fetch_claude", return_value={"session": 20.0}), \
             mock.patch.object(usage, "fetch_codex", return_value=None):
            usage.fetch_once()
        snap = usage.snapshot()
        self.assertEqual(snap["claude"]["session"], 20.0)   # updated
        self.assertEqual(snap["codex"]["session"], 50.0)    # kept: its fetch failed

    def test_force_refresh_sets_tmux_flag(self):
        calls = []
        with mock.patch.object(usage.tmux_api, "run",
                               side_effect=lambda *a, **k: calls.append(a) or ""):
            usage.force_refresh()
        self.assertIn(("set", "-g", usage.FORCE_OPT, "1"), calls)


if __name__ == "__main__":
    unittest.main()
