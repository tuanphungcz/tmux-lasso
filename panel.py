#!/usr/bin/env python3
"""tmux-lasso sidebar: the live, clickable sidebar pane -- a dumb renderer.

Runs inside a narrow tmux pane (one per window). Every refresh it repaints the
agent list from render.build_frame(); a left click on an agent row switches the
attached client to that pane.

Lifecycle (which windows get a sidebar, dedupe, resize, desktop/mobile) is
owned by the single reconciler in daemon.py
-- this process only draws and handles clicks, so there's no per-pane
self-management to race over. If the reconciler kills this pane (window closed,
duplicate, tmux-lasso off), the process simply dies with it.
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render  # noqa: E402
import tmux_api  # noqa: E402
import tui  # noqa: E402
import usage  # noqa: E402

PANE = os.environ.get("TMUX_PANE", "")
REFRESH = 0.5

HERE = os.path.dirname(os.path.abspath(__file__))
# Files whose edits should hot-reload the live sidebar -- just what this process
# draws. Lifecycle/daemon code lives in daemon.py + toggle.sh, not here.
WATCH = [os.path.join(HERE, f) for f in ("panel.py", "render.py", "detect.py", "usage.py")]
TOGGLE = os.path.join(HERE, "toggle.sh")
SWITCH = os.path.join(HERE, "switch.py")


def watch_snapshot():
    s = {}
    for p in WATCH:
        try:
            s[p] = os.path.getmtime(p)
        except OSError:
            s[p] = None
    return s


def watched_py_parses():
    """True if every watched Python file currently parses. A file caught
    mid-save (a half-written edit) won't -- re-exec'ing into it would crash the
    panel and close the pane, so we hold off until it parses cleanly."""
    for p in WATCH:
        if not p.endswith(".py"):
            continue
        try:
            with open(p) as f:
                compile(f.read(), p, "exec")
        except SyntaxError:
            return False
        except OSError:
            pass
    return True


def our_session():
    return tmux_api.display("#{session_name}", target=PANE) if PANE else ""


def switch_to(pane_id):
    # Target the pane id directly: it is stable and unambiguous, so it survives
    # renumber-windows index shifts that a stale session:win_index would not.
    client = tmux_api.client_for_session(our_session())
    if client:
        tmux_api.run("switch-client", "-c", client, "-t", pane_id)
    else:
        tmux_api.run("switch-client", "-t", pane_id)
    tmux_api.run("select-pane", "-t", pane_id)


def open_switch():
    """Open the window switcher popup (same one the phone status bar uses).
    Fire-and-forget so the panel loop keeps repainting behind the popup."""
    session = our_session()
    client = tmux_api.client_for_session(session)
    cmd = ["tmux", "display-popup", "-E", "-B", "-w", "100%", "-h", "100%"]
    if client:
        cmd += ["-c", client]
    # Pass the session explicitly: inside a popup #{client_session} is unreliable.
    cmd += [SWITCH, session]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def sync_sidebar_width():
    """Copy this sidebar pane's current width to every tmux-lasso sidebar."""
    try:
        subprocess.Popen(
            [TOGGLE, "sync-width", PANE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def main():
    if not PANE:
        sys.stderr.write("tmux-lasso: not inside tmux\n")
        return

    def hot_reload(sources):
        """Re-exec this pane when a watched drawing file changes, so edits to
        panel.py/render.py show up live without a toggle."""
        now = watch_snapshot()
        if now == sources:
            return sources
        if not watched_py_parses():  # mid-save: wait for a clean parse next tick
            return sources
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])

    last_rows = []

    def build_rows(width, height):
        try:
            lines, targets = render.build_frame(width, our_session())
            rows = list(zip(lines, targets))
            rows = render.with_usage_footer(
                rows, width, usage.snapshot(), usage.age(), height)
            last_rows[:] = rows
            return rows
        except Exception:
            # A bad reload (e.g. a runtime error in freshly-edited render code)
            # must not take the pane down -- show the last good frame instead.
            return list(last_rows) or [(" tmux-lasso: reloading…", None)]

    def handle_action(target, x, _y):
        if target[0] == "buttons":
            target = next((sub for x0, x1, sub in target[1] if x0 <= x <= x1), None)
        if not target:
            return False
        if target[0] == "agent":
            switch_to(target[3])
        elif target[0] == "switch":
            open_switch()
        elif target[0] == "sync":
            sync_sidebar_width()
        elif target[0] == "usage":
            usage.force_refresh()
        return False

    sources = watch_snapshot()

    def on_tick():
        nonlocal sources
        sources = hot_reload(sources)

    tui.run_mouse_ui(
        build_rows,
        handle_action,
        refresh_interval=REFRESH,
        default_cols=34,
        default_lines=40,
        on_tick=on_tick,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"tmux-lasso panel error: {e}\n")
        time.sleep(3)
