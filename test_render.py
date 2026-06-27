#!/usr/bin/env python3
import re
import unittest
from unittest import mock

import render
import tmux_api

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(s):
    return _ANSI.sub("", s)


class RenderTests(unittest.TestCase):
    def test_tmux_api_run_preserves_trailing_separators(self):
        result = mock.Mock(stdout=f"a{tmux_api.SEP}{tmux_api.SEP}\n")
        with mock.patch("subprocess.run", return_value=result):
            self.assertEqual(tmux_api.run("x"), f"a{tmux_api.SEP}{tmux_api.SEP}")

    def test_compact_state_shows_blocked_as_time_only(self):
        rendered, visible = render.compact_state("blocked", 65)
        self.assertIn("1:05", rendered)
        self.assertNotIn("blocked", rendered)
        self.assertGreater(visible, 1)

    def test_window_rows_use_blocked_time_when_pill_does_not_fit(self):
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
        windows = [{
            "window_id": "@1",
            "session": "s",
            "win_index": "1",
            "win_name": "w",
            "win_current": True,
        }]
        by_window = {
            "1": [{
                **panes["123"],
                "state": "blocked",
                "agent": "codex",
                "ts": 100,
                "space": "/tmp",
                "space_label": "tmux-lasso",
                "branch": "",
            }]
        }

        rows = render._window_rows(12, "s", 160, windows, by_window, panes)
        line, _target = rows[1]
        self.assertNotIn("blocked", _plain(rows[0][0]))
        self.assertIn("1:00", line)
        self.assertNotIn("blocked", line)
        self.assertLessEqual(len(_plain(line)), 12)

    def test_header_exposes_sync_and_switch_buttons(self):
        _line, target = render.header(20)

        self.assertEqual(target[0], "buttons")
        self.assertEqual(target[1][0][2], ("sync",))
        self.assertEqual(target[1][1][2], ("switch",))
        self.assertLess(target[1][0][1], target[1][1][0])

    def test_window_line_shows_reset_remaining_and_fills_width(self):
        line = render._window_line(26, "claude", 50.0, 9000)
        plain = _plain(line)
        self.assertEqual(len(plain), 26)         # bar absorbs slack to full width
        self.assertIn("claude", plain)
        self.assertIn("2h30m", plain)            # reset countdown (left of bar)
        self.assertIn("50%", plain)              # 100 - 50 used = remaining

    def test_window_line_weekly_shows_day_scale_reset(self):
        line = render._window_line(26, "", 18.0, 200000)
        plain = _plain(line)
        self.assertEqual(len(plain), 26)
        self.assertIn("82%", plain)              # weekly remaining
        self.assertIn("2d7h", plain)             # day-scale countdown to reset

    def test_window_line_never_exceeds_width(self):
        # A reset like 22h41m used to render 6 cols where 5 were budgeted, so the
        # row overflowed by one and wrapped — scrolling the sidebar's top row off
        # and knocking every click one row out of alignment.
        for reset in (22 * 3600 + 41 * 60, 23 * 3600 + 59 * 60, 9 * 3600 + 59 * 60):
            for w in (26, 35):
                self.assertLessEqual(len(_plain(render._window_line(w, "claude", 61.0, reset))), w)

    def test_window_line_colors_by_amount_remaining(self):
        # lots left -> green
        self.assertIn(render.USAGE_GREEN, render._window_line(26, "claude", 10.0, 9000))
        # nearly out -> red
        self.assertIn(render.USAGE_RED, render._window_line(26, "codex", 92.0, 3000))

    def test_window_line_handles_missing_data(self):
        plain = _plain(render._window_line(26, "codex", None, None))
        self.assertIn("codex", plain)
        self.assertIn("—", plain)

    def test_fmt_ago_is_whole_minutes(self):
        self.assertEqual(render._fmt_ago(30), "now")
        self.assertEqual(render._fmt_ago(90), "1m")
        self.assertEqual(render._fmt_ago(720), "12m")

    def test_usage_rows_shows_both_windows_with_divider(self):
        snap = {"claude": {"session": 50.0, "session_reset": 9000,
                           "week": 18.0, "week_reset": 200000},
                "codex": {"session": 80.0, "session_reset": 3000,
                          "week": 35.0, "week_reset": 400000}}
        rows = render.usage_rows(26, snap, synced_age=720, now=0)
        # rule + header + claude(5h,7d) + divider + codex(5h,7d)
        self.assertEqual(len(rows), 7)
        text = "\n".join(_plain(line) for line, _ in rows)
        self.assertIn("usage", text)
        self.assertIn("12m", text)               # minutes-ago in the header
        self.assertIn("2h30m", text)             # session reset countdown
        self.assertIn("50%", text)               # session remaining
        self.assertIn("82%", text)               # weekly remaining
        self.assertIn("2d7h", text)              # weekly reset countdown
        self.assertIsNone(rows[4][1])            # divider between providers
        self.assertIn("─", rows[4][0])
        self.assertTrue(any(t == ("usage",) for _line, t in rows))

    def test_usage_rows_empty_until_data_lands(self):
        self.assertEqual(render.usage_rows(26, {}), [])
        # rule + header + 5h + 7d for the single provider present
        rows = render.usage_rows(26, {"claude": {"session": 10.0, "week": 40.0}}, now=0)
        self.assertEqual(len(rows), 4)

    def test_build_frame_uses_full_sidebar_even_when_narrow(self):
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
                "command": "codex",
            }
        }
        windows = [{
            "window_id": "@1",
            "session": "s",
            "win_index": "1",
            "win_name": "w",
            "win_current": True,
        }]
        by_window = {
            "1": [{
                **panes["123"],
                "state": "idle",
                "agent": "codex",
                "ts": 100,
                "space": "/tmp",
                "space_label": "tmux-lasso",
                "branch": "",
            }]
        }

        original_session_frame_data = render._session_frame_data
        try:
            render._session_frame_data = lambda _session, _now: (
                panes,
                windows,
                by_window,
            )
            lines, targets = render.build_frame(12, "s")
        finally:
            render._session_frame_data = original_session_frame_data

        self.assertEqual(targets[0], ("switch",))
        self.assertIn("─", lines[1])
        self.assertIn(("agent", "s", "1", "%123"), targets)

    def test_agent_row_uses_activity_summary(self):
        panes = {
            "123": {
                "pane_id": "%123", "session": "s", "win_index": "1",
                "win_name": "w", "pane_index": "0", "path": "/repo",
                "visible": True, "win_current": True, "title": "",
                "sidebar": False, "command": "codex",
            }
        }
        windows = [{
            "window_id": "@1", "session": "s", "win_index": "1",
            "win_name": "w", "win_current": True,
        }]
        by_window = {
            "1": [{
                **panes["123"],
                "state": "working",
                "agent": "codex",
                "summary": "migrate test suite",
                "ts": 100,
                "space": "/repo",
                "space_label": "TextCut",
                "branch": "main",
            }]
        }
        rows = render._window_rows(44, "s", 160, windows, by_window, panes)
        text = "\n".join(_plain(l) for l, _ in rows)
        self.assertIn("co: migrate test suite", text)
        self.assertIn("1:00", text)
        self.assertNotIn("main", text)

    def test_windows_in_same_space_group_under_folder(self):
        panes = {
            "1": {
                "pane_id": "%1", "session": "s", "win_index": "1",
                "win_name": "agent", "pane_index": "0", "path": "/repo",
                "visible": True, "win_current": True, "title": "",
                "sidebar": False, "command": "codex",
            },
            "2": {
                "pane_id": "%2", "session": "s", "win_index": "2",
                "win_name": "zsh", "pane_index": "0", "path": "/repo",
                "visible": False, "win_current": False, "title": "",
                "sidebar": False, "command": "zsh",
            },
        }
        windows = [
            {"window_id": "@1", "session": "s", "win_index": "1",
             "win_name": "agent", "win_current": True},
            {"window_id": "@2", "session": "s", "win_index": "2",
             "win_name": "zsh", "win_current": False},
        ]
        by_window = {
            "1": [{
                **panes["1"],
                "state": "working",
                "agent": "codex",
                "summary": "migrate test suite",
                "ts": 100,
                "space": "/repo",
                "space_label": "TextCut",
                "branch": "main",
            }]
        }
        with mock.patch.object(render, "space_of", return_value=("/repo", "TextCut", "main")):
            rows = render._window_rows(44, "s", 160, windows, by_window, panes)
        text = "\n".join(_plain(l) for l, _ in rows)
        self.assertIn("▾ TextCut", text)
        self.assertIn("1 co: migrate test suite", text)
        self.assertIn("2 zsh", text)
        self.assertNotIn("└─ codex", text)

    def test_single_agent_space_still_renders_as_folder_with_task(self):
        panes = {
            "3": {
                "pane_id": "%3", "session": "s", "win_index": "3",
                "win_name": "agent", "pane_index": "0", "path": "/repo",
                "visible": True, "win_current": True, "title": "",
                "sidebar": False, "command": "claude",
            }
        }
        windows = [{
            "window_id": "@3", "session": "s", "win_index": "3",
            "win_name": "agent", "win_current": True,
        }]
        by_window = {
            "3": [{
                **panes["3"],
                "state": "idle",
                "agent": "claude",
                "summary": 'Try "fix lint errors"',
                "ts": 100,
                "space": "/repo",
                "space_label": "tuanphung-dev",
                "branch": "main",
            }]
        }
        rows = render._window_rows(44, "s", 160, windows, by_window, panes)
        plain = [_plain(l) for l, _ in rows]
        text = "\n".join(plain)
        self.assertIn("▾ tuanphung-dev", plain[0])
        self.assertNotIn("idle", plain[0])
        self.assertIn("idle", plain[1])
        self.assertNotIn("main", text)
        self.assertIn('3 cc: Try "fix lint errors"', plain[1])


class AgentLetterTests(unittest.TestCase):
    def _multi(self):
        def agent(pid, pidx, vis):
            return {"session": "s", "win_index": "2", "win_name": "w",
                    "pane_id": pid, "pane_index": pidx, "path": "/tmp",
                    "win_current": True, "sidebar": False, "command": "claude",
                    "visible": vis, "state": "idle", "agent": "claude", "ts": 100,
                    "space": "/tmp", "space_label": "tmux-lasso", "branch": ""}
        windows = [{"window_id": "@2", "session": "s", "win_index": "2",
                    "win_name": "w", "win_current": True}]
        members = [agent("%1", "0", False), agent("%2", "1", True)]
        panes = {"1": members[0], "2": members[1]}
        return panes, windows, {"2": members}

    def test_multi_agent_window_letters_each_agent(self):
        panes, windows, by_window = self._multi()
        rows = render._window_rows(40, "s", 160, windows, by_window, panes)
        text = "\n".join(_plain(l) for l, _ in rows)
        self.assertIn("A cc", text)
        self.assertIn("B cc", text)
        self.assertGreaterEqual(text.count("idle"), 2)

    def test_single_agent_window_has_no_letter(self):
        panes, windows, by_window = self._multi()
        by_window["2"] = by_window["2"][:1]      # collapse to one agent
        rows = render._window_rows(40, "s", 160, windows, by_window, panes)
        text = "\n".join(_plain(l) for l, _ in rows)
        self.assertNotIn("A cc", text)
        self.assertIn("cc", text)

    def test_focused_agent_returns_letter_pane_and_count(self):
        panes, windows, by_window = self._multi()   # %2 is the visible (focused) pane
        with mock.patch.object(render, "_session_frame_data",
                               return_value=(panes, windows, by_window)):
            self.assertEqual(render.focused_agent("s"), ("B", "%2", 2))


if __name__ == "__main__":
    unittest.main()
