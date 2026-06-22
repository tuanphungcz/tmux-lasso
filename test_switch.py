#!/usr/bin/env python3
import re
import unittest
from unittest import mock

import render
import switch
import usage


def _plain(s):
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def _build(session="s", pending=False, active=("3", "dev"), focused=("", "", 1)):
    # focused = render.focused_agent()'s (letter, pane_id, agent_count)
    with mock.patch.object(render, "window_rows", return_value=[]), \
         mock.patch.object(usage, "snapshot", return_value={}), \
         mock.patch.object(render, "focused_agent", return_value=focused), \
         mock.patch.object(switch, "active_window", return_value=active):
        return switch.build_rows(40, 30, session, {"pending": pending})


def _actions(rows):
    return [a for _, a in rows if a]


class SwitchButtonTests(unittest.TestCase):
    def test_new_tab_button_is_two_full_rows_with_one_action(self):
        rows = switch.new_tab_button(28, "ws1")
        self.assertEqual(len(rows), 2)
        self.assertIn("+ new tab", _plain(rows[0][0]))
        self.assertEqual(len(_plain(rows[0][0])), 28)    # full-width button bar
        self.assertEqual(rows[0][1], ("new-window", "ws1"))
        self.assertEqual(rows[1][1], ("new-window", "ws1"))  # tall tap target


class SwitchCloseTests(unittest.TestCase):
    def test_close_is_a_full_width_two_row_button(self):
        rows = _build()
        close = [line for line, a in rows if a == ("close",)]
        self.assertEqual(len(close), 2)                  # 2-row tap target
        self.assertIn("✕ close", _plain(close[0]))
        self.assertEqual(len(_plain(close[0])), 40)      # full-width button bar


class SwitchDeleteTests(unittest.TestCase):
    def test_list_offers_new_tab_and_delete_current_tab(self):
        kinds = {a[0] for a in _actions(_build())}
        self.assertIn("new-window", kinds)
        self.assertIn("delete-ask", kinds)
        self.assertNotIn("delete-confirm", kinds)        # confirm only once armed

    def test_delete_button_names_the_current_tab(self):
        text = "\n".join(_plain(line) for line, _ in _build(active=("3", "dev")))
        self.assertIn("delete tab 3 (dev)", text)

    def test_tapping_delete_reveals_a_confirm_for_that_tab(self):
        rows = _build(pending=True, active=("3", "dev"))
        acts = _actions(rows)
        self.assertTrue(any(a == ("delete-confirm", "3", "s") for a in acts))
        self.assertTrue(any(a == ("delete-cancel",) for a in acts))
        self.assertFalse(any(a[0] == "delete-ask" for a in acts))  # replaced by confirm

    def test_no_delete_affordance_without_a_current_window(self):
        kinds = {a[0] for a in _actions(_build(active=(None, None)))}
        self.assertNotIn("delete-ask", kinds)
        self.assertNotIn("delete-confirm", kinds)


class SwitchKillPaneTests(unittest.TestCase):
    def test_two_agents_label_the_kill_with_tab_and_letter(self):
        text = "\n".join(_plain(l) for l, _ in
                         _build(active=("2", "dev"), focused=("C", "%7", 2)))
        self.assertIn("kill 2C", text)

    def test_two_agents_confirm_targets_the_pane_not_the_window(self):
        acts = _actions(_build(pending=True, active=("2", "dev"), focused=("C", "%7", 2)))
        self.assertIn(("kill-pane", "%7"), acts)
        self.assertFalse(any(a[0] == "delete-confirm" for a in acts))

    def test_single_agent_still_deletes_the_whole_tab(self):
        acts = _actions(_build(pending=True, active=("3", "dev"), focused=("", "%7", 1)))
        self.assertIn(("delete-confirm", "3", "s"), acts)
        self.assertFalse(any(a[0] == "kill-pane" for a in acts))

    def test_dispatch_kill_pane_kills_and_stays_open(self):
        st = {"pending": True}
        with mock.patch("switch.tmux_api.run") as run:
            close = switch.dispatch(("kill-pane", "%7"), "s", st)
        run.assert_any_call("kill-pane", "-t", "%7")
        self.assertFalse(st["pending"])
        self.assertFalse(close)                          # stay open after a kill


class SwitchSpacesTests(unittest.TestCase):
    def test_lists_sessions_and_marks_current(self):
        with mock.patch("switch.tmux_api.run",
                        return_value=f"work{switch.SEP}3\npersonal{switch.SEP}1"):
            rows = switch.session_rows(40, "work")
        acts = _actions(rows)
        self.assertIn(("session", "work"), acts)
        self.assertIn(("session", "personal"), acts)
        text = "\n".join(_plain(line) for line, _ in rows)
        self.assertIn("3 tabs", text)
        self.assertIn("1 tab\n", text + "\n")            # singular, not "1 tabs"

    def test_hidden_with_a_single_session(self):
        with mock.patch("switch.tmux_api.run", return_value=f"only{switch.SEP}2"):
            self.assertEqual(switch.session_rows(40, "only"), [])

    def test_dispatch_session_switches_and_closes(self):
        with mock.patch("switch.tmux_api.run") as run:
            close = switch.dispatch(("session", "personal"), "work", {})
        run.assert_any_call("switch-client", "-t", "personal")
        self.assertTrue(close)


class SwitchFattenTests(unittest.TestCase):
    def test_tappable_rows_get_a_blank_twin_inert_rows_dont(self):
        rows = switch._fatten([
            ("header", None),
            ("tab 1", ("agent", "s", "1", "%2")),
            ("", None),
        ], 40)
        self.assertEqual(rows, [
            ("header", None),
            ("tab 1", ("agent", "s", "1", "%2")),
            ("", ("agent", "s", "1", "%2")),     # fat tap target, same action
            ("", None),
        ])

    def test_window_header_stays_tight_against_its_agents(self):
        # a header (no ├/└) followed by a tree child gets NO blank twin, else a
        # gap opens between the window and its first agent; agents still fatten.
        rows = switch._fatten([
            ("1 lasso", ("agent", "s", "1", "%1")),     # window header
            ("├─ claude", ("agent", "s", "1", "%2")),
            ("└─ claude", ("agent", "s", "1", "%3")),
        ], 40)
        self.assertEqual(rows, [
            ("1 lasso", ("agent", "s", "1", "%1")),     # tight to its child
            ("├─ claude", ("agent", "s", "1", "%2")),
            ("", ("agent", "s", "1", "%2")),
            ("└─ claude", ("agent", "s", "1", "%3")),
            ("", ("agent", "s", "1", "%3")),
        ])

    def test_highlighted_agent_twin_repaints_the_focus_band(self):
        # a current-window row carries HILITE; its twin must too, else a dark
        # stripe breaks the selected window's band between its agents.
        hl = render.HILITE
        rows = switch._fatten(
            [(f"{hl}├─ claude{switch.RESET}", ("agent", "s", "1", "%2"))], 12)
        self.assertEqual(rows[1][0], f"{hl}{' ' * 12}{switch.RESET}")

    def test_switcher_doubles_each_session_row(self):
        with mock.patch("switch.tmux_api.run",
                        return_value=f"work{switch.SEP}3\npersonal{switch.SEP}1"), \
             mock.patch.object(render, "window_rows", return_value=[]), \
             mock.patch.object(usage, "snapshot", return_value={}), \
             mock.patch.object(switch, "active_window", return_value=(None, None)):
            rows = switch.build_rows(40, 30, "work", {"pending": False})
        self.assertEqual(sum(1 for _, a in rows if a == ("session", "personal")), 2)


class SwitchDispatchTests(unittest.TestCase):
    def test_delete_arm_cancel_flow(self):
        st = {"pending": False}
        self.assertFalse(switch.dispatch(("delete-ask",), "s", st))
        self.assertTrue(st["pending"])
        self.assertFalse(switch.dispatch(("delete-cancel",), "s", st))
        self.assertFalse(st["pending"])

    def test_confirm_kills_window_and_clears_pending(self):
        st = {"pending": True}
        with mock.patch.object(render, "tmux_windows", return_value=[{}, {}]), \
             mock.patch("switch.tmux_api.run") as run:
            close = switch.dispatch(("delete-confirm", "3", "s"), "s", st)
        run.assert_any_call("kill-window", "-t", "s:3")
        self.assertFalse(st["pending"])
        self.assertFalse(close)                          # stay open after a delete

    def test_kill_refuses_the_last_window(self):
        with mock.patch.object(render, "tmux_windows", return_value=[{}]), \
             mock.patch("switch.tmux_api.run") as run:
            switch._kill_window("s", "1")
        self.assertFalse(any(c.args[:1] == ("kill-window",) for c in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
