# KLORB(1)

## NAME

klorb - send a prompt to a model via OpenRouter, or start an interactive REPL

## SYNOPSIS

`klorb` [`-m` *PROMPT* | `--message` *PROMPT*] [`--model` *MODEL*] [`--config` *FILE*]
[`--interactive` | `--no-interactive`] [`--session-log` | `--no-session-log`]

`klorb init` [`--system` | `--user`] [`--force`]

## DESCRIPTION

klorb is an agent harness. Invoked with `-m`/`--message` and no explicit
`--interactive` flag, it sends a single prompt to a model via OpenRouter and
prints the response to stdout. Invoked with no `-m`/`--message` flag, it
starts an interactive, full-screen terminal REPL instead. Passing
`--interactive` together with `-m`/`--message` starts the REPL with that
message submitted as the first turn, then keeps the REPL open for more.

Invoked as `klorb init` (only recognized when `init` is the very first
argument), it instead bootstraps a `klorb-config.json` file and a `klorb`
executable symlink — see COMMANDS below and `docs/specs/klorb-init.md`.

## COMMANDS

* `init` [`--system` | `--user`] [`--force`]

  Writes the packaged reference `klorb-config.json` into place and creates a
  `klorb` executable symlink pointing at the currently-running launcher
  script. `--user` (the default unless running as root) targets
  `$KLORB_CONFIG_DIR/klorb-config.json` and `~/.local/bin/klorb`; `--system`
  (must be run as root) targets `/etc/klorb/klorb-config.json` and
  `/usr/bin/klorb`. Each of the two targets is left alone with a stderr
  message if it already exists, unless `--force` is given, in which case the
  existing file/symlink is replaced. Progress and diagnostics are printed to
  stderr; exit status is `0` if both steps ran or were skipped as
  already-done, `1` on a real failure (e.g. `--system` run as a non-root
  user, or a permission error). Also reachable from the interactive REPL as
  the `Init local klorb config` command palette entry (always `--user`
  scope) — see `docs/specs/klorb-init.md`.

## OPTIONS

* `-m` *PROMPT*, `--message` *PROMPT*

  The prompt to send to the model. If omitted, klorb starts the interactive
  REPL instead of sending a one-shot prompt.

* `--model` *MODEL*

  OpenRouter model identifier to use (e.g. `anthropic/claude-3.5-sonnet`).
  Defaults to `openai/gpt-5-nano`, unless overridden by a `klorb-config.json`
  file (see `docs/specs/process-and-session-config.md`).

* `--config` *FILE*

  Path to an additional `klorb-config.json`-shaped file, layered on top of
  the `/etc`, per-user, and per-project config files described in
  `docs/specs/process-and-session-config.md`. Not required; klorb runs fine
  with no config files at all.

* `--interactive`, `--no-interactive`

  Stay in the interactive REPL, submitting `-m`/`--message`'s prompt as the
  first turn if one was given. Defaults to true; defaults to false when
  `-m`/`--message` is given without an explicit `--interactive`/
  `--no-interactive` flag (preserving the one-shot behavior above).
  `--no-interactive` without `-m`/`--message` is a usage error, since there
  would be nothing to send.

* `--session-log`, `--no-session-log`

  Write (or skip) a per-session log file under `$KLORB_STATE_DIR/session-logs/`.
  Defaults to on when interactive and off for a one-shot prompt (see
  ENVIRONMENT below for `KLORB_STATE_DIR`); use `--no-session-log` to disable
  it in the REPL, or `--session-log` to enable it for a one-shot prompt.

## ENVIRONMENT

* `OPENROUTER_API_KEY`

  Required. The OpenRouter API key used to authenticate outgoing requests.
  klorb loads a `.env` file in the working directory (via `python-dotenv`)
  before reading this, so it may also be set there instead of the shell
  environment.

* `KLORB_CONFIG_DIR`

  Overrides klorb's config directory. Defaults to `~/.config/klorb`.

* `KLORB_DATA_DIR`

  Overrides klorb's data directory. Defaults to `~/.local/share/klorb`.

* `KLORB_STATE_DIR`

  Overrides klorb's state directory, under which session logs are written
  (`$KLORB_STATE_DIR/session-logs/`). Defaults to `~/.local/state/klorb`.

## EXAMPLES

Start the interactive REPL with the default model:

```
klorb
```

Send a single prompt and print the response:

```
klorb -m "What is the capital of France?"
```

Send a single prompt to a specific model:

```
klorb --model anthropic/claude-3.5-sonnet --message "Summarize this repo."
```

Start the REPL without writing a session log:

```
klorb --no-session-log
```

Send a one-shot prompt and also write a session log:

```
klorb --session-log -m "What is 2+2?"
```

Start the REPL with a starting message, then keep chatting:

```
klorb -m "What is 2+2?" --interactive
```

Start the REPL with settings from an extra config file, on top of the usual
`/etc`, per-user, and per-project `klorb-config.json` files:

```
klorb --config ./ci-defaults.json
```

Bootstrap a per-user config file and `~/.local/bin/klorb` symlink:

```
klorb init
```

## SEE ALSO

`docs/specs/openrouter-prompt-client.md`, `docs/specs/terminal-repl.md`,
`docs/specs/paths-and-logging.md`, `docs/specs/session-and-turns.md`,
`docs/specs/process-and-session-config.md`,
`docs/specs/persisted-json-schema-versioning.md`, `docs/specs/klorb-init.md`
