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
            tempfile.gettempdir(), f"tmux-lasso-agents-test-{os.getpid()}.json"
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
                "⠧ tmux-lasso",
                "• Working (24s • esc to interrupt)\n\n  gpt-5.6-sol medium · Context 71% left · ~/Projects/tmux-lasso",
            ),
            "codex",
        )

    def test_detect_agent_kind_claude_from_body(self):
        self.assertEqual(
            detect.detect_agent_kind("python", "", "Claude Code\nBypass permissions"),
            "claude",
        )

    def test_detect_agent_kind_from_title(self):
        # Non-shell commands trust the title for agent detection.
        self.assertEqual(detect.detect_agent_kind("node", "codex", ""), "codex")
        self.assertEqual(detect.detect_agent_kind("node", "Claude Code", ""), "claude")
        # Shell commands ignore the title (it's stale from a previous agent).
        self.assertIsNone(detect.detect_agent_kind("zsh", "codex", ""))
        self.assertIsNone(detect.detect_agent_kind("zsh", "Claude Code", ""))

    def test_detect_agent_kind_pi(self):
        # pi-coding-agent runs as `node` with a static "π - <cwd>" title
        self.assertEqual(detect.detect_agent_kind("node", "π - tmux-lasso", ""), "pi")
        self.assertEqual(
            detect.detect_agent_kind("node", "", "New version of pi-coding-agent"),
            "pi",
        )

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

    def test_pi_states(self):
        self.assertEqual(detect._state_pi("π - tmux-lasso", "⠴ Working..."), "working")
        self.assertEqual(detect._state_pi("π - tmux-lasso", "Run npm install? [y/N]"), "blocked")
        self.assertEqual(
            detect._state_pi("π - tmux-lasso", "~/Projects/tmux-lasso (main)\n⚡ 0.0 tps"), "idle"
        )

    def test_activity_summary_prefers_first_prompt_line(self):
        body = (
            "gpt-5.6-sol medium · Context 71% left\n"
            "› Refactor checkout flow\n"
            "› Then rewrite the docs later\n"
            "• Working (24s • esc to interrupt)\n"
        )
        self.assertEqual(
            detect.activity_summary("codex", "⠧ acme-storefront", body),
            "Refactor checkout flow",
        )

    def test_activity_summary_is_short(self):
        body = "› " + ("Refactor checkout to serve logged-out users " * 4)
        self.assertLessEqual(len(detect.activity_summary("codex", "", body)), detect.SUMMARY_MAX)

    def test_activity_summary_is_first_four_prompt_words(self):
        body = "› Jako to je můj use case je, že nikdy nepracuju na obou zařízeních naraz."
        self.assertEqual(detect.activity_summary("claude", "", body), "Jako to je můj")
        self.assertLessEqual(len(detect.activity_summary("claude", "", body).split()), 4)

    def test_activity_summary_does_not_apply_stopword_maps(self):
        body = "› Hele, teďka mám jeden Mac mini imich Mac, na kterém bych rozjel agent"
        self.assertEqual(detect.activity_summary("claude", "", body), "Hele teďka mám jeden")

    def test_activity_summary_strips_claude_choice_bullets(self):
        body = "● How is Claude doing this session? (optional)"
        self.assertEqual(
            detect.activity_summary("claude", "✳ ready", body),
            "",
        )

    def test_activity_summary_ignores_claude_status_without_prompt(self):
        body = (
            "● How is Claude doing this session? (optional)\n"
            "────────────────\n"
            "❯\n"
            "────────────────\n"
            "  Opus 4.6 (1M context) [███████   ] 747k left (74%)\n"
            "  ⏵⏵ bypass permissions on · You've used 88% of your weekly limit\n"
        )
        self.assertEqual(
            detect.activity_summary("claude", "✳ Compare web search methods", body),
            "",
        )

    def test_activity_summary_ignores_timing_table_rows(self):
        body = "LFM 230M │ 2.86s │ ? │ 16 tok/s"
        self.assertEqual(
            detect.activity_summary("claude", "✳ Compare web search methods", body),
            "",
        )

    def test_activity_summary_ignores_url_rows(self):
        body = "https://huggingface.co/YuvrajSingh9886/LFM2.5-350M-grpo-summarization-quality-bleu"
        self.assertEqual(detect.activity_summary("claude", "✳ Download model", body), "")

    def test_activity_summary_does_not_guess_from_output_without_prompt_or_title(self):
        body = "Máme srvnat tyto dva modely?\nLiquidAI 230M bf16 4"
        self.assertEqual(detect.activity_summary("claude", "✳ ready", body), "")

    def test_activity_summary_keeps_first_four_prompt_words(self):
        body = "› Máme srvnat tyto dva modely?"
        self.assertEqual(detect.activity_summary("claude", "✳ ready", body), "Máme srvnat tyto dva")

    def test_pi_frozen_spinner_clears_to_done(self):
        # pi can leave a non-animating "⠴ Working..." on screen after it finishes;
        # tmux-lasso must not report 'working' forever. A frozen glyph -> done; an
        # animating glyph (changes tick-to-tick) stays working.
        original_ttl = detect.CAPTURE_TTL
        original_cap = detect.capture_body
        detect.CAPTURE_TTL = 0
        body = {"v": "⠴ Working..."}
        detect.capture_body = lambda pid: body["v"]
        try:
            panes = {
                "123": {
                    "pane_id": "%123", "session": "s", "win_index": "1",
                    "win_name": "w", "pane_index": "0", "path": "/tmp",
                    "visible": False, "win_current": True, "title": "π - w",
                    "sidebar": False, "command": "node",
                }
            }
            self.assertEqual(detect.scrape_agents(panes, 100)[0]["state"], "working")
            # same glyph next tick -> spinner frozen -> finished
            self.assertEqual(detect.scrape_agents(panes, 101)[0]["state"], "done")
            # spinner advances again -> pi is genuinely working
            body["v"] = "⠦ Working..."
            self.assertEqual(detect.scrape_agents(panes, 102)[0]["state"], "working")
        finally:
            detect.CAPTURE_TTL = original_ttl
            detect.capture_body = original_cap

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

    def test_scrape_agents_ignores_stale_title_on_shell_pane(self):
        """A shell pane whose title is left over from a dead agent should not
        be reported as an agent."""
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

        self.assertEqual(agents, [])

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

    def test_done_clears_when_visible_even_with_unchanged_prompt(self):
        # Old behaviour: a finished Claude pane clears from 'done' to 'idle' as
        # soon as it is visible/focused, even if the prompt is still unchanged.
        original_ttl = detect.CAPTURE_TTL
        original_cap = detect.capture_body
        detect.CAPTURE_TTL = 0
        body = {"v": ""}
        detect.capture_body = lambda pid: body["v"]
        try:
            panes = {
                "123": {
                    "pane_id": "%123", "session": "s", "win_index": "1",
                    "win_name": "w", "pane_index": "0", "path": "/tmp",
                    "visible": True, "win_current": True, "title": "⣾ thinking",
                    "sidebar": False, "command": "claude",
                }
            }
            self.assertEqual(detect.scrape_agents(panes, 100)[0]["state"], "working")
            panes["123"]["title"] = "✳ ready"
            body["v"] = "────────\n❯\n────────"            # finished, empty prompt
            self.assertEqual(detect.scrape_agents(panes, 101)[0]["state"], "done")
            cleared = detect.scrape_agents(panes, 102)
        finally:
            detect.CAPTURE_TTL = original_ttl
            detect.capture_body = original_cap
        self.assertEqual(cleared[0]["state"], "idle")
        self.assertEqual(cleared[0]["ts"], 102)

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

    def test_agents_from_cache_includes_cached_summary(self):
        detect._save_scrape({
            "123": {
                "agent": "codex",
                "state": "working",
                "summary": "migrate test suite",
                "ts": 100,
                "cap_t": 0.0,
            }
        })
        panes = {
            "123": {
                "pane_id": "%123", "session": "s", "win_index": "1",
                "win_name": "w", "pane_index": "0", "path": "/tmp",
                "visible": True, "win_current": True, "title": "",
                "sidebar": False, "command": "codex",
            }
        }
        self.assertEqual(detect.agents_from_cache(panes, 200)[0]["summary"], "migrate test suite")

    def test_cached_summary_is_not_replaced_by_later_prompt(self):
        original_ttl = detect.CAPTURE_TTL
        original_cap = detect.capture_body
        detect.CAPTURE_TTL = 0
        body = {"v": "› First task title\n• Working (1s • esc to interrupt)"}
        detect.capture_body = lambda _pane_id: body["v"]
        try:
            panes = {
                "123": {
                    "pane_id": "%123", "session": "s", "win_index": "1",
                    "win_name": "w", "pane_index": "0", "path": "/tmp",
                    "visible": True, "win_current": True, "title": "⣾ thinking",
                    "sidebar": False, "command": "codex",
                }
            }
            self.assertEqual(detect.scrape_agents(panes, 100)[0]["summary"], "First task title")
            body["v"] = "› Completely different follow-up prompt\n• Working (2s • esc to interrupt)"
            self.assertEqual(detect.scrape_agents(panes, 101)[0]["summary"], "First task title")
        finally:
            detect.CAPTURE_TTL = original_ttl
            detect.capture_body = original_cap

    def test_old_summary_cache_version_is_recomputed(self):
        original_ttl = detect.CAPTURE_TTL
        original_cap = detect.capture_body
        original_cache = detect.load_scrape()
        detect.CAPTURE_TTL = 0
        detect._save_scrape({
            "123": {
                "agent": "codex",
                "state": "working",
                "ts": 100,
                "cap_t": 0.0,
                "summary": "Very old verbose title that should not survive",
            }
        })
        detect.capture_body = lambda _pane_id: "› Short fresh title\n• Working"
        try:
            panes = {
                "123": {
                    "pane_id": "%123", "session": "s", "win_index": "1",
                    "win_name": "w", "pane_index": "0", "path": "/tmp",
                    "visible": True, "win_current": True, "title": "⣾ thinking",
                    "sidebar": False, "command": "codex",
                }
            }
            agent = detect.scrape_agents(panes, 101)[0]
        finally:
            detect.CAPTURE_TTL = original_ttl
            detect.capture_body = original_cap
            detect._save_scrape(original_cache)
        self.assertEqual(agent["summary"], "Short fresh title")


if __name__ == "__main__":
    unittest.main()
