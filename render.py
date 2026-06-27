#!/usr/bin/env python3
"""tmux-lasso: build the sidebar frame for tmux windows.

Model:
  - a "space" is one git repository (the toplevel of an agent pane's cwd).
    Agents in subfolders of the same repo share a space. A pane whose cwd is
    not in a git repo falls back to that folder.
  - an "agent" is a tmux pane detected from its foreground process, pane title,
    and recent terminal output (`tmux capture-pane`).

The sidebar shows every window in the current tmux session. Windows without an
agent stay clickable and render as "no agent". Windows with multiple agents
show a window header plus one child row per agent pane.

build_frame(width, session) -> (lines, targets):
  lines[i]   = a ready-to-print ANSI string for display row i
  targets[i] = click target for row i, or None for headers / blank rows.

"""
import os
import subprocess
import time

import detect
import tmux_api

SEP = tmux_api.SEP


def sgr(code):
    return f"\x1b[{code}m"


def fg(r, g, b):
    return sgr(f"38;2;{r};{g};{b}")


def bg(r, g, b):
    return sgr(f"48;2;{r};{g};{b}")

RESET = sgr(0)
DIM = sgr(2)
BOLD = sgr(1)

# state -> (color, attention-priority)
STATES = {
    "blocked": (sgr(31), 4),
    "done":    (sgr(36), 3),
    "working": (sgr(33), 2),
    "idle":    (sgr(32), 1),
    "unknown": (sgr(90), 0),
}
# Truecolor palette tuned to the HTML mockup (soft, low-saturation tints).
# Needs a 24-bit-capable terminal (Ghostty/iTerm/kitty/WezTerm/Alacritty do).
HILITE = bg(42, 47, 55)           # soft blue-gray focus background

# --- variant C: tree + pills -------------------------------------------------
ACCENT = fg(108, 182, 227)        # focus: current window / visible pane
FAINT = fg(74, 79, 87)            # tertiary text (empty windows)
BRANCH_COL = fg(118, 125, 135)    # branch name on the header's right edge
RULE_COL = fg(38, 43, 49)         # header hairline
BAR = "\N{LEFT ONE QUARTER BLOCK}"   # ▎ focus bar in column 0
TEE = "├─"                        # tree branch (non-last child)
ELBOW = "└─"                      # tree branch (last child)
DOT = "\N{BLACK CIRCLE}"        # ● compact state dot
TIMED_STATES = {"working", "blocked"}  # states that show a live age

# state -> (pill bg, pill fg+attrs, label). Timed states append fmt_age().
# bg = the state colour blended ~18-22% over the dark pane; fg = the colour.
PILL = {
    "working": (bg(65, 59, 43),  fg(216, 178, 94)  + BOLD, ""),
    "blocked": (bg(66, 46, 46),  fg(224, 121, 106) + BOLD, "blocked"),
    "done":    (bg(40, 57, 68),  fg(108, 182, 227) + BOLD, "done"),
    "idle":    (bg(40, 50, 42),  fg(156, 204, 122),        "idle"),
    "unknown": (bg(40, 44, 50),  fg(140, 147, 156),        "idle"),
}
AGENT_SHORT = {"codex": "co", "claude": "cc", "pi": "pi"}

# path -> (space_key, label, branch, expiry); git is cheap but not per-frame.
_GIT = {}
_GIT_TTL = 15.0


def _git(cwd, *args):
    try:
        r = subprocess.run(
            ["git", "-C", cwd, *args], capture_output=True, text=True, timeout=1
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ""


def space_of(path):
    """Resolve a pane cwd to (space_key, label, branch). Cached ~15s.

    space_key is the git toplevel (so subfolders of one repo group together);
    for a non-git cwd it is the folder itself. label is the basename shown in
    the UI; branch is the current git branch ("" when not a repo / detached).
    """
    if not path:
        return ("?", "?", "")
    now = time.time()
    hit = _GIT.get(path)
    if hit and hit[3] > now:
        return hit[0], hit[1], hit[2]
    root = _git(path, "rev-parse", "--show-toplevel")
    if root:
        key, label, branch = root, (os.path.basename(root) or root), _git(
            root, "branch", "--show-current"
        )
    else:
        key = path
        label = os.path.basename(path.rstrip("/")) or path
        branch = ""
    _GIT[path] = (key, label, branch, now + _GIT_TTL)
    return key, label, branch


def truncate(text, width):
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def fmt_age(seconds):
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{seconds // 60}:{seconds % 60:02d}"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}"


def seg(text, style="", row_bg=""):
    """A styled run -> (rendered, visible_width).

    Resets after the run, then re-applies row_bg so a highlighted row keeps its
    background between runs. Pass style="" for plain text on the current bg.
    """
    return (f"{style}{text}{RESET}{row_bg}", len(text))


def pill(state, seconds, row_bg=""):
    """State pill -> (rendered, visible_width). A coloured ' label ' chip."""
    bg, fg, label = PILL.get(state, PILL["unknown"])
    if state in TIMED_STATES:
        label = f"{label} {fmt_age(seconds)}".strip()
    text = f" {label} "
    return (f"{bg}{fg}{text}{RESET}{row_bg}", len(text))


def status_chip(state, row_bg=""):
    """Static state chip for group/window headers."""
    bgc, fgc, label = PILL.get(state, PILL["unknown"])
    label = label or state or "idle"
    text = f" {label} "
    return (f"{bgc}{fgc}{text}{RESET}{row_bg}", len(text))


def status_pill(state, ts, now, row_bg=""):
    if state in TIMED_STATES and ts is not None:
        return pill(state, now - ts, row_bg)
    return status_chip(state, row_bg)


def compact_state(state, seconds, row_bg=""):
    """Compact state marker for narrow layouts.

    In tight layouts, blocked should still be distinguishable from idle/done,
    but without spending space on the full label.
    """
    if state == "blocked":
        text = f" {fmt_age(seconds)} "
        bgc, fgc, _label = PILL["blocked"]
        return (f"{bgc}{fgc}{text}{RESET}{row_bg}", len(text))
    color = STATES.get(state, STATES["unknown"])[0]
    return (f"{color}{DOT}{RESET}{row_bg}", 1)


# --- usage footer -----------------------------------------------------------
# Per-provider remaining-quota gauges (one row each for the 5h session and 7d
# week window), pinned to the bottom of the sidebar. Each bar is a plain gauge:
# the solid fill is how much quota is left, coloured green/amber/red as it runs
# down, with a reset countdown on the left. The clickable "usage <age> ↻" header
# reports the last sync (minutes) and forces a refresh when tapped.
GREEN_RGB = (156, 204, 122)
AMBER_RGB = (216, 178, 94)
RED_RGB = (224, 121, 106)
TRACK_RGB = (60, 66, 74)          # empty bar track
USAGE_GREEN = fg(*GREEN_RGB)
USAGE_AMBER = fg(*AMBER_RGB)
USAGE_RED = fg(*RED_RGB)
USAGE_TRACK = fg(*TRACK_RGB)
BAR_FULL = "\N{FULL BLOCK}"       # █ quota remaining (one solid bar)
BAR_EMPTY = "\N{LIGHT SHADE}"     # ░ used up
SYNC_GLYPH = "\N{CLOCKWISE OPEN CIRCLE ARROW}"   # ↻ tap-to-sync button


def _usage_rgb(left):
    """Colour by how much is left: green plenty, amber low, red nearly out."""
    if left is None:
        return TRACK_RGB
    if left < 15:
        return RED_RGB
    if left < 35:
        return AMBER_RGB
    return GREEN_RGB


def _bar(cells, left, rgb):
    """One solid fill = quota remaining; the rest is faint track."""
    fill = max(0, min(cells, int(round((left or 0) / 100.0 * cells))))
    return (f"{fg(*rgb)}{BAR_FULL * fill}{RESET}"
            f"{USAGE_TRACK}{BAR_EMPTY * (cells - fill)}{RESET}")


def _fmt_ago(seconds):
    """Last-sync label in whole minutes (seconds flicker too much): now / 3m."""
    if seconds is None:
        return "—"
    m = int(seconds // 60)
    return "now" if m <= 0 else f"{m}m"


def _fmt_reset(seconds):
    """Compact countdown to a window reset: now / 44m / 2h4m / 1d7h."""
    if seconds is None:
        return ""
    s = int(seconds)
    if s <= 0:
        return "now"
    if s < 3600:
        return f"{max(1, s // 60)}m"
    if s < 86400:
        h, m = s // 3600, (s % 3600) // 60
        return f"{h}h{m}m" if m and h < 10 else f"{h}h"  # cap at 5 cols ("9h59m")
    d, h = s // 86400, (s % 86400) // 3600
    return f"{d}d{h}h" if h else f"{d}d"


def _usage_header(width, synced_age):
    """'usage  <age> ↻' — clickable row that reports the last sync and, when
    tapped, forces a refresh."""
    left = [seg(" usage", DIM)]
    right = [seg(f"{_fmt_ago(synced_age)} ", DIM), seg(SYNC_GLYPH, ACCENT)]
    return compose(width, left, right)


def _window_line(width, name, used, reset_in):
    """' claude  2h4m ████░░ 27%' — countdown to this window's reset, then one
    solid bar of how much quota is left and the percent. Colour (bar + number)
    is by how much is left. The reset countdown stands in for a static 5h/7d tag
    — its scale (minutes/hours vs days) tells the windows apart."""
    name6 = (name or "")[:6].ljust(6)
    rs = _fmt_reset(reset_in)
    rs5 = rs.rjust(5)                      # fixed reset column so bars align
    if used is None:
        return f" {name6} {DIM}{rs5}{RESET}    {DIM}—{RESET}"
    left = max(0.0, min(100.0, 100.0 - float(used)))
    rgb = _usage_rgb(left)
    col = fg(*rgb)
    pct = f"{left:>3.0f}%"
    head = 1 + 6 + 1 + 5 + 1               # ' ' name ' ' reset ' '
    cells = width - head - (1 + len(pct))
    if cells < 1:                          # too tight for a bar: name + percent
        return f" {name6} {DIM}{rs5}{RESET} {col}{pct}{RESET}"
    bar = _bar(cells, left, rgb)
    return f" {name6} {DIM}{rs5}{RESET} {bar} {col}{pct}{RESET}"


def usage_rows(width, snap, synced_age=None, now=None):
    """Footer rows [(line, target)] for build_rows to pin to the bottom: a rule,
    the clickable sync header, then a 5h + 7d remaining bar per provider with a
    faint divider between providers. Empty until the first snapshot lands. The
    header row's target is ('usage',)."""
    if not snap or not any(snap.get(k) for k in ("claude", "codex")):
        return []
    if now is None:
        now = int(time.time())
    rows = [
        (f"{RULE_COL}{'─' * width}{RESET}", None),
        (_usage_header(width, synced_age), ("usage",)),
    ]
    first = True
    for key in ("claude", "codex"):
        u = snap.get(key)
        if not u:
            continue
        if not first:
            # a faint hairline between providers so each claude/codex block of
            # bars reads as separate rather than merging into one
            rows.append((f"{RULE_COL}{'─' * width}{RESET}", None))
        first = False
        sr, wr = u.get("session_reset"), u.get("week_reset")
        rows.append((_window_line(width, key, u.get("session"),
                                  (sr - now) if sr else None), None))
        rows.append((_window_line(width, "", u.get("week"),
                                  (wr - now) if wr else None), None))
    return rows


def with_usage_footer(rows, width, snap, synced_age=None, height=None):
    """Append the usage footer to a panel's rows, pinned to the bottom when
    there's room. Shared by the desktop sidebar and the mobile switcher so both
    grow the same bars below the same window tree."""
    foot = usage_rows(width, snap, synced_age)
    if not foot:
        return rows
    if height:
        pad = height - len(rows) - len(foot)
        if pad > 0:
            rows = rows + [("", None)] * pad
    return rows + foot


def compose(width, left, right=(), row_bg=""):
    """Lay out left + right segments on one full-width row.

    left/right are lists of (rendered, visible_width) from seg()/pill(). The row
    is padded with row_bg to the full width, so a highlight spans the whole pane
    instead of stopping at the text (the old ragged edge).
    """
    lvis = sum(v for _, v in left)
    rvis = sum(v for _, v in right)
    gap = max(1, width - lvis - rvis)
    parts = [row_bg] if row_bg else []
    parts.extend(s for s, _ in left)
    parts.append(f"{row_bg}{' ' * gap}")
    parts.extend(s for s, _ in right)
    parts.append(RESET)
    return "".join(parts)


def header(width):
    """Top row: 'agents' left; clickable 'sync' and 'switch' buttons right."""
    sync_txt, gap_txt, sw_txt = "sync", "  ", "switch "
    if width < len(sync_txt) + len(gap_txt) + len(sw_txt):
        right = [seg(sw_txt, ACCENT + BOLD)]
        rvis = sum(v for _, v in right)
        left = [seg(" agents", DIM)] if width >= rvis + 8 else []
        return compose(width, left, right), ("switch",)

    right = [
        seg(sync_txt, ACCENT + BOLD),
        seg(gap_txt),
        seg(sw_txt, ACCENT + BOLD),
    ]
    rvis = sum(v for _, v in right)
    left = [seg(" agents", DIM)] if width >= rvis + 8 else []
    line = compose(width, left, right)
    start = width - rvis
    return line, ("buttons", [
        (start + 1, start + len(sync_txt), ("sync",)),
        (start + len(sync_txt) + len(gap_txt) + 1, width, ("switch",)),
    ])


def tmux_windows(session):
    fmt = SEP.join([
        "#{window_id}", "#{session_name}", "#{window_index}", "#{window_name}",
        "#{window_active}", "#{window_active_clients}", "#{window_active_sessions}",
    ])
    try:
        out = tmux_api.run("list-windows", "-t", session, "-F", fmt)
    except Exception:
        return []
    windows = []
    for line in out.splitlines():
        p = line.split(SEP)
        if len(p) != 7:
            continue
        wid, sname, widx, wname, active, active_clients, active_sessions = p
        windows.append({
            "window_id": wid,
            "session": sname,
            "win_index": widx,
            "win_name": wname,
            "win_current": active == "1" and active_clients != "0" and active_sessions != "0",
        })
    return windows


def sort_agents(members):
    return sorted(
        members,
        key=lambda m: (
            not m["win_current"],
            STATES.get(m["state"], STATES["unknown"])[1] * -1,
            int(m["pane_index"] or 0),
        ),
    )


def window_pane(session, win_index, panes):
    """The pane that represents a window: the visible work pane if any, else the
    first non-sidebar pane. Returns the pane meta dict, or None."""
    return next(
        (
            p for p in panes.values()
            if p["session"] == session and p["win_index"] == win_index and p["visible"] and not p["sidebar"]
        ),
        next(
            (
                p for p in panes.values()
                if p["session"] == session and p["win_index"] == win_index and not p["sidebar"]
            ),
            None,
        ),
    )


def window_target(session, win_index, panes):
    pane = window_pane(session, win_index, panes)
    return ("agent", session, win_index, pane["pane_id"]) if pane else None


def window_space(session, window, panes, members):
    """Folder/repo metadata for a tmux window.

    Agent panes already carry their resolved git repo. Empty/new shell tabs use
    their work pane cwd, so a new tab opened inside TextCut groups with the
    existing TextCut agents instead of floating as an unrelated shell window.
    """
    if members:
        m = members[0]
        return m["space"], m["space_label"], m["branch"]
    pane = window_pane(session, window["win_index"], panes)
    if pane and pane.get("path"):
        return space_of(pane["path"])
    label = window.get("win_name") or "window"
    return f"window:{window.get('window_id') or window['win_index']}", label, ""


def _space_groups(session, windows, by_window, panes):
    groups, by_key = [], {}
    for w in sorted(windows, key=lambda m: int(m["win_index"] or 0)):
        members = sort_agents(by_window.get(w["win_index"], []))
        key, label, branch = window_space(session, w, panes, members)
        entry = {"window": w, "members": members}
        group = by_key.get(key)
        if not group:
            group = {"key": key, "label": label, "branch": branch, "entries": []}
            by_key[key] = group
            groups.append(group)
        if not group.get("branch") and branch:
            group["branch"] = branch
        group["entries"].append(entry)
    return groups


def _window_child_label(window, members):
    if members:
        return window.get("win_name") or (
            members[0]["agent"] if len(members) == 1 else f"{len(members)} agents"
        )
    return window.get("win_name") or "shell"


def _agent_label(member):
    summary = (member.get("summary") or "").strip()
    agent = member.get("agent") or "agent"
    short = AGENT_SHORT.get(agent, agent)
    if not summary:
        return short
    if summary.lower().startswith(f"{agent.lower()}:") or summary.lower().startswith(f"{short.lower()}:"):
        return summary
    return f"{short}: {summary}"


def _aggregate_status(entries):
    members = [
        m
        for e in entries
        for m in e.get("members", [])
        if m.get("state") in STATES
    ]
    if not members:
        return "", None
    # Highest-priority state wins; ties use the oldest timestamp so the header
    # shows the longest-running active state in that workspace.
    m = max(
        members,
        key=lambda x: (
            STATES.get(x.get("state"), STATES["unknown"])[1],
            -(x.get("ts") or 0),
        ),
    )
    return m.get("state"), m.get("ts")


def _right_meta(_branch, status, now, row_bg, branch_style=BRANCH_COL):
    right = []
    state, ts = status if status else ("", None)
    if state:
        right.append(status_pill(state, ts, now, row_bg))
    return right


def _space_header_row(width, label, branch, current, status=None, now=0):
    # Workspace header is a grouping label, not a selectable row — never
    # highlight it so it doesn't look "selected" alongside the active tab.
    col0 = seg(" ")
    right = _right_meta(truncate(branch, max(3, width // 2)), status, now, "")
    fixed = col0[1] + 2 + sum(v for _, v in right) + 1
    budget = max(1, width - fixed)
    left = [
        col0,
        seg("▾ ", ACCENT if current else DIM),
        seg(truncate(label, budget), BOLD),
    ]
    return compose(width, left, right)


def _task_row(width, idx, label, current, target, prefix="", status=None, now=0):
    row_bg = HILITE if current else ""
    col0 = (f"{ACCENT}{BAR}{RESET}{row_bg}", 1) if current else seg(" ", "", row_bg)
    idx_style = (ACCENT + BOLD) if current else (DIM + BOLD)
    right = _right_meta("", status, now, row_bg)
    fixed = col0[1] + len(prefix) + len(idx) + 1 + sum(v for _, v in right) + 1
    if status and width - fixed < 2:
        state, ts = status
        right = [compact_state(state, now - ts if ts is not None else 0, row_bg)]
        fixed = col0[1] + len(prefix) + len(idx) + 1 + sum(v for _, v in right) + 1
    if status and width - fixed < 0:
        right = []
        fixed = col0[1] + len(prefix) + len(idx) + 1 + 1
    budget = max(0, width - fixed)
    left = [col0]
    if prefix:
        left.append(seg(prefix, DIM, row_bg))
    left += [
        seg(idx, idx_style, row_bg),
        seg(" ", "", row_bg),
        seg(truncate(label, budget), BOLD if current else "", row_bg),
    ]
    return compose(width, left, right, row_bg=row_bg), target


def _win_header_row(width, col0, idx, label, branch,
                    idx_style, label_style, branch_style, row_bg, prefix="", status=None, now=0):
    """One window row: [col0][idx] [label] ....... [branch]. Shared by the
    empty-window and agent-header branches so the width-budget math lives once."""
    right = _right_meta(truncate(branch, max(3, width // 2)), status, now, row_bg, branch_style)
    fixed = col0[1] + len(prefix) + len(idx) + 1 + sum(v for _, v in right) + 1
    budget = max(1, width - fixed)
    left = [col0]
    if prefix:
        left.append(seg(prefix, DIM, row_bg))
    left += [seg(idx, idx_style, row_bg), seg(" ", "", row_bg),
             seg(truncate(label, budget), label_style, row_bg)]
    return compose(width, left, right, row_bg=row_bg)


def _add_window_block(rows, width, session, now, window, members, panes, grouped=False):
    def add(line, target=None):
        rows.append((line, target))

    current = window["win_current"]
    idx = window["win_index"]
    target = window_target(session, idx, panes)
    row_bg = HILITE if current else ""
    col0 = (f"{ACCENT}{BAR}{RESET}{row_bg}", 1) if current else seg(" ", "", row_bg)
    prefix = "  " if grouped else ""

    if not members:
        # Empty window: still show where it is. Inside a space group the folder
        # is already in the group header, so the child row uses the tab/window
        # name instead of repeating the same project label.
        if grouped:
            label, branch = _window_child_label(window, members), ""
        else:
            pane = window_pane(session, idx, panes)
            if pane and pane.get("path"):
                _, label, branch = space_of(pane["path"])
            else:
                label, branch = (window["win_name"] or "window"), ""
        add(_win_header_row(width, col0, idx, label, branch,
                            FAINT + BOLD, FAINT, FAINT, "", prefix), target)
        if not grouped:
            add("")
        return

    if grouped and len(members) == 1:
        m = members[0]
        label = _agent_label(m)
        mt = ("agent", m["session"], m["win_index"], m["pane_id"])
        line, mt = _task_row(width, idx, label, current, mt, prefix, (m["state"], m.get("ts")), now)
        add(line, mt)
        return

    # Window header: either the repo label (standalone) or the tab label under a
    # repo/folder group.
    a = members[0]
    label = _window_child_label(window, members) if grouped else a["space_label"]
    branch = "" if grouped else a["branch"]
    status = _aggregate_status([{"members": members}])
    idx_style = (ACCENT + BOLD) if current else (DIM + BOLD)
    add(_win_header_row(width, col0, idx, label, branch,
                        idx_style, BOLD if current else "", BRANCH_COL, row_bg, prefix, status, now), target)

    # Agents nested under the window via tree connectors.
    last = len(members) - 1
    # When a tab holds 2+ agents, letter them A,B,C… (in this list order) so
    # each pane is addressable as tab+letter, e.g. the switcher's "kill 2C".
    lettered = len(members) > 1
    for i, m in enumerate(members):
        visible = m["visible"]
        c_bg = HILITE if current else ""
        conn = ELBOW if i == last else TEE
        indent = "   " if grouped else " "
        right = _right_meta("", (m["state"], m.get("ts")), now, c_bg)
        fixed = len(indent) + len(conn) + 1 + sum(v for _, v in right) + 1
        if width - fixed < 2:
            right = [compact_state(m["state"], now - m["ts"] if m.get("ts") is not None else 0, c_bg)]
            fixed = len(indent) + len(conn) + 1 + sum(v for _, v in right) + 1
        if width - fixed < 0:
            right = []
            fixed = len(indent) + len(conn) + 1 + 1
        budget = max(1, width - fixed)
        label = f"{chr(65 + i)} {_agent_label(m)}" if lettered else _agent_label(m)
        nm = truncate(label, budget)
        left = [
            seg(indent, "", c_bg),
            seg(conn, (ACCENT if visible else DIM), c_bg),
            seg(" ", "", c_bg),
            seg(nm, (BOLD if visible else ""), c_bg),
        ]
        mt = ("agent", m["session"], m["win_index"], m["pane_id"])
        add(compose(width, left, right, row_bg=c_bg), mt)
    if not grouped:
        add("")


def _window_rows(width, session, now, windows, by_window, panes):
    """The per-window tree (window header + nested agent pills) as a list of
    (line, target). Shared by the desktop frame and the mobile switcher so the
    two render identically. target for a window/agent row is
    ("agent", session, win_index, pane_id); blank spacer rows have target None."""
    rows = []
    groups = _space_groups(session, windows, by_window, panes)

    for g in groups:
        current = any(e["window"]["win_current"] for e in g["entries"])
        rows.append((
            _space_header_row(
                width, g["label"], g.get("branch", ""),
                current, None, now,
            ),
            None,
        ))
        for e in g["entries"]:
            _add_window_block(
                rows, width, session, now, e["window"], e["members"], panes, grouped=True
            )
        rows.append(("", None))

    while rows and rows[-1][0] == "":
        rows.pop()
    return rows


def _session_frame_data(session, now):
    """Shared lookup: live panes, this session's windows, and agents grouped by
    window index. Returned to both build_frame and window_rows."""
    panes = detect.tmux_panes()
    agents = detect.agents_from_cache(panes, now)   # read the daemon's cache, no capture
    for a in agents:
        a["space"], a["space_label"], a["branch"] = space_of(a["path"])
    windows = tmux_windows(session)
    by_window = {}
    for a in agents:
        if a["session"] == session:
            by_window.setdefault(a["win_index"], []).append(a)
    return panes, windows, by_window


def window_rows(width, session):
    """Self-contained per-window tree for callers outside the desktop frame
    (the mobile switcher). Returns [(line, target)]."""
    width = max(8, width)
    if not session:
        return []
    now = int(time.time())
    panes, windows, by_window = _session_frame_data(session, now)
    return _window_rows(width, session, now, windows, by_window, panes)


def focused_agent(session):
    """The focused agent in the session's current window, for the switcher's
    per-agent kill. Returns (letter, pane_id, count): the agent's per-window
    letter (A,B,… in the same order the tree lists them, '' when the window holds
    a single agent), its pane id, and how many agents share the window. The
    focused agent is the visible pane; ('', '', n) when no agent pane is focused."""
    if not session:
        return "", "", 0
    now = int(time.time())
    _panes, windows, by_window = _session_frame_data(session, now)
    for w in windows:
        if not w["win_current"]:
            continue
        members = sort_agents(by_window.get(w["win_index"], []))
        for i, m in enumerate(members):
            if m["visible"]:
                return (chr(65 + i) if len(members) > 1 else ""), m["pane_id"], len(members)
        return "", "", len(members)
    return "", "", 0


def build_frame(width, session):
    width = max(8, width)
    if not session:
        return [], []
    now = int(time.time())
    panes, windows, by_window = _session_frame_data(session, now)

    lines, targets = [], []

    def add(line, target=None):
        lines.append(line)
        targets.append(target)

    # header: "agents" on the left, clickable "switch" button on the right.
    add(*header(width))
    if not windows:
        add("")
        add(f"{DIM} no windows{RESET}")
        return lines, targets
    add(f"{RULE_COL}{'─' * width}{RESET}")
    for line, target in _window_rows(width, session, now, windows, by_window, panes):
        add(line, target)
    return lines, targets
