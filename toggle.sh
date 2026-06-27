#!/bin/sh
# tmux-lasso: manage the always-on sidebar across every tmux window.
#
#   toggle.sh                -> toggle on/off globally (bound to prefix + g)
#   toggle.sh enable         -> turn on: start the reconciler daemon + show sidebars
#   toggle.sh disable        -> turn off: stop the daemon, remove sidebars
#   toggle.sh startup        -> enable on tmux start (default on)
#   toggle.sh reconcile      -> internal: one idempotent pass (the daemon calls this)
#   toggle.sh add-window <id>-> internal: add a sidebar to one window (switch.py)
#   toggle.sh new-window [s] -> open a tab in the current work pane's cwd
#   toggle.sh compact-space [s] -> make current cwd's windows contiguous
#   toggle.sh sync-width <p> -> internal: adopt pane p's width as the global width
#
# A sidebar pane is tagged with the pane option @tmux_lasso_pane=1 so we can find/skip it.
# Lifecycle is owned by ONE reconciler (daemon.py): it polls and calls `reconcile`
# every tick, so a missing or duplicated sidebar self-heals. No tmux hooks, no
# per-pane leader election -- that scheme is what made the old sidebar race, vanish
# and duplicate. State: global option @tmux_lasso_on = on|off, @tmux_lasso_mobile = 1 when a
# phone-width client is active (sidebar hidden; the status bar is left untouched).
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PANEL="$HERE/panel.py"
DAEMON="$HERE/daemon.py"
MIN_SIDEBAR_WIDTH="${TMUX_LASSO_MIN_WIDTH:-18}"   # floor and default width

is_uint() {
  case "$1" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac
}

desktop_width() {
  v="$1"
  is_uint "$v" || v="$MIN_SIDEBAR_WIDTH"
  [ "$v" -lt "$MIN_SIDEBAR_WIDTH" ] && v="$MIN_SIDEBAR_WIDTH"
  printf '%s\n' "$v"
}

pane_width() {  # $1 = window id
  if is_uint "${TMUX_LASSO_WIDTH:-}"; then
    desktop_width "$TMUX_LASSO_WIDTH"
    return 0
  fi
  saved=$(tmux show -gv @tmux_lasso_width 2>/dev/null)
  if is_uint "$saved"; then
    desktop_width "$saved"
    return 0
  fi
  desktop_width "$MIN_SIDEBAR_WIDTH"
}

is_on() { [ "$(tmux show -gv @tmux_lasso_on 2>/dev/null)" = "on" ]; }
is_mobile() { [ "$(tmux show -gv @tmux_lasso_mobile 2>/dev/null)" = "1" ]; }

# --- mobile mode ------------------------------------------------------------
# On a phone-width client the sidebar is dropped entirely so the work pane goes
# fullscreen -- that's all. We deliberately DON'T restyle the status bar: the old
# save/restore of the user's ~12 global status options was the last fragile
# corruptor (it once blanked the bar on macOS). Your normal tmux bar stays as-is;
# on a phone (@tmux_lasso_mobile=1) tapping the top status line opens switch.py via the
# MouseDown1Status binding in tmux-lasso.tmux.
# Below this client width the sidebar is dropped. Tune via @tmux_lasso_mobile_width
# (or TMUX_LASSO_MOBILE_WIDTH env); a phone over SSH (Termius landscape ~79 cols)
# should fall under it, a full desktop terminal should not.
mobile_width() {
  is_uint "${TMUX_LASSO_MOBILE_WIDTH:-}" && { printf '%s\n' "$TMUX_LASSO_MOBILE_WIDTH"; return 0; }
  saved=$(tmux show -gv @tmux_lasso_mobile_width 2>/dev/null)
  is_uint "$saved" && { printf '%s\n' "$saved"; return 0; }
  printf '90\n'   # default: catches a phone in landscape (~79-89 cols); a real desktop terminal is wider
}

enter_mobile() {  # phone: drop the sidebars; the flag routes status taps to switch.py
  is_mobile && return 0
  tmux set -g @tmux_lasso_mobile 1
  kill_all
}

exit_mobile() {
  is_mobile || return 0
  tmux set -g @tmux_lasso_mobile 0
}

# --- sidebar panes ----------------------------------------------------------
add_window() {  # $1 = window id
  win="$1"
  width=$(pane_width "$win")
  is_on || return 0
  is_mobile && return 0   # phone: no sidebar, the switch popup replaces it
  [ -n "$win" ] || return 0
  # already has a sidebar pane?
  if tmux list-panes -t "$win" -F '#{@tmux_lasso_pane}' 2>/dev/null | grep -q '^1$'; then
    return 0
  fi
  pane=$(tmux split-window -hbf -l "$width" -d -t "$win" -P -F '#{pane_id}' \
           "exec '$PANEL'" 2>/dev/null) || return 0
  [ -n "$pane" ] && tmux set -p -t "$pane" @tmux_lasso_pane 1 2>/dev/null
}

resize_window_to() {  # $1 = window id, $2 = width
  win="$1"
  width="$2"
  is_on || return 0
  [ -n "$win" ] || return 0
  is_uint "$width" || return 0
  pane=$(tmux list-panes -t "$win" -F '#{pane_id} #{@tmux_lasso_pane}' 2>/dev/null \
    | awk '$2==1 {print $1; exit}')
  [ -n "$pane" ] || return 0
  cur=$(tmux display-message -p -t "$pane" '#{pane_width}' 2>/dev/null)
  if is_uint "$cur" && [ "$cur" -eq "$width" ]; then
    return 0
  fi
  tmux resize-pane -t "$pane" -x "$width" 2>/dev/null || true
}

resize_window() {  # $1 = window id
  win="$1"
  resize_window_to "$win" "$(pane_width "$win")"
}

kill_all() {
  tmux list-panes -a -F '#{pane_id} #{@tmux_lasso_pane}' 2>/dev/null \
    | awk '$2==1 {print $1}' \
    | while IFS= read -r p; do tmux kill-pane -t "$p" 2>/dev/null; done
}

# --- reconcile (the one writer) ---------------------------------------------
narrowest_client() {  # width of the smallest attached client, or empty if none
  tmux list-clients -F '#{client_width}' 2>/dev/null \
    | grep -E '^[0-9]+$' | sort -n | head -n1
}

reconcile_mobile() {
  # Mobile follows the narrowest attached client: a phone over SSH falls under
  # the threshold, a desktop doesn't. Only flip on a real crossing.
  # ponytail: @tmux_lasso_mobile is one global flag; a phone AND a desktop attached
  # at once forces mobile for both (panes are window-global, not per-client).
  w=$(narrowest_client)
  is_uint "$w" || return 0    # nothing attached: leave the current mode as-is
  if [ "$w" -lt "$(mobile_width)" ]; then
    is_mobile || enter_mobile
  else
    is_mobile && exit_mobile
  fi
}

reconcile_sidebars() {
  # Desktop steady state: every window has exactly one sidebar pane at the
  # current width. Idempotent -- a gap is filled, a duplicate (briefly created
  # by a race) is killed keeping the lowest pane id, and a window left with only
  # a sidebar (its work pane closed) loses it so the window closes as expected.
  tmux list-windows -a -F '#{window_id}' 2>/dev/null | while IFS= read -r win; do
    [ -n "$win" ] || continue
    work=$(tmux list-panes -t "$win" -F '#{@tmux_lasso_pane}' 2>/dev/null \
           | awk '$1!="1"{n++} END{print n+0}')
    sidebars=$(tmux list-panes -t "$win" -F '#{pane_id} #{@tmux_lasso_pane}' 2>/dev/null \
               | awk '$2=="1"{print $1}')
    if [ "$work" -eq 0 ]; then
      printf '%s\n' "$sidebars" | while IFS= read -r p; do
        [ -n "$p" ] && tmux kill-pane -t "$p" 2>/dev/null
      done
      continue
    fi
    keeper=$(printf '%s\n' "$sidebars" | sed 's/%//' | grep -E '^[0-9]+$' | sort -n | head -n1)
    if [ -z "$keeper" ]; then
      add_window "$win"
    else
      keeper="%$keeper"
      printf '%s\n' "$sidebars" | while IFS= read -r p; do
        [ -n "$p" ] && [ "$p" != "$keeper" ] && tmux kill-pane -t "$p" 2>/dev/null
      done
      resize_window "$win"
    fi
  done
}

reconcile() {
  is_on || return 0
  reconcile_mobile
  if is_mobile; then
    kill_all       # phone: no sidebar, the switch popup replaces it
    return 0
  fi
  reconcile_sidebars
}

sync_width() {  # $1 = source tmux-lasso pane id
  is_on || return 0
  is_mobile && return 0
  pane="$1"
  [ -n "$pane" ] || return 0
  [ "$(tmux display-message -p -t "$pane" '#{@tmux_lasso_pane}' 2>/dev/null)" = "1" ] || return 0
  width=$(tmux display-message -p -t "$pane" '#{pane_width}' 2>/dev/null)
  is_uint "$width" || return 0
  width=$(desktop_width "$width")
  tmux set -g @tmux_lasso_width "$width"
  start_daemon    # self-heal: make sure the reconciler is alive
  reconcile       # one writer applies the new width everywhere
}

current_session() {
  s=$(tmux display-message -p '#{client_session}' 2>/dev/null)
  [ -n "$s" ] || s=$(tmux display-message -p '#{session_name}' 2>/dev/null)
  printf '%s\n' "$s"
}

window_work_cwd() {  # $1 = window id; cwd of its active non-sidebar pane
  win="$1"
  [ -n "$win" ] || return 0
  sep=$(printf '\037')
  tmux list-panes -t "$win" \
    -F "#{pane_active}${sep}#{@tmux_lasso_pane}${sep}#{pane_current_path}" 2>/dev/null \
    | awk -F "$sep" '
        $2 != "1" {
          if (first == "") first = $3
          if ($1 == "1") { print $3; found = 1; exit }
        }
        END { if (!found && first != "") print first }
      '
}

work_cwd() {  # $1 = session; cwd of its active non-sidebar pane
  sess="$1"
  [ -n "$sess" ] || return 0
  win=$(tmux display-message -p -t "$sess" '#{window_id}' 2>/dev/null)
  window_work_cwd "$win"
}

space_key() {  # $1 = cwd; git root when available, else the cwd itself
  cwd="$1"
  [ -n "$cwd" ] || return 0
  root=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null)
  [ -n "$root" ] && printf '%s\n' "$root" || printf '%s\n' "$cwd"
}

space_last_index() {  # $1 = session, $2 = cwd
  sess="$1"
  cwd="$2"
  key=$(space_key "$cwd")
  [ -n "$sess" ] && [ -n "$key" ] || return 0
  sep=$(printf '\037')
  tmux list-windows -t "$sess" -F "#{window_index}${sep}#{window_id}" 2>/dev/null \
    | while IFS="$sep" read -r idx wid; do
        wcwd=$(window_work_cwd "$wid")
        [ "$(space_key "$wcwd")" = "$key" ] && printf '%s\n' "$idx"
      done \
    | tail -n1
}

compact_space() {  # $1 = optional session, $2 = optional cwd
  sess="$1"
  [ -n "$sess" ] || sess=$(current_session)
  [ -n "$sess" ] || return 0
  cwd="$2"
  [ -n "$cwd" ] || cwd=$(work_cwd "$sess")
  key=$(space_key "$cwd")
  [ -n "$key" ] || return 0
  sep=$(printf '\037')
  tmux list-windows -t "$sess" -F "#{window_index}${sep}#{window_id}" 2>/dev/null \
    | while IFS="$sep" read -r _idx wid; do
        wcwd=$(window_work_cwd "$wid")
        [ "$(space_key "$wcwd")" = "$key" ] || continue
        if [ -z "$anchor" ]; then
          anchor="$wid"
        else
          tmux move-window -a -s "$wid" -t "$anchor" 2>/dev/null || true
          anchor="$wid"
        fi
      done
  tmux move-window -r -t "$sess" 2>/dev/null || true
}

new_window() {  # $1 = optional session
  sess="$1"
  [ -n "$sess" ] || sess=$(current_session)
  [ -n "$sess" ] || return 0
  cwd=$(work_cwd "$sess")
  compact_space "$sess" "$cwd"
  after=$(space_last_index "$sess" "$cwd")
  win=""
  if [ -n "$cwd" ] && [ -n "$after" ]; then
    win=$(tmux new-window -a -t "$sess:$after" -c "$cwd" -P -F '#{window_id}' 2>/dev/null)
  elif [ -n "$cwd" ]; then
    win=$(tmux new-window -t "$sess" -c "$cwd" -P -F '#{window_id}' 2>/dev/null)
  fi
  [ -n "$win" ] || win=$(tmux new-window -t "$sess" -P -F '#{window_id}' 2>/dev/null)
  [ -n "$win" ] && add_window "$win"
}

# --- legacy hook purge ------------------------------------------------------
remove_hooks() {
  # tmux-lasso no longer installs tmux hooks; the reconciler daemon polls instead.
  # We still unbind the [42] hooks here so a reload from an older, hook-driven
  # build purges that stale behaviour from the running tmux server.
  tmux set-hook -gu 'after-new-window[42]'  2>/dev/null
  tmux set-hook -gu 'after-new-session[42]' 2>/dev/null
  tmux set-hook -gu 'after-select-window[42]' 2>/dev/null
  tmux set-hook -gu 'client-resized[42]' 2>/dev/null
  tmux set-hook -gu 'client-attached[42]' 2>/dev/null
  tmux set-hook -gu 'after-resize-pane[42]' 2>/dev/null
  tmux set-hook -gu 'after-select-pane[42]' 2>/dev/null
  tmux set-hook -gu 'after-split-window[42]' 2>/dev/null
}

start_daemon() {
  # Detached singleton; daemon.py's flock makes a duplicate start a no-op, so
  # it's safe to call this from every enable/sync without first checking.
  nohup python3 "$DAEMON" >/dev/null 2>&1 </dev/null &
}

enable() {
  tmux set -g @tmux_lasso_on on
  remove_hooks     # purge any stale [42] hooks left by an older hook-driven build
  start_daemon
  reconcile        # instant: don't wait for the daemon's first tick
}

disable() {
  tmux set -g @tmux_lasso_on off    # the daemon's loop sees this and exits
  remove_hooks
  tmux set -g @tmux_lasso_mobile 0
  kill_all
}

case "${1:-toggle}" in
  enable)      enable ;;
  disable)     disable ;;
  startup)     enable ;;
  reconcile)   reconcile ;;
  add-window)  add_window "$2" ;;
  compact-space) compact_space "$2" ;;
  new-window)  new_window "$2" ;;
  sync-width)  sync_width "$2" ;;
  toggle|"")   if is_on; then disable; else enable; fi ;;
  __selftest)  # pure smoke checks, no tmux side effects (run by test_toggle.py)
    fail=0
    [ "$(desktop_width 5)" = "$MIN_SIDEBAR_WIDTH" ] \
      || { echo "desktop_width: width floor broken" >&2; fail=1; }
    [ "$(desktop_width 99)" = "99" ] \
      || { echo "desktop_width: valid width should pass through" >&2; fail=1; }
    [ "$fail" = 0 ] && echo "toggle.sh selftest ok"
    exit "$fail" ;;
  *)           exit 0 ;;
esac
