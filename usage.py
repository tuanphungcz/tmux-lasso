#!/usr/bin/env python3
"""tmux-lasso: subscription usage for the sidebar footer.

Both Claude Code and Codex expose a "session" (5-hour) and a weekly rate-limit
window as a percent used, via the same endpoints their official CLIs/menubar
apps poll:

  claude -> GET api.anthropic.com/api/oauth/usage   (OAuth bearer)
  codex  -> GET chatgpt.com/backend-api/wham/usage  (ChatGPT bearer)

The reconciler daemon is the SOLE fetcher: every TTL (or when the footer's sync
button sets @tmux_lasso_usage_force) it runs `usage.py fetch-once` as a detached
subprocess, which writes the latest values to a small temp file. Every sidebar
and the switcher just read that file via snapshot() -- no per-process polling,
threads or locks. Running the fetch out-of-process means a wedged network call
can never stall the daemon's reconcile loop.
"""
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import tmux_api

# Auto-refresh interval. The 5h/weekly windows move slowly, so 60s is plenty;
# override with TMUX_LASSO_USAGE_TTL (seconds). Tapping the footer forces a refresh
# regardless. Floored at 10s so a tiny value can't hammer the endpoints.
try:
    TTL = max(10.0, float(os.environ.get("TMUX_LASSO_USAGE_TTL", "60")))
except ValueError:
    TTL = 60.0
TIMEOUT = 8                     # per-request network timeout
CACHE = os.path.join(tempfile.gettempdir(), "tmux-lasso-usage.json")
FORCE_OPT = "@tmux_lasso_usage_force"   # footer tap sets this; the daemon acts on it


# --- token sources ----------------------------------------------------------
def _claude_token():
    """Live OAuth token. The macOS keychain holds the refreshed copy; the
    dotfile is a stale fallback for non-keychain setups."""
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s",
             "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0 and out.stdout.strip():
            return json.loads(out.stdout)["claudeAiOauth"]["accessToken"]
    except Exception:
        pass
    try:
        with open(os.path.expanduser("~/.claude/.credentials.json")) as f:
            return json.load(f)["claudeAiOauth"]["accessToken"]
    except Exception:
        return None


def _codex_auth():
    try:
        with open(os.path.expanduser("~/.codex/auth.json")) as f:
            t = json.load(f).get("tokens") or {}
        return t.get("access_token"), t.get("account_id", "")
    except Exception:
        return None, ""


def _iso_epoch(s):
    if not s:
        return None
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def _get_json(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


# --- fetchers ---------------------------------------------------------------
def fetch_claude():
    """{session, week, session_reset, week_reset} percent-used, or None."""
    tok = _claude_token()
    if not tok:
        return None
    d = _get_json(
        "https://api.anthropic.com/api/oauth/usage",
        {"Authorization": f"Bearer {tok}",
         "anthropic-beta": "oauth-2025-04-20"},
    )
    fh = d.get("five_hour") or {}
    sd = d.get("seven_day") or {}
    return {
        "session": fh.get("utilization"),
        "session_reset": _iso_epoch(fh.get("resets_at")),
        "week": sd.get("utilization"),
        "week_reset": _iso_epoch(sd.get("resets_at")),
    }


def fetch_codex():
    tok, acc = _codex_auth()
    if not tok:
        return None
    d = _get_json(
        "https://chatgpt.com/backend-api/wham/usage",
        {"Authorization": f"Bearer {tok}",
         "chatgpt-account-id": acc, "User-Agent": "tmux-lasso"},
    )
    rl = d.get("rate_limit") or {}
    pw = rl.get("primary_window") or {}
    sw = rl.get("secondary_window") or {}
    return {
        "session": pw.get("used_percent"),
        "session_reset": pw.get("reset_at"),
        "week": sw.get("used_percent"),
        "week_reset": sw.get("reset_at"),
    }


# --- shared file (daemon writes, everyone reads) ----------------------------
def read_cache():
    return tmux_api.read_json(CACHE)


def _write_cache(data):
    tmux_api.write_json(CACHE, data)


def snapshot():
    """Latest usage from the shared file (the daemon keeps it fresh), or {}
    before the first fetch lands. Never fetches or blocks -- just a file read."""
    return read_cache()


def age():
    """Seconds since the cache was last written, or None if never."""
    try:
        return max(0.0, time.time() - os.stat(CACHE).st_mtime)
    except OSError:
        return None


def force_refresh():
    """Footer sync button: flag the daemon to fetch now. The daemon reads and
    clears @tmux_lasso_usage_force next tick and applies its own min-gap, so a stray
    double-tap can't hammer the endpoints."""
    tmux_api.run("set", "-g", FORCE_OPT, "1")


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


def fetch_once():
    """Fetch both providers and write the shared file, keeping the last-known
    value for any provider whose fetch fails (a transient 429 never blanks a
    working bar). The daemon runs this as a detached subprocess.

    A non-blocking flock makes a second fetch a no-op while one is in flight, so
    a forced tap landing on top of a periodic fetch can't lose-update it (each
    process starts from its own read_cache() snapshot)."""
    with open(CACHE + ".lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return                   # another fetch is already running
        data = dict(read_cache())
        c = _safe(fetch_claude)
        x = _safe(fetch_codex)
        if c:
            data["claude"] = c
        if x:
            data["codex"] = x
        if c or x:
            _write_cache(data)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "fetch-once":
        fetch_once()
