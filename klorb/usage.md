# KLORB(1)

## NAME

klorb - send a prompt to a model via OpenRouter, or start an interactive REPL

## SYNOPSIS

`klorb` [`-m` *PROMPT* | `--message` *PROMPT*] [`--model` *MODEL*]
[`--session-log` | `--no-session-log`]

## DESCRIPTION

klorb is an agent harness. Invoked with `-m`/`--message`, it sends a single
prompt to a model via OpenRouter and prints the response to stdout. Invoked
with no `-m`/`--message` flag, it starts an interactive, full-screen terminal
REPL instead.

## OPTIONS

* `-m` *PROMPT*, `--message` *PROMPT*

  The prompt to send to the model. If omitted, klorb starts the interactive
  REPL instead of sending a one-shot prompt.

* `--model` *MODEL*

  OpenRouter model identifier to use (e.g. `anthropic/claude-3.5-sonnet`).
  Defaults to `openai/gpt-4o-mini`.

* `--session-log`, `--no-session-log`

  Write (or skip) a per-session log file under `$KLORB_STATE_DIR/session-logs/`.
  Defaults to on in the REPL and off for a one-shot prompt (see ENVIRONMENT
  below for `KLORB_STATE_DIR`); use `--no-session-log` to disable it in the
  REPL, or `--session-log` to enable it for a one-shot prompt.

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

## SEE ALSO

`docs/specs/openrouter-prompt-client.md`, `docs/specs/terminal-repl.md`,
`docs/specs/paths-and-logging.md`
