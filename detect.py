#!/usr/bin/env python3
"""Lasso: discover agent panes and detect each one's state (the "model" half).

  - query tmux for every live pane (tmux_panes)
  - for each non-sidebar pane decide which agent it runs and its state from the
    foreground command, the OSC pane title and a one-shot capture-pane of the
    visible body -> working / blocked / done / idle / unknown
  - publish per-pane {agent, state, ts} to a shared cache file so readers don't
    each scrape. The daemon is the SOLE writer (refresh_scrape); panel.py and
    switch.py are pure readers (agents_from_cache/load_scrape).

render.py turns this state into the ANSI sidebar; it imports detect, not the
other way round.
"""
import os
import re
import tempfile
import time

import tmux_api

SEP = tmux_api.SEP


def tmux_panes():
    """{pane_key: meta} for every live tmux pane."""
    vis = "#{?pane_active,#{?window_active,#{?session_attached,1,0},0},0}"
    wcur = "#{?window_active,#{?session_attached,1,0},0}"
    fmt = SEP.join([
        "#{pane_id}", "#{session_name}", "#{window_index}", "#{window_name}",
        "#{pane_index}", "#{pane_current_path}", vis, wcur, "#{pane_title}",
        "#{@lasso_pane}", "#{pane_current_command}",
    ])
    try:
        out = tmux_api.run("list-panes", "-a", "-F", fmt)
    except Exception:
        return {}
    panes = {}
    for line in out.splitlines():
        p = line.split(SEP)
        if len(p) != 11:
            continue
        pid, sname, widx, wname, pidx, path, v, wc, title, hl, cmd = p
        panes[pid.lstrip("%")] = {
            "pane_id": pid, "session": sname, "win_index": widx,
            "win_name": wname, "pane_index": pidx, "path": path,
            "visible": v == "1", "win_current": wc == "1", "title": title,
            "sidebar": hl == "1", "command": cmd,
        }
    return panes


# --- scrape detection -------------------------------------------------------
# State is read straight off each pane. The OSC title (tmux #{pane_title})
# already tells us working/idle for Claude and Codex for free, and a one-shot
# `capture-pane` of the visible body covers blocked prompts.
# Patterns are trimmed from the upstream herdr manifests
# (src/detect/manifests/*.toml).
SPINNER = re.compile(r"^[⠀-⣿]")   # braille spinner = working
CODEX_STATUS = re.compile(r"(?im)\bgpt-[\w.:-]+\b.*\bcontext\s+\d+% left\b")
WORKING_PROGRESS = re.compile(r"(?m)([■⬝•·.=])\1{3,}")
SHELLS = {"zsh", "bash", "sh", "fish", "dash", "ksh", "tcsh"}
# A pane is re-captured at most this often. paint() runs on every click as well
# as the periodic tick; throttling here decouples the (blocking) capture-pane
# subprocess from the repaint rate, so clicking between agents stays instant —
# the click repaint just reuses the cached state.
CAPTURE_TTL = 0.4
# Per-pane derived state {pane_key: {"agent","state","ts","cap_t"}} lives in one
# shared temp file, not per-process memory: every window's panel.py and the
# mobile switch popup read/write the same cache, so a working->done edge survives
# a freshly spawned panel (new window / reload) and N panels don't each scrape.
# ts = when the state last changed (age timer); cap_t = wall-clock of the last
# capture (wall, not monotonic, so it compares across processes) for the throttle.
# ponytail: last-writer-wins on the file; a lost race costs one extra capture next
# tick, never correctness.
_SCRAPE_FILE = os.path.join(tempfile.gettempdir(), "lasso-agents.json")


def load_scrape():
    """The shared per-pane state cache, {} if absent/unreadable."""
    return tmux_api.read_json(_SCRAPE_FILE)


def _save_scrape(cache):
    tmux_api.write_json(_SCRAPE_FILE, cache)


def _agentish_title(title):
    low = (title or "").lower()
    return bool(
        SPINNER.match(title or "")
        or (title or "").startswith("✳")
        or "action required" in low
        or "codex" in low
        or "claude" in low
    )


def capture_body(pane_id):
    """Visible text of a pane (no history, no escapes). '' on any failure."""
    try:
        return tmux_api.run("capture-pane", "-p", "-t", pane_id, timeout=1)
    except Exception:
        return ""


def _contains_all(text, parts):
    low = text.lower()
    return all(part.lower() in low for part in parts)


def _contains_any(text, parts):
    low = text.lower()
    return any(part.lower() in low for part in parts)


def _tail_lines(text, count=8):
    lines = text.splitlines()
    return "\n".join(lines[-count:])


def _looks_working(title, body, allow_body=True):
    t = title or ""
    if SPINNER.match(t):
        return True
    if not allow_body:
        return False
    low = _tail_lines(body).lower()
    if "esc interrupt" in low or "esc to interrupt" in low:
        return True
    if "ctrl+c to interrupt" in low or "press esc to interrupt" in low:
        return True
    if WORKING_PROGRESS.search(_tail_lines(body)):
        return True
    return False


def detect_agent_kind(command, title, body):
    """Which agent (if any) a pane is running. Command name first, then a body
    signature so a pane that shows up as plain `node`/`python` still resolves."""
    c = (command or "").lstrip("-").lower()
    if "claude" in c:
        return "claude"
    if "codex" in c:
        return "codex"
    low = body.lower()
    title_low = (title or "").lower()
    if "claude" in title_low:
        return "claude"
    if "codex" in title_low:
        return "codex"
    if "action required" in title_low:
        return "codex"
    if CODEX_STATUS.search(body) or (
        "context " in low and "% left" in low and "esc to interrupt" in low
    ):
        return "codex"
    if "codex" in low and (
        "esc to" in low or "allow command?" in low or "enter to submit" in low
    ):
        return "codex"
    if "bypass permissions" in low or "claude code" in low:
        return "claude"
    return None


def _state_claude(title, body):
    t = title or ""
    if _looks_working(t, body, allow_body=False):
        return "working"
    low = body.lower()
    if _contains_all(low, ["run a dynamic workflow?", "esc to cancel"]):
        return "blocked"
    if _contains_all(low, ["do you want to proceed?", "esc to cancel"]):
        return "blocked"
    if _contains_all(low, ["enter to select", "esc to cancel"]) and _contains_any(
        low,
        [
            "tab/arrow keys to navigate",
            "arrow keys to navigate",
            "arrows to navigate",
            "↑/↓ to navigate",
            "↑↓ to navigate",
        ],
    ):
        return "blocked"
    if _contains_all(low, ["do you want to allow this connection?"]):
        return "blocked"
    if _contains_any(low, ["waiting for permission", "tab to amend", "ctrl+e to explain"]):
        return "blocked"
    if _contains_all(low, ["showing detailed transcript"]) and _contains_any(
        low, ["ctrl+o", "ctrl+e", "↑↓ scroll", "? for shortcuts"]
    ):
        return "unknown"
    if _contains_all(low, ["select model", "enter to set as default", "esc to cancel"]):
        return "unknown"
    if t.startswith("✳"):  # ✳ idle marker
        return "idle"
    if re.search(r"(?m)^\s*❯", body):  # ❯ prompt box
        return "idle"
    return "unknown"


def _state_codex(title, body):
    t = title or ""
    if "Action Required" in t:
        return "blocked"
    if _looks_working(t, body, allow_body=False):
        return "working"
    low = body.lower()
    if _contains_all(low, ["↑/↓ to scroll", "pgup/pgdn to", "home/end to jump", "q to quit"]) and _contains_any(
        low, ["esc to edit prev", "esc/← to edit prev"]
    ):
        return "unknown"
    if any(s in low for s in (
        "press enter to confirm or esc to cancel",
        "enter to submit answer",
        "enter to submit all",
        "allow command?",
        "[y/n]",
        "yes (y)",
    )):
        return "blocked"
    if any(s in low for s in ("do you want to", "would you like to")) and (
        "yes" in low or "❯" in body
    ):
        return "blocked"
    if t.strip():
        return "idle"
    return "unknown"


_STATE_FNS = {
    "claude": _state_claude,
    "codex": _state_codex,
}


def _apply_state_transition(state, cached_state, meta):
    if state == "idle" and cached_state == "working":
        return "done"
    if state == "idle" and cached_state == "done":
        if meta.get("visible"):
            return "idle"
        return "done"
    return state


def _merge_cached(kind, state, cached, meta, now):
    """Reconcile a freshly-detected (kind, state) against the cached entry and
    return (state, ts): apply the working->done edge, carry the last real state
    forward over a transient 'unknown', and keep the original ts while the state
    is unchanged so the age timer keeps counting (a new/changed state resets it)."""
    same = bool(cached) and cached.get("agent") == kind
    if same:
        state = _apply_state_transition(state, cached.get("state"), meta)
    if state == "unknown" and same and cached.get("state") not in (None, "unknown"):
        return cached["state"], cached["ts"]
    if same and cached.get("state") == state:
        return state, cached["ts"]
    return state, now


def refresh_scrape(panes, now):
    """Capture each candidate pane, detect its agent + state, apply the
    working->done transition, and write the shared cache. The daemon is the SOLE
    caller -- exactly one writer -- so renderers just read the result via
    agents_from_cache and never run capture-pane on the paint path."""
    wall = time.time()
    cache = load_scrape()
    for key, meta in panes.items():
        if meta["sidebar"]:
            continue
        cmd = (meta.get("command") or "").lstrip("-").lower()
        title = meta.get("title") or ""
        cached = cache.get(key)
        # Plain shell with no agent-ish title: not worth capturing.
        if cmd in SHELLS and not cached and not _agentish_title(title):
            continue
        if cached and wall - cached["cap_t"] < CAPTURE_TTL:
            continue  # within the throttle window: keep the cached entry as-is
        body = capture_body(meta["pane_id"])
        kind = detect_agent_kind(cmd, title, body)
        state = _STATE_FNS[kind](title, body) if kind else None
        state, ts = _merge_cached(kind, state, cached, meta, now)
        cache[key] = {"agent": kind, "state": state, "ts": ts, "cap_t": wall}
    # forget caches for panes that no longer exist, then publish for readers
    for dead in [k for k in cache if k not in panes]:
        cache.pop(dead, None)
    _save_scrape(cache)


def agents_from_cache(panes, now):
    """The sidebar's agent list, built from the shared cache the daemon writes
    -- a pure read, no capture-pane. Joins each cached (agent, state, ts) with
    the pane's live meta; panes with no detected agent are skipped."""
    cache = load_scrape()
    agents = []
    for key, meta in panes.items():
        if meta["sidebar"]:
            continue
        c = cache.get(key)
        if not c:
            continue
        kind, state = c.get("agent"), c.get("state")
        if not kind or state in (None, "unknown"):
            continue
        agents.append({**meta, "state": state, "agent": kind, "ts": c.get("ts", now)})
    return agents


def scrape_agents(panes, now):
    """One-shot: refresh the cache, then read it back. Used by tests; in
    production the two halves are split across the daemon (refresh_scrape) and
    the panels (agents_from_cache)."""
    refresh_scrape(panes, now)
    return agents_from_cache(panes, now)
