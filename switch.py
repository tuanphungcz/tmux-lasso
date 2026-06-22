#!/usr/bin/env python3
"""Lasso switch: a fullscreen, tap-driven switcher for phone/mobile tmux.

Opened from the status-bar "switch" button via `display-popup -E`. Laid out like
the desktop lasso panel — the same window tree and usage footer — plus a couple
of switcher-only buttons:

  + new tab        -> open an empty tab (like Cmd+T)
  ✕ delete tab N   -> delete the current (highlighted) tab; tapping it reveals a
                      big confirm button right below, so a stray tap deletes
                      nothing — it takes the confirm too.

Tap a row (left click / touch) to act; jumping to a tab closes the popup, which
fills the whole screen because mobile mode runs without the sidebar.

Run `switch.py --dump [width] [pending]` to print the rows as plain text.
"""
import os
import re
import shlex
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOGGLE = os.path.join(HERE, "toggle.sh")

sys.path.insert(0, HERE)
import render  # noqa: E402
import tmux_api  # noqa: E402
import tui  # noqa: E402
import usage  # noqa: E402

SEP = render.SEP
RESET = render.RESET
DIM = render.DIM
BOLD = render.BOLD
RULE = render.RULE_COL
NEW_BG = render.bg(38, 64, 46)   # green "new tab" / confirm button
NEW_FG = render.fg(176, 224, 182)
DEL_BG = render.bg(64, 40, 42)   # red "delete" button
DEL_FG = render.fg(228, 150, 150)
NO_BG = render.bg(44, 48, 56)    # neutral "keep it" button
NO_FG = render.fg(170, 176, 184)


def button_rows(width, label, action, fg=NEW_FG, bg=NEW_BG):
    """A full-width, two-row tappable button — an easy target on a phone; the
    whole block carries one action."""
    fill = render.truncate(f"  {label}", width).ljust(width)
    return [(f"{bg}{fg}{BOLD}{fill}{RESET}", action),
            (f"{bg}{' ' * width}{RESET}", action)]


def new_tab_button(width, session):
    """Prominent 'new tab' button — opens an empty tab, like Cmd+T."""
    return button_rows(width, "+ new tab", ("new-window", session))


def _has_connector(line):
    """True for a nested-agent row — it carries a ├/└ tree connector. Window
    headers, rules and spacers don't."""
    return "├" in line or "└" in line


def _fatten(rows, width):
    """Give every tappable LEAF row a blank twin below it, so each agent/tab is a
    two-row, thumb-sized target on a phone — the same trick button_rows uses.
    Inert rows (rules, spacers) stay single; so does a window-header row that has
    agents nested under it — its twin would open a gap between the header and its
    first └/├ agent. The agents below still fatten.
    The twin repaints the row's highlight, so a current window's focus band stays
    unbroken across its agents instead of showing a dark stripe between them."""
    out = []
    for i, (line, action) in enumerate(rows):
        out.append((line, action))
        if action is None:
            continue
        nxt = rows[i + 1][0] if i + 1 < len(rows) else ""
        if not _has_connector(line) and _has_connector(nxt):
            continue  # window header → keep it tight against its first agent
        pad = f"{render.HILITE}{' ' * width}{RESET}" if render.HILITE in line else ""
        out.append((pad, action))
    return out


def session_rows(width, current):
    """A 'spaces' section: one tappable row per tmux session so the switcher
    hops between sessions, not just the tabs of one. Hidden when there's only
    one session — nothing to switch to."""
    out = tmux_api.run("list-sessions", "-F",
                       f"#{{session_name}}{SEP}#{{session_windows}}")
    sessions = [ln.partition(SEP)[::2] for ln in out.splitlines() if ln]
    if len(sessions) <= 1:
        return []
    rows = [(render.compose(width, [render.seg("  spaces", DIM)]), None)]
    for name, nwin in sessions:
        cur = name == current
        bg = render.HILITE if cur else ""
        col0 = ((f"{render.ACCENT}{render.BAR}{RESET}{bg}", 1) if cur
                else render.seg(" ", "", bg))
        cnt = f"{nwin or 0} tab" + ("" if nwin == "1" else "s")
        right = [render.seg(cnt, DIM, bg)]
        budget = max(1, width - 2 - sum(v for _, v in right) - 1)
        left = [col0, render.seg(" ", "", bg),
                render.seg(render.truncate(name, budget), (BOLD if cur else ""), bg)]
        rows.append((render.compose(width, left, right, row_bg=bg), ("session", name)))
        # 2-row block: the current space's highlight spans both rows (a fat target)
        rows.append((f"{bg}{' ' * width}{RESET}" if bg else "", ("session", name)))
    rows.append((f"{RULE}{'─' * width}{RESET}", None))
    return rows


def cur_session():
    return tmux_api.display("#{client_session}") or tmux_api.display(
        "#{session_name}"
    )


def active_window(session):
    """(index, name) of the session's current (highlighted) window, or (None,
    None) — this is the tab the delete button targets."""
    out = tmux_api.display(f"#{{window_index}}{SEP}#{{window_name}}", target=session)
    parts = (out or "").split(SEP)
    if len(parts) == 2 and parts[0]:
        return parts[0], parts[1]
    return None, None


def _delete_block(width, session, pending):
    """The delete affordance for the current tab: a red button, plus — once
    tapped (pending) — a labelled confirm with a keep-it escape. Targets the
    whole tab (kill-window) normally, but just the focused pane (kill-pane) when
    the tab holds two+ agents — labelled tab+letter, e.g. "kill 2C" — so you can
    shut one of two agents and keep the tab. Focus the agent you want gone, reopen
    the switcher, kill it."""
    idx, name = active_window(session)
    if not idx:
        return []
    tab = f"tab {idx}" + (f" ({name})" if name else "")
    letter, pane, agents = render.focused_agent(session)
    if agents >= 2 and pane:
        verb, what, confirm = "kill", f"{idx}{letter}", ("kill-pane", pane)
    else:
        verb, what, confirm = "delete", tab, ("delete-confirm", idx, session)
    if not pending:
        return button_rows(width, f"✕ {verb} {what}", ("delete-ask",), DEL_FG, DEL_BG)
    rows = [(render.compose(width, [render.seg(f"   {verb} {what}?", DIM)]), None)]
    rows += button_rows(width, f"✓ yes, {verb} it", confirm)
    rows += button_rows(width, "✗ no, keep it", ("delete-cancel",), NO_FG, NO_BG)
    return rows


def build_rows(width, height, session, state=None):
    """Return [(rendered_line, action_or_None)] for the whole switcher: header,
    the desktop window tree, the new-tab and delete buttons, then the desktop
    usage footer pinned to the bottom."""
    width = max(16, width)
    state = state if state is not None else {"pending": False}
    rows = [(render.compose(width, [render.seg(" switch", DIM)]), None)]  # title
    rows += button_rows(width, "✕ close", ("close",), NO_FG, NO_BG)       # full-width 2-row tap target
    rows += [(f"{RULE}{'─' * width}{RESET}", None)]
    rows.extend(session_rows(width, session))   # spaces: already a 2-row block each
    # the same window tree the desktop sidebar draws; strip its inter-window spacer
    # rows so _fatten's thumb-blank is the ONLY gap between tabs (was a double gap)
    tabs = [r for r in render.window_rows(width, session) if r != ("", None)]
    rows.extend(_fatten(tabs, width))  # targets: ("agent", ...)
    rows += [("", None), ("", None)]   # gap above new-tab, clear of the window list
    rows += new_tab_button(width, session)
    delete = _delete_block(width, session, state.get("pending"))
    if delete:
        rows += [("", None), ("", None)]   # dead gap so a stray tap near new-tab can't hit delete
        rows += delete
    return render.with_usage_footer(rows, width, usage.snapshot(), usage.age(), height)


def _kill_window(session, idx):
    # Never let the switcher kill the session's last window (that ends the
    # session and the popup); there must be somewhere to land.
    if len(render.tmux_windows(session)) <= 1:
        return
    tmux_api.run("kill-window", "-t", f"{session}:{idx}")


def dispatch(action, session, state):
    """Run the tmux action / update switcher state. Return True to close popup."""
    kind = action[0]
    if kind == "delete-ask":                # reveal the confirm (safety step 1)
        state["pending"] = True
        return False
    if kind == "delete-cancel":             # back out before confirming
        state["pending"] = False
        return False
    if kind == "delete-confirm":            # the big confirm (safety step 2)
        _kill_window(action[2], action[1])
        state["pending"] = False
        return False
    if kind == "kill-pane":                 # kill ONE agent in a multi-agent tab
        tmux_api.run("kill-pane", "-t", action[1])
        state["pending"] = False
        return False
    if kind == "new-window":
        win = tmux_api.run("new-window", "-t", action[1], "-P", "-F", "#{window_id}")
        if win:
            tmux_api.run("run-shell", "-b", f"{shlex.quote(TOGGLE)} add-window {shlex.quote(win)}")
        return True
    if kind == "agent":
        # ("agent", session, win_index, pane_id) from render.window_rows: jump
        # to that pane and close the popup.
        tmux_api.run("switch-client", "-t", action[1])
        tmux_api.run("select-window", "-t", action[3])
        tmux_api.run("select-pane", "-t", action[3])
        return True
    if kind == "session":    # a 'spaces' row: hop to that session, close popup
        tmux_api.run("switch-client", "-t", action[1])
        return True
    if kind == "usage":      # tapping the usage header re-syncs, popup stays open
        usage.force_refresh()
        return False
    if kind == "close":
        return True
    return False


def main(session=None):
    session = session or cur_session()
    state = {"pending": False}

    def key_should_close(buf):
        return "\x1b[<" not in buf and (
            buf in ("q", "\x1b", "\x1b\x1b") or buf.strip() == "q"
        )

    tui.run_mouse_ui(
        lambda width, height: build_rows(width, height, session, state),
        lambda action, _x, _y: dispatch(action, session, state),
        refresh_interval=1.0,
        default_cols=40,
        default_lines=30,
        key_should_close=key_should_close,
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--dump":
        w = int(sys.argv[2]) if len(sys.argv) > 2 else 40
        st = {"pending": len(sys.argv) > 3 and sys.argv[3] == "pending"}
        for line, action in build_rows(w, 30, cur_session(), st):
            plain = re.sub(r"\x1b\[[0-9;]*m", "", line)
            tag = action[0] if action else "-"
            print(f"|{plain.ljust(w)}|  [{tag}]")
    else:
        try:
            main(sys.argv[1] if len(sys.argv) > 1 else None)
        except Exception as e:
            sys.stderr.write(f"Lasso switch error: {e}\n")
            time.sleep(3)
