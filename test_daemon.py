#!/usr/bin/env python3
import unittest

import daemon


class SoundEdgeTests(unittest.TestCase):
    """daemon._announce_edges: one afplay per working->done / ->blocked edge,
    each with its own sound, gated on @tmux_lasso_announce, no cold-start replay."""

    OPTS = {
        "@tmux_lasso_announce": "on",
        "@tmux_lasso_sound": "/done.mp3",
        "@tmux_lasso_sound_request": "/request.mp3",
    }

    def setUp(self):
        self._fired = []
        self._orig_popen = daemon.subprocess.Popen
        self._orig_run = daemon.tmux_api.run
        self._opts = dict(self.OPTS)
        daemon.subprocess.Popen = lambda args, **kw: self._fired.append(args) or _Dummy()
        daemon.tmux_api.run = lambda *a, **k: self._opts.get(a[-1], "")

    def tearDown(self):
        daemon.subprocess.Popen = self._orig_popen
        daemon.tmux_api.run = self._orig_run

    def _after(self, state):
        return {"3": {"agent": "claude", "state": state}}

    def test_done_plays_the_done_sound(self):
        daemon._announce_edges(
            {"3": {"agent": "claude", "state": "working"}}, self._after("done"), {})
        self.assertEqual(self._fired, [["afplay", "/done.mp3"]])

    def test_blocked_plays_the_request_sound(self):
        daemon._announce_edges(
            {"3": {"agent": "claude", "state": "working"}}, self._after("blocked"), {})
        self.assertEqual(self._fired, [["afplay", "/request.mp3"]])

    def test_steady_done_does_not_replay(self):
        daemon._announce_edges(self._after("done"), self._after("done"), {})
        self.assertEqual(self._fired, [])

    def test_cold_start_does_not_replay(self):
        # no `before` entry (fresh pane / daemon restart) -> stay silent
        daemon._announce_edges({}, self._after("done"), {})
        self.assertEqual(self._fired, [])

    def test_idle_is_silent(self):
        daemon._announce_edges(
            {"3": {"agent": "claude", "state": "done"}}, self._after("idle"), {})
        self.assertEqual(self._fired, [])

    def test_gate_off_is_silent(self):
        self._opts["@tmux_lasso_announce"] = "off"
        daemon._announce_edges(
            {"3": {"agent": "claude", "state": "working"}}, self._after("done"), {})
        self.assertEqual(self._fired, [])


class _Dummy:
    pass


if __name__ == "__main__":
    unittest.main()
