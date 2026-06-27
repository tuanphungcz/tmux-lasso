#!/usr/bin/env python3
"""tmux-lasso: discover agent panes and detect each one's state (the "model" half).

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
SUMMARY_MAX = 30
SUMMARY_WORDS = 4
SUMMARY_VERSION = 10


def tmux_panes():
    """{pane_key: meta} for every live tmux pane."""
    vis = "#{?pane_active,#{?window_active,#{?session_attached,1,0},0},0}"
    wcur = "#{?window_active,#{?session_attached,1,0},0}"
    fmt = SEP.join([
        "#{pane_id}", "#{session_name}", "#{window_index}", "#{window_name}",
        "#{pane_index}", "#{pane_current_path}", vis, wcur, "#{pane_title}",
        "#{@tmux_lasso_pane}", "#{pane_current_command}",
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
# pi (pi-coding-agent): static "π - <cwd>" title, so working is read from the
# body's spinner line (e.g. "⠴ Working..."). The capture group is the spinner
# glyph -- pi can leave a FROZEN "Working..." on screen after it finishes, so we
# only trust it as working while the glyph actually animates between ticks.
PI_WORKING = re.compile(r"([⠀-⣿])\s*[Ww]orking\.\.\.")


def _pi_spinner(body):
    """The braille glyph in pi's '⠴ Working...' line, or '' if none."""
    m = PI_WORKING.search(body or "")
    return m.group(1) if m else ""
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
_SCRAPE_FILE = os.path.join(tempfile.gettempdir(), "tmux-lasso-agents.json")


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
        or (title or "").startswith("π")
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
    # When the foreground command is a plain shell, the OSC pane title is stale
    # (set by a previous agent that has since exited). Skip title-based detection
    # and fall through to body-based checks which reflect what's actually visible.
    if c not in SHELLS:
        if "claude" in title_low:
            return "claude"
        if "codex" in title_low:
            return "codex"
        if (title or "").startswith("π"):     # pi-coding-agent: "π - <cwd>" title
            return "pi"
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
    if "pi-coding-agent" in low:          # body fallback if the title is hidden
        return "pi"
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


def _state_pi(title, body):
    """pi-coding-agent. Its title stays "π - <cwd>", so working is read from the
    body's "⠴ Working..." spinner line; a "[y/N]" readline prompt is a permission
    block; otherwise it's sitting at the input box (idle)."""
    low = body.lower()
    if PI_WORKING.search(body) or "esc to interrupt" in low:
        return "working"
    if "[y/n]" in low:                 # rl.question("... [y/N] ") permission prompt
        return "blocked"
    return "idle"


_STATE_FNS = {
    "claude": _state_claude,
    "codex": _state_codex,
    "pi": _state_pi,
}


def _clean_summary_text(text):
    text = re.sub(r"\s+", " ", (text or "").strip())
    text = text.strip(" \t│┃┆┊╭╮╰╯┌┐└┘─━═")
    text = re.sub(r"^[>›❯$#%]\s*", "", text)
    text = re.sub(r"^[•·●○◦*\-]\s*", "", text)
    text = re.sub(r"^[⠀-⣿✳]\s*", "", text)
    text = re.sub(r"\s*\((?:optional|volitelné)\)\s*$", "", text, flags=re.I)
    text = text.strip()
    if len(text) > SUMMARY_MAX:
        cut = text[:SUMMARY_MAX - 1].rstrip()
        word_cut = re.sub(r"\s+\S*$", "", cut).rstrip()
        if len(word_cut) >= 12:
            cut = word_cut
        text = cut + "…"
    return text


def _summary_candidate(text):
    text = _clean_summary_text(text)
    if len(text) < 4:
        return ""
    low = text.lower()
    noisy = (
        "esc to interrupt", "press esc", "ctrl+c", "ctrl+", "context ",
        "% left", "tokens", "claude code", "bypass permissions",
        "enter to select", "esc to cancel", "tab/arrow", "arrow keys",
        "↑/↓", "pgup/pgdn", "home/end", "working...", "working (",
        "gpt-", "0.0 tps", "weekly limit", "how is claude doing this",
    )
    if any(s in low for s in noisy):
        return ""
    if re.search(r"https?://|www\.", low):
        return ""
    if re.search(r"\b(opus|sonnet|haiku)\b.*\bcontext\b", low):
        return ""
    if re.search(r"\bleft\s+\(\d+%\)", low):
        return ""
    if "│" in text and re.search(r"\b\d+(?:\.\d+)?s\b", low):
        return ""
    if low in {"codex", "claude", "pi", "ready", "action required", "yes", "no"}:
        return ""
    if re.match(r"^(~|/).*(\(\w+\)|/)", text):
        return ""
    if not re.search(r"[A-Za-z0-9]", text):
        return ""
    return text


def _prompt_title(text):
    """Stable title from the user's prompt: first few visible words, no guessing."""
    text = _summary_candidate(text)
    if not text:
        return ""
    words = re.findall(r"[A-Za-z0-9À-ž][A-Za-z0-9À-ž._+-]*", text)
    return _clean_summary_text(" ".join(words[:SUMMARY_WORDS])) if words else text


def activity_summary(kind, title, body):
    """Best-effort short task title for the pane.

    This intentionally stays heuristic and local: the daemon already captures
    the visible pane body, so we extract the first prompt-like line and cache it.
    Renderers never call capture-pane just to make labels prettier.
    """
    prompt = re.compile(r"^\s*(?:[>›❯])\s+(.+?)\s*$")
    for line in (body or "").splitlines():
        m = prompt.match(line)
        if m:
            cand = _prompt_title(m.group(1))
            if cand:
                return cand
    return ""


def _apply_state_transition(state, cached_state, meta):
    """working->done, then clear done once the pane becomes visible/focused."""
    if state == "idle" and cached_state == "working":
        return "done"
    if state == "idle" and cached_state == "done":
        if meta.get("visible"):
            return "idle"                 # no readable prompt: old visible rule
        return "done"
    return state


def _merge_cached(kind, state, cached, meta, now):
    """Reconcile a freshly-detected (kind, state) against the cached entry and
    return (state, ts): apply the working->done edge, clear done when focused,
    carry the last real state over a transient 'unknown', and keep ts while the
    state is unchanged so the age timer keeps counting."""
    same = bool(cached) and cached.get("agent") == kind
    cached_state = cached.get("state") if same else None
    new = _apply_state_transition(state, cached_state, meta)
    if new == "unknown" and same and cached.get("state") not in (None, "unknown"):
        return cached["state"], cached["ts"]
    if same and cached.get("state") == new:
        return new, cached["ts"]
    return new, now


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
        if cached and wall - cached.get("cap_t", 0.0) < CAPTURE_TTL:
            continue  # within the throttle window: keep the cached entry as-is
        body = capture_body(meta["pane_id"])
        kind = detect_agent_kind(cmd, title, body)
        state = _STATE_FNS[kind](title, body) if kind else None
        # pi can leave a frozen "Working..." on screen after finishing, so only
        # trust it as working while the spinner glyph actually changes tick-to-tick.
        spin = _pi_spinner(body)
        if kind == "pi" and state == "working" and spin and cached and spin == cached.get("spin"):
            state = "idle"
        if (
            cached
            and cached.get("agent") == kind
            and cached.get("summary")
            and cached.get("summary_v") == SUMMARY_VERSION
        ):
            summary = cached.get("summary", "")
        else:
            summary = activity_summary(kind, title, body)
        state, ts = _merge_cached(kind, state, cached, meta, now)
        cache[key] = {"agent": kind, "state": state, "ts": ts,
                      "cap_t": wall, "spin": spin,
                      "summary": summary, "summary_v": SUMMARY_VERSION}
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
        agents.append({
            **meta,
            "state": state,
            "agent": kind,
            "ts": c.get("ts", now),
            "summary": c.get("summary", ""),
        })
    return agents


def scrape_agents(panes, now):
    """One-shot: refresh the cache, then read it back. Used by tests; in
    production the two halves are split across the daemon (refresh_scrape) and
    the panels (agents_from_cache)."""
    refresh_scrape(panes, now)
    return agents_from_cache(panes, now)
