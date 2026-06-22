#!/usr/bin/env python3
"""Shared terminal UI runtime for Lasso entrypoints."""
import os
import re
import select
import signal
import sys
import termios
import time
import tty

MOUSE_ON = "\x1b[?1000h\x1b[?1006h"
MOUSE_OFF = "\x1b[?1000l\x1b[?1006l"
HIDE = "\x1b[?25l"
SHOW = "\x1b[?25h"
SGR = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")


def terminal_size(fileno, default_cols, default_lines):
    try:
        size = os.get_terminal_size(fileno)
        cols, lines = size.columns, size.lines
    except OSError:
        cols, lines = 0, 0
    if cols <= 0:
        cols = int(os.environ.get("COLUMNS") or default_cols)
    if lines <= 0:
        lines = int(os.environ.get("LINES") or default_lines)
    return cols, lines


def clamp_scroll(offset, rows_len, height):
    max_offset = max(0, rows_len - max(0, height))
    return max(0, min(offset, max_offset))


def page_step(height):
    return max(1, height - 1)


def paint_rows(out, rows, height, offset=0):
    buf = ["\x1b[H"]
    offset = clamp_scroll(offset, len(rows), height)
    visible = rows[offset:offset + max(0, height)]
    limit = min(len(visible), height)
    for i in range(limit):
        buf.append(visible[i][0] + "\x1b[K")
        if i < limit - 1:
            buf.append("\r\n")
    buf.append("\x1b[J")
    out.write("".join(buf))
    out.flush()


def trim_input_buffer(buf, consumed):
    buf = buf[consumed:]
    tail = buf.rfind("\x1b")
    return buf[tail:] if tail != -1 else (buf if len(buf) < 8 else "")


def run_mouse_ui(
    build_rows,
    on_action,
    *,
    refresh_interval,
    default_cols,
    default_lines,
    should_exit=None,
    on_tick=None,
    key_should_close=None,
):
    """Run a repainting mouse UI.

    build_rows(width, height) -> [(line, action)]
    on_action(action, x, y) -> truthy to exit immediately
    should_exit() -> truthy to stop before repaint
    on_tick() -> optional hook run before periodic repaint; may exec/exit
    key_should_close(buffer) -> truthy to exit on plain-key input
    """
    fd = sys.stdin.fileno()
    out = sys.stdout
    old = termios.tcgetattr(fd)

    def restore(*_):
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass
        out.write(MOUSE_OFF + SHOW + "\x1b[0m")
        out.flush()

    def die(*_):
        restore()
        os._exit(0)

    signal.signal(signal.SIGTERM, die)
    signal.signal(signal.SIGHUP, die)
    tty.setcbreak(fd)
    out.write(HIDE + MOUSE_ON + "\x1b[2J")
    out.flush()
    scroll = 0

    def redraw():
        nonlocal scroll
        width, height = terminal_size(out.fileno(), default_cols, default_lines)
        rows = build_rows(width, height)
        scroll = clamp_scroll(scroll, len(rows), height)
        paint_rows(out, rows, height, scroll)
        return rows, height

    try:
        rows, height = redraw()
        next_draw = time.monotonic() + refresh_interval
        buf = ""
        while True:
            timeout = max(0.0, next_draw - time.monotonic())
            ready, _, _ = select.select([fd], [], [], timeout)
            if ready:
                try:
                    data = os.read(fd, 1024)
                except OSError:
                    break
                if not data:
                    break
                buf += data.decode("utf-8", "replace")
                if key_should_close and key_should_close(buf):
                    break
                consumed = 0
                for match in SGR.finditer(buf):
                    consumed = match.end()
                    button = int(match.group(1))
                    x = int(match.group(2))
                    y = int(match.group(3))
                    kind = match.group(4)
                    if kind != "M":
                        continue
                    if button & 0x40:
                        step = 1
                        if (button & 0x03) == 0:
                            scroll = clamp_scroll(scroll - step, len(rows), height)
                        elif (button & 0x03) == 1:
                            scroll = clamp_scroll(scroll + step, len(rows), height)
                        paint_rows(out, rows, height, scroll)
                        next_draw = time.monotonic() + refresh_interval
                        continue
                    if (button & 0x03) == 0:
                        idx = scroll + y - 1
                        if 0 <= idx < len(rows) and rows[idx][1]:
                            if on_action(rows[idx][1], x, y):
                                return
                            rows, height = redraw()
                            next_draw = time.monotonic() + refresh_interval
                if "\x1b[B" in buf:
                    scroll = clamp_scroll(scroll + 1, len(rows), height)
                    paint_rows(out, rows, height, scroll)
                elif "\x1b[A" in buf:
                    scroll = clamp_scroll(scroll - 1, len(rows), height)
                    paint_rows(out, rows, height, scroll)
                elif "\x1b[6~" in buf:
                    scroll = clamp_scroll(scroll + page_step(height), len(rows), height)
                    paint_rows(out, rows, height, scroll)
                elif "\x1b[5~" in buf:
                    scroll = clamp_scroll(scroll - page_step(height), len(rows), height)
                    paint_rows(out, rows, height, scroll)
                elif buf in ("j", "\x0e"):
                    scroll = clamp_scroll(scroll + 1, len(rows), height)
                    paint_rows(out, rows, height, scroll)
                elif buf in ("k", "\x10"):
                    scroll = clamp_scroll(scroll - 1, len(rows), height)
                    paint_rows(out, rows, height, scroll)
                buf = trim_input_buffer(buf, consumed)
            if time.monotonic() >= next_draw:
                if should_exit and should_exit():
                    break
                if on_tick:
                    on_tick()
                rows, height = redraw()
                next_draw = time.monotonic() + refresh_interval
    finally:
        restore()
