# tmux-lasso

An always-on sidebar pane in every tmux window, auto-sized to the client width
and hidden on phone-width clients. Toggle with `prefix + g`.

No Claude Code hooks, no `settings.json` edits — tmux-lasso reads session state by
scraping the panes, so install is just the plugin line below.

## Requirements

- tmux
- python3 (stdlib only — nothing to `pip install`)

## Install

### With [TPM](https://github.com/tmux-plugins/tpm) (recommended)

Add to `~/.tmux.conf`, then hit `prefix + I` to fetch:

```tmux
set -g @plugin 'tuanphungcz/tmux-lasso'
```

### Manual

```sh
git clone https://github.com/tuanphungcz/tmux-lasso ~/.tmux/plugins/tmux-lasso
```

Add to `~/.tmux.conf` and reload (`prefix + r` or `tmux source ~/.tmux.conf`):

```tmux
run-shell ~/.tmux/plugins/tmux-lasso/tmux-lasso.tmux
```

## Config

```tmux
set -g @tmux_lasso_key g            # toggle key (prefix + g)
set -g @tmux_lasso_new_window_key c # new tab key (prefix + c); set to off to keep tmux default
set -g @tmux_lasso_width 18         # sidebar columns (also TMUX_LASSO_WIDTH / TMUX_LASSO_MIN_WIDTH env)
set -g @tmux_lasso_mobile_width 90  # below this client width: hide sidebar (tap top bar to switch) (also TMUX_LASSO_MOBILE_WIDTH env)
```

## Sound on finish (optional)

Play a short sound when an agent changes state, so you know without watching the
screen. The daemon already tracks each session; it plays a sound on two edges
(fired detached, so the loop never stalls):

- **done** — an agent finished (`working → done`) → `@tmux_lasso_sound`
- **needs input** — an agent is blocked waiting on you → `@tmux_lasso_sound_request`

```tmux
set -g @tmux_lasso_announce on                                   # off by default
set -g @tmux_lasso_sound         ~/.config/tmux-lasso/done.mp3        # any file `afplay` can play
set -g @tmux_lasso_sound_request ~/.config/tmux-lasso/request.mp3
```

Unset sounds fall back to `/System/Library/Sounds/Glass.aiff`. Toggle live with
`tmux set -g @tmux_lasso_announce on|off`. Restart the daemon (toggle tmux-lasso off and
on) once after upgrading so the new code runs.

## Tests

```sh
python3 -m py_compile *.py && sh -n toggle.sh tmux-lasso.tmux
python3 -m unittest discover -p 'test_*.py'
```

## License

[MIT](LICENSE)
