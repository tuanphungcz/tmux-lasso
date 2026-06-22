#!/usr/bin/env python3
import os
import tempfile
import unittest

import detect


class DetectTests(unittest.TestCase):
    def setUp(self):
        # Isolate the shared scrape cache per test (it lives in a file now).
        self._orig_scrape_file = detect._SCRAPE_FILE
        detect._SCRAPE_FILE = os.path.join(
            tempfile.gettempdir(), f"lasso-agents-test-{os.getpid()}.json"
        )
        self._reset_scrape()

    def tearDown(self):
        self._reset_scrape()
        detect._SCRAPE_FILE = self._orig_scrape_file

    def _reset_scrape(self):
        try:
            os.remove(detect._SCRAPE_FILE)
        except OSError:
            pass

    def test_detect_agent_kind_codex_from_title_and_body(self):
        self.assertEqual(
            detect.detect_agent_kind("node", "Action Required", ""),
            "codex",
        )
        self.assertEqual(
            detect.detect_agent_kind("node", "", "codex\npress enter to confirm or esc to cancel"),
            "codex",
        )
        self.assertEqual(
            detect.detect_agent_kind(
                "node",
                "⠧ Lasso",
                "• Working (24s • esc to interrupt)\n\n  gpt-5.4 medium · Context 71% left · ~/Projects/lasso",
            ),
            "codex",
        )

    def test_detect_agent_kind_claude_from_body(self):
        self.assertEqual(
            detect.detect_agent_kind("python", "", "Claude Code\nBypass permissions"),
            "claude",
        )

    def test_detect_agent_kind_from_title(self):
        self.assertEqual(detect.detect_agent_kind("zsh", "codex", ""), "codex")
        self.assertEqual(detect.detect_agent_kind("zsh", "Claude Code", ""), "claude")

    def test_codex_states(self):
        self.assertEqual(detect._state_codex("⣾ thinking", ""), "working")
        self.assertEqual(detect._state_codex("Action Required", ""), "blocked")
        self.assertEqual(
            detect._state_codex("", "Allow command?\nYes (y)\nNo (n)"),
            "blocked",
        )
        self.assertEqual(
            detect._state_codex(
                "codex",
                "old transcript line\n• Working (24s • esc to interrupt)\nfinished output",
            ),
            "idle",
        )
        self.assertEqual(detect._state_codex("codex", ""), "idle")

    def test_claude_states(self):
        self.assertEqual(detect._state_claude("⣾ thinking", ""), "working")
        self.assertEqual(
            detect._state_claude("", "Enter to select\nEsc to cancel\nTab/arrow keys to navigate"),
            "blocked",
        )
        self.assertEqual(detect._state_claude("✳ ready", ""), "idle")
        self.assertEqual(detect._state_claude("", "   ❯"), "idle")

    def test_scrape_agents_preserves_previous_state_across_unknown_overlay(self):
        panes = {
            "123": {
                "pane_id": "%123",
                "session": "s",
                "win_index": "1",
                "win_name": "w",
                "pane_index": "0",
                "path": "/tmp",
                "visible": True,
                "win_current": True,
                "title": "Action Required",
                "sidebar": False,
                "command": "codex",
            }
        }

        first = detect.scrape_agents(panes, 100)
        self.assertEqual(first[0]["state"], "blocked")

        original_capture = detect.capture_body
        try:
            detect.capture_body = lambda _pane_id: (
                "↑/↓ to scroll\npgup/pgdn to\nhome/end to jump\nq to quit\nesc to edit prev"
            )
            panes["123"]["title"] = ""
            second = detect.scrape_agents(panes, 101)
        finally:
            detect.capture_body = original_capture

        self.assertEqual(second[0]["state"], "blocked")
        self.assertEqual(second[0]["ts"], 100)

    def test_scrape_agents_uses_persisted_file_cache(self):
        # Cross-process state lives in the shared scrape file now, not tmux
        # options: a seeded "working" timestamp must survive into this scrape.
        detect._save_scrape(
            {"123": {"agent": "codex", "state": "working", "ts": 100, "cap_t": 0.0}}
        )
        original_ttl = detect.CAPTURE_TTL
        original_capture = detect.capture_body
        detect.CAPTURE_TTL = 0
        detect.capture_body = lambda _pane_id: ""
        try:
            panes = {
                "123": {
                    "pane_id": "%123",
                    "session": "s",
                    "win_index": "1",
                    "win_name": "w",
                    "pane_index": "0",
                    "path": "/tmp",
                    "visible": True,
                    "win_current": True,
                    "title": "⣾ thinking",
                    "sidebar": False,
                    "command": "codex",
                }
            }

            agents = detect.scrape_agents(panes, 160)
        finally:
            detect.CAPTURE_TTL = original_ttl
            detect.capture_body = original_capture

        self.assertEqual(agents[0]["state"], "working")
        self.assertEqual(agents[0]["ts"], 100)

    def test_scrape_agents_does_not_skip_shell_with_agent_title(self):
        original_ttl = detect.CAPTURE_TTL
        detect.CAPTURE_TTL = 0
        try:
            panes = {
                "123": {
                    "pane_id": "%123",
                    "session": "s",
                    "win_index": "1",
                    "win_name": "w",
                    "pane_index": "0",
                    "path": "/tmp",
                    "visible": True,
                    "win_current": True,
                    "title": "codex",
                    "sidebar": False,
                    "command": "zsh",
                }
            }

            agents = detect.scrape_agents(panes, 100)
        finally:
            detect.CAPTURE_TTL = original_ttl

        self.assertEqual(agents[0]["agent"], "codex")
        self.assertEqual(agents[0]["state"], "idle")

    def test_scrape_agents_marks_codex_done_after_working_to_idle_transition(self):
        original_ttl = detect.CAPTURE_TTL
        detect.CAPTURE_TTL = 0
        try:
            panes = {
                "123": {
                    "pane_id": "%123",
                    "session": "s",
                    "win_index": "1",
                    "win_name": "w",
                    "pane_index": "0",
                    "path": "/tmp",
                    "visible": True,
                    "win_current": True,
                    "title": "⣾ thinking",
                    "sidebar": False,
                    "command": "codex",
                }
            }

            first = detect.scrape_agents(panes, 100)
            self.assertEqual(first[0]["state"], "working")

            panes["123"]["visible"] = False
            panes["123"]["title"] = "codex"
            second = detect.scrape_agents(panes, 101)
            third = detect.scrape_agents(panes, 102)
        finally:
            detect.CAPTURE_TTL = original_ttl

        self.assertEqual(second[0]["state"], "done")
        self.assertEqual(second[0]["ts"], 101)
        self.assertEqual(third[0]["state"], "done")
        self.assertEqual(third[0]["ts"], 101)

    def test_scrape_agents_marks_claude_done_after_working_to_idle_transition(self):
        original_ttl = detect.CAPTURE_TTL
        detect.CAPTURE_TTL = 0
        try:
            panes = {
                "123": {
                    "pane_id": "%123",
                    "session": "s",
                    "win_index": "1",
                    "win_name": "w",
                    "pane_index": "0",
                    "path": "/tmp",
                    "visible": True,
                    "win_current": True,
                    "title": "⣾ thinking",
                    "sidebar": False,
                    "command": "claude",
                }
            }

            first = detect.scrape_agents(panes, 100)
            self.assertEqual(first[0]["state"], "working")

            panes["123"]["visible"] = False
            panes["123"]["title"] = "✳ ready"
            second = detect.scrape_agents(panes, 101)
            third = detect.scrape_agents(panes, 102)
        finally:
            detect.CAPTURE_TTL = original_ttl

        self.assertEqual(second[0]["state"], "done")
        self.assertEqual(second[0]["ts"], 101)
        self.assertEqual(third[0]["state"], "done")
        self.assertEqual(third[0]["ts"], 101)

    def test_scrape_agents_clears_done_when_pane_becomes_visible(self):
        original_ttl = detect.CAPTURE_TTL
        detect.CAPTURE_TTL = 0
        try:
            panes = {
                "123": {
                    "pane_id": "%123",
                    "session": "s",
                    "win_index": "1",
                    "win_name": "w",
                    "pane_index": "0",
                    "path": "/tmp",
                    "visible": True,
                    "win_current": True,
                    "title": "⣾ thinking",
                    "sidebar": False,
                    "command": "claude",
                }
            }

            first = detect.scrape_agents(panes, 100)
            self.assertEqual(first[0]["state"], "working")

            panes["123"]["visible"] = False
            panes["123"]["title"] = "✳ ready"
            second = detect.scrape_agents(panes, 101)
            self.assertEqual(second[0]["state"], "done")

            panes["123"]["visible"] = True
            third = detect.scrape_agents(panes, 102)
        finally:
            detect.CAPTURE_TTL = original_ttl

        self.assertEqual(third[0]["state"], "idle")
        self.assertEqual(third[0]["ts"], 102)

    def test_agents_from_cache_never_captures(self):
        # The render path must read the daemon's cache only -- guard against a
        # refactor sneaking capture-pane back onto the paint loop.
        detect._save_scrape(
            {"123": {"agent": "codex", "state": "working", "ts": 100, "cap_t": 0.0}}
        )
        panes = {
            "123": {
                "pane_id": "%123", "session": "s", "win_index": "1",
                "win_name": "w", "pane_index": "0", "path": "/tmp",
                "visible": True, "win_current": True, "title": "",
                "sidebar": False, "command": "codex",
            }
        }

        def _boom(*_a, **_k):
            raise AssertionError("capture_body must not run on the render path")

        original = detect.capture_body
        detect.capture_body = _boom
        try:
            agents = detect.agents_from_cache(panes, 200)
        finally:
            detect.capture_body = original
        self.assertEqual(agents[0]["agent"], "codex")
        self.assertEqual(agents[0]["state"], "working")


if __name__ == "__main__":
    unittest.main()
