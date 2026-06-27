#!/bin/sh
# TPM entry point: bind the toggle key and start tmux-lasso on tmux launch.
# TPM runs this file with $0 = its own path, so we resolve our dir from it.
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

key=$(tmux show -gv @tmux_lasso_key 2>/dev/null); [ -n "$key" ] || key=g
tmux bind-key "$key" run-shell "$HERE/toggle.sh"      # prefix + g toggles
new_key=$(tmux show -gv @tmux_lasso_new_window_key 2>/dev/null); [ -n "$new_key" ] || new_key=c
[ "$new_key" = "off" ] || tmux bind-key "$new_key" run-shell "$HERE/toggle.sh new-window"
tmux run-shell -b "$HERE/toggle.sh startup"           # always-on by default
# ponytail: no @tmux_lasso_startup opt-out — drop this last line to install key-only.

# Mobile top bar is just a "switch" button: tapping the status line opens the
# switcher. Desktop keeps tmux's default (click a window in the list to select).
tmux bind-key -T root MouseDown1Status if-shell -F '#{==:#{@tmux_lasso_mobile},1}' \
  "display-popup -BE -w 100% -h 100% '$HERE/switch.py'" "select-window -t ="
