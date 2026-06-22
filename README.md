# Lasso

An always-on sidebar pane in every tmux window, auto-sized to the client width
and hidden on phone-width clients. Toggle with `prefix + g`.

No Claude Code hooks, no `settings.json` edits — Lasso reads session state by
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
run-shell ~/.tmux/plugins/tmux-lasso/lasso.tmux
```

## Config

```tmux
set -g @lasso_key g            # toggle key (prefix + g)
set -g @lasso_width 18         # sidebar columns (also LASSO_WIDTH / LASSO_MIN_WIDTH env)
set -g @lasso_mobile_width 90  # below this client width: hide sidebar (tap top bar to switch) (also LASSO_MOBILE_WIDTH env)
```

## Tests

```sh
python3 -m py_compile *.py && sh -n toggle.sh lasso.tmux
python3 -m unittest discover -p 'test_*.py'
```

## License

[MIT](LICENSE)
