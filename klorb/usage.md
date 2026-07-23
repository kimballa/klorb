# KLORB(1)

## NAME

klorb - send a prompt to a model via OpenRouter, or start an interactive REPL

## SYNOPSIS

`klorb` [`-m` *PROMPT* | `--message` *PROMPT*] [`--model` *MODEL*] [`--config` *FILE*]
[`--interactive` | `--no-interactive`] [`--session-log` | `--no-session-log`]
[`-y` | `--auto-approve`] [`--log-tool-calls` | `--no-log-tool-calls`]
[`--max-tool-calls-per-turn` *N*] [`--max-tool-calls-per-session` *N*]
[`-V` | `--version`]

`klorb init` [`--system` | `--user`] [`--force`]

`klorb system-prompt` [`--role` *ROLE*] [`--model` *MODEL*] [`--config` *FILE*]

`klorb models` [`--json`] [`--brief`] [`--costs`]

`klorb show-config` [`--config` *FILE*]

`klorb server` [`--config` *FILE*]

## DESCRIPTION

klorb is an agent harness. Invoked with `-m`/`--message` and no explicit
`--interactive` flag, it sends a single prompt to a model via OpenRouter and
prints the response to stdout. Invoked with no `-m`/`--message` flag, it
starts an interactive, full-screen terminal REPL instead. Passing
`--interactive` together with `-m`/`--message` starts the REPL with that
message submitted as the first turn, then keeps the REPL open for more.

Invoked as `klorb init` (only recognized when `init` is the very first
argument), it instead bootstraps a `klorb-config.json` file and a `klorb`
Invoked as `klorb system-prompt` (only recognized when `system-prompt` is the
very first argument), it dumps the resolved system prompt and tool definitions
to stdout — see COMMANDS below. Invoked as `klorb models` (only recognized
when `models` is the very first argument), it lists every model klorb has
discovered — see COMMANDS below. Invoked as `klorb show-config` (only
recognized when `show-config` is the very first argument), it prints the
merged config from all config files to stdout as pretty-printed JSON — see
COMMANDS below. Invoked as `klorb server` (only recognized when `server` is
the very first argument), it instead runs a persistent process that reads
newline-delimited JSON commands from stdin and writes newline-delimited JSON
replies to stdout — see COMMANDS below.

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
* `system-prompt` [`--role` *ROLE*] [`--model` *MODEL*] [`--config` *FILE*]

  Dumps the resolved system prompt and tool definitions to stdout, with a
  token-count summary at the bottom. Output is plain text with
  markdown-style section headers separating the default system prompt
  (`default_sys.md`), the role-specific addendum (inside an `<AgentRole>`
  tag), and the pretty-printed tool-definition JSON the model receives.
  `--role` concretizes the system prompt for a specific operating role
  (defaults to `operator`, the same role a default session runs as).
  `--model` resolves model-specific prompt tiers (defaults to the model
  configured via the `klorb-config.json` file stack). `--config` layers an
  additional config file on top of the usual `/etc`, per-user, and
  per-project files. Exit status is `0` on success.
* `models` [`--json`] [`--brief`] [`--costs`]

  Lists every model `ModelRegistry` discovers (built-in and user-added, see
  `docs/specs/model-framework.md`), sorted by name. With no flags, prints a
  column-aligned table (no vertical borders, a single horizontal rule under
  the header row) of each model's name, family, version, and capabilities.
  `--json` instead emits a JSON array of each model's data. `--brief` emits
  only each model's OpenRouter name and no other fields, one per line as
  plain text; combined with `--json`, it instead emits a JSON array of name
  strings (e.g. `["xiaomi/mimo-v2.5", ...]`). `--costs` looks up each
  model's current per-token cost from OpenRouter (live, not stored — see
  `docs/adrs/fetch-model-pricing-live-not-from-json.md`), throttled to
  `klorb.models.openrouter_pricing.MAX_PRICING_REQUESTS_PER_SECOND` requests
  per second, and folds it into the table (as an `IN $/MTOK`/`OUT $/MTOK`
  column pair) or the `--json` output (as a `costs` key); it's ignored with
  `--brief`. Exit status is always `0`.
* `show-config` [`--config` *FILE*]

  Prints the merged config from all config files (built-in defaults, `/etc`,
  per-user, per-project, and `--config`) to stdout as pretty-printed JSON.
  The workspace is resolved non-interactively via `TrustManager`: if the
  current directory is a trusted workspace, the per-project config layer is
  included; otherwise it's skipped. `--config` layers an additional config
  file on top of the usual stack. Exit status is `0` on success.
* `server` [`--config` *FILE*]

  Runs a persistent process that reads newline-delimited JSON (JSONL)
  command records from stdin, one line at a time, and writes a JSONL reply
  record to stdout for each — flushed immediately, so a caller reading
  stdout sees each reply as soon as it's produced. `{"greet": "someName"}`
  replies `{"message": "hello, someName!"}`; `{"action": "shutdown"}` stops
  the loop with no reply; any other record gets `{"error": "..."}`.
  Stops (exit status `0`) on a shutdown command, on stdin reaching EOF, or on
  `SIGINT` — see `docs/specs/klorb-server.md`. `--config` layers an
  additional config file on top of the usual `/etc`, per-user, and
  per-project files; the resolved config isn't consumed by the server loop
  itself yet, but a malformed `--config` file is still surfaced as a
  warning.

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

* `-y`, `--auto-approve`

  Auto-approve every tool-permission "ask" verdict for this run (sets
  `permissionFramework` to `auto`, in-memory only, for the rest of the
  session). Without this flag, `permissionFramework` is `ask` (show the
  interactive modal) when the session is interactive, or `deny` (fail closed)
  for a one-shot prompt. See `docs/specs/permissions.md`.

* `--log-tool-calls`

  Append every tool call's request and response to `$KLORB_STATE_DIR/tool-calls.log`
  (default `~/.local/state/klorb/tool-calls.log`), creating it (or appending to it,
  if it already exists) as needed. Defaults to off; also enabled by the `LOG_TOOL_CALLS`
  environment variable (`1` or `true`) or the `tools.logCalls` config key,
  independently of this flag. `--no-log-tool-calls` forces logging off,
  overriding those other sources. See `docs/specs/tool-call-logging.md`.

* `--max-tool-calls-per-turn` *N*

  Override the max tool calls allowed in a single turn before it fails.
  Defaults to the configured/process value (see
  `docs/specs/process-and-session-config.md`).

* `--max-tool-calls-per-session` *N*

  Override the max tool calls allowed across the whole session before a turn
  fails. Defaults to the configured/process value.

* `-V`, `--version`

  Print the klorb version number and exit.

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
  (`$KLORB_STATE_DIR/session-logs/`) and the `--log-tool-calls` audit file
  (`$KLORB_STATE_DIR/tool-calls.log`). Defaults to `~/.local/state/klorb`.

* `LOG_TOOL_CALLS`

  Set to `1` or `true` to enable tool-call logging when neither `--log-tool-calls`
  nor `tools.logCalls` is set; an explicit flag or config key overrides this.
  See `docs/specs/tool-call-logging.md`.

## FILES

### Config file stack

klorb merges `klorb-config.json` files in increasing order of precedence:

1. Built-in defaults (`klorb.resources/default-config.json`) — shipped inside
   every klorb install, never missing.
2. `/etc/klorb/klorb-config.json` (or the file named by `$KLORB_ETC_CONFIG`).
3. `$KLORB_CONFIG_DIR/klorb-config.json` (default `~/.config/klorb/klorb-config.json`).
4. `<workspace>/.klorb/klorb-config.json` — only read when the workspace is trusted.
5. `--config` on the command line, if given.

Later layers override earlier ones. Each layer may contain a `sessionDefaults` key
with session-scoped settings nested inside it.

See `docs/specs/process-and-session-config.md` for the full config schema and
`docs/specs/persisted-json-schema-versioning.md` for the on-disk format.

### Context instruction files

klorb reads project-supplied instruction files into the model's context on the
very first turn of a trusted workspace. Files are read in priority order (highest
first); the model uses the priority tag to decide which file wins on conflict:

1. `$KLORB_CONFIG_DIR/INSTRUCTIONS.md` — durable, per-install instructions
   (default `~/.config/klorb/INSTRUCTIONS.md`).
2. `<workspace>/.klorb/INSTRUCTIONS.md` — durable per-project instructions kept
   alongside `klorb-config.json`.
3. `<workspace>/AGENTS.md` — General root-level convention.
4. `<workspace>/CLAUDE.md` — only when `compatibility.claudeMarkdown` is `true`
   in the config file stack.

None of the workspace files are read (or even stat-ed) when the workspace is untrusted.
See `docs/specs/workspace-context-files.md` for details.

## EXAMPLES

Start the interactive REPL with the default model:

```bash
klorb
```

Send a single prompt and print the response:

```bash
klorb -m "What is the capital of France?"
```

Send a single prompt to a specific model:

```bash
klorb --model anthropic/claude-3.5-sonnet --message "Summarize this repo."
```

Start the REPL without writing a session log:

```bash
klorb --no-session-log
```

Send a one-shot prompt and also write a session log:

```bash
klorb --session-log -m "What is 2+2?"
```

Start the REPL with a starting message, then keep chatting:

```bash
klorb -m "What is 2+2?" --interactive
```

Start the REPL with settings from an extra config file, on top of the usual
`/etc`, per-user, and per-project `klorb-config.json` files:

```bash
klorb --config ./ci-defaults.json
```

Send a one-shot prompt that auto-approves any tool-permission "ask" verdict
it hits, without an interactive prompt to confirm it:

```bash
klorb -y -m "Clean up this directory."
```

Bootstrap a per-user config file and `~/.local/bin/klorb` symlink:

```bash
klorb init
```

Send a one-shot prompt and record every tool call to `tool-calls.log`:

```bash
klorb --log-tool-calls -m "List the files in this directory."
```

Force tool-call logging off even though `LOG_TOOL_CALLS=1` is set in the
environment:

```bash
klorb --no-log-tool-calls -m "List the files in this directory."
```

Dump the resolved system prompt and tool definitions for the default
operator role:

```bash
klorb system-prompt
```

Dump the system prompt for a different role and model:

```bash
klorb system-prompt --role auditor --model openai/gpt-5-nano
```

List every discovered model as a table:

```bash
klorb models
```

List just the model names, for scripting:

```bash
klorb models --brief
```

List every model as JSON, with live per-token pricing:

```bash
klorb models --json --costs
```

Show the merged config from all config files:

```bash
klorb show-config
```

Show the merged config with an extra config file layered on top:

```bash
klorb show-config --config ./ci-defaults.json
```

Run the persistent JSONL server, greet it, then ask it to shut down:

```bash
klorb server
{"greet": "Ada"}
{"action": "shutdown"}
```

## SEE ALSO

`docs/specs/openrouter-prompt-client.md`, `docs/specs/terminal-repl.md`,
`docs/specs/paths-and-logging.md`, `docs/specs/session-and-turns.md`,
`docs/specs/process-and-session-config.md`,
`docs/specs/persisted-json-schema-versioning.md`, `docs/specs/klorb-init.md`,
`docs/specs/tool-call-logging.md`, `docs/specs/model-framework.md`,
`docs/specs/klorb-server.md`
