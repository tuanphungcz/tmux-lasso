#!/usr/bin/env python3
"""Lasso reconciler: the single writer, one process per tmux server.

Replaces the old scheme where every sidebar pane self-managed (dedupe, exit,
leader-elect, re-register hooks) while tmux hooks imperatively added/removed
panes -- the source of the races, duplicates and vanishings. Here ONE loop
owns every tmux mutation: each tick it asks toggle.sh to reconcile (exactly one
sidebar per desktop window, none on mobile, at the current width; self-healing
-- a gap or a duplicate is fixed on the next tick). Panels are dumb renderers.

A flock makes it a singleton, so the two start points (tmux launch, toggle on)
can't both run. If it ever dies the sidebars keep rendering; reconciliation
just pauses until the next `toggle.sh enable` restarts it.
ponytail: shells out to `toggle.sh reconcile` each tick to reuse the debugged
shell helpers; port that pass to Python if the per-tick sh+tmux spawn ever bites.
"""
import fcntl
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import detect  # noqa: E402
import tmux_api  # noqa: E402
import usage  # noqa: E402

TOGGLE = os.path.join(HERE, "toggle.sh")
TICK = 0.5
FORCE_MIN_GAP = 5.0   # min seconds between usage fetches, even on a forced tap


def _lock():
    """flock a per-server file; return the held fd, or None if another daemon
    already owns it (this start is then a harmless no-op). flock releases on
    process death, so there are no stale locks to reap."""
    server = tmux_api.run("display-message", "-p", "#{pid}") or "default"
    path = os.path.join(tempfile.gettempdir(), f"lasso-daemon-{server}.lock")
    fd = open(path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        return None
    return fd


def _on():
    return tmux_api.run("show", "-gv", "@lasso_on") == "on"


def _maybe_fetch_usage(last):
    """Spawn the usage fetch as a DETACHED subprocess (so a wedged network call
    never stalls this loop) at most every TTL, or sooner when the footer's sync
    button set @lasso_usage_force -- but never more often than FORCE_MIN_GAP.
    Returns the new 'last fetched' monotonic stamp."""
    now = time.monotonic()
    if now - last < FORCE_MIN_GAP:
        return last
    forced = tmux_api.run("show", "-gv", usage.FORCE_OPT) == "1"
    if not forced and now - last < usage.TTL:
        return last
    if forced:
        tmux_api.run("set", "-g", usage.FORCE_OPT, "0")
    try:
        subprocess.Popen(
            [sys.executable, os.path.join(HERE, "usage.py"), "fetch-once"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    return now


def main():
    lock = _lock()
    if lock is None:
        return                       # another daemon owns this server
    last_usage = 0.0
    while _on():
        try:
            subprocess.run([TOGGLE, "reconcile"], timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            detect.refresh_scrape(detect.tmux_panes(), int(time.time()))
        except Exception:
            pass                     # scraping is the daemon's job; never crash on it
        last_usage = _maybe_fetch_usage(last_usage)
        time.sleep(TICK)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"Lasso daemon error: {e}\n")
