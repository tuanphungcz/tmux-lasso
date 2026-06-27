#!/usr/bin/env python3
"""Shared tmux + small shared-file helpers for tmux-lasso."""
import json
import os
import subprocess

SEP = "\x1f"


def run(*args, timeout=2):
    try:
        return subprocess.run(
            ["tmux", *args], capture_output=True, text=True, timeout=timeout
        ).stdout.rstrip("\n")
    except Exception:
        return ""


def display(fmt, target=None):
    args = ["display-message", "-p"]
    if target:
        args += ["-t", target]
    args.append(fmt)
    return run(*args)


def client_for_session(session):
    out = run("list-clients", "-F", f"#{{client_name}}{SEP}#{{client_session}}")
    for line in out.splitlines():
        name, _, sess = line.partition(SEP)
        if sess == session:
            return name
    return ""


def read_json(path, default=None):
    """Read a JSON file, or return `default` ({} if unset) on any failure."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {} if default is None else default


def write_json(path, data):
    """Atomically publish `data` as JSON: write a pid-suffixed temp, then
    os.replace it in. Best-effort -- swallows errors (it's a cache, not truth)."""
    try:
        tmp = f"{path}.{os.getpid()}"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        pass
