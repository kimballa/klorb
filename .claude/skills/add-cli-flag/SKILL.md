---
name: add-cli-flag
description: Add a new command-line flag to the klorb CLI (klorb/src/klorb/cli.py). Use when the user wants to add, wire up, or document a new --flag for klorb, especially one that should be threaded through SessionConfig/Session so library code (TUI, future VSCode plugin) can see it. Also covers updating klorb/usage.md to document the new flag.
---

# Adding a CLI flag to klorb

klorb's CLI/library firewall (see `CLAUDE.md`) means `klorb/src/klorb/cli.py` may only
parse arguments and translate them into library calls — it must not carry agentic state of
its own. In practice that means: a flag that affects session *behavior* (not just how the
CLI parses argv) belongs on `SessionConfig` (`klorb/src/klorb/session.py`), and `cli.py`'s
job is to resolve the parsed flag to a value and hand it to `SessionConfig(...)`. Follow
these steps in order.

## 1. Add the argparse argument

In `build_parser()` in `klorb/src/klorb/cli.py`:

* Use a kebab-case flag name (`--my-flag`) with a snake_case `dest=`.
* For booleans, use `action=argparse.BooleanOptionalAction` so both `--my-flag` and
  `--no-my-flag` exist for free — this is the existing pattern for `--session-log` and
  `--interactive`. Set `default=None` (not `True`/`False`) whenever you need to distinguish
  "the user didn't pass this flag" from "the user explicitly chose the default value" —
  `--interactive`'s defaulting logic (see `main()`) only works because unset is `None`,
  distinct from an explicit `--no-interactive`.
* Write a one-line `help=` string that states the default and what overrides it; this is
  the only place flag help text lives — don't duplicate it in a separate `--help` handler.

## 2. Decide: does this flag belong on SessionConfig?

Ask: does any code outside `cli.py` need this value (the REPL, `Session`, a future VSCode
plugin)? If yes, it belongs on `SessionConfig`. Almost every flag that isn't purely about
*how argv gets parsed* qualifies — `model`, `interactive`, and `log_filename` are the
existing examples. If the flag is genuinely CLI-only (e.g. it only affects how output is
formatted on stdout and nothing downstream ever reads it), it can stay local to `main()`.

When it does belong on `SessionConfig`:

* Add the field in `klorb/src/klorb/session.py` with an explicit type annotation and a
  sensible default (pydantic `BaseModel` fields, per `CLAUDE.md`'s typing rule — every
  field needs a declared type).
* In `cli.main()`, resolve any defaulting logic that depends on *other* flags (like
  `--interactive`'s dependence on whether `--message` was given) into a plain local
  variable first, then pass that into `SessionConfig(...)`.
* Nothing outside `cli.py` should ever read `args.<flag>` or touch `argparse.Namespace` —
  every other module (`klorb.session`, `klorb.tui.repl`, etc.) depends on `SessionConfig`/
  `Session` only.
* If `Session` needs to *act* on the new field (not just store it), add or update a method
  on `Session` that reads `self.config.<field>` — see `Session.active_model_name()` for the
  pattern of resolving a config value into behavior.

## 3. Update `klorb/usage.md`

This file is klorb's single source of CLI documentation — don't leave a flag undocumented
or duplicate its docs elsewhere:

* Add the flag to the `## SYNOPSIS` line.
* Add a `* `--flag` *VALUE*` (or `* `--flag`, `--no-flag`` for booleans) entry under
  `## OPTIONS` explaining what it does and its default.
* If it changes user-facing behavior, add or extend an example under `## EXAMPLES`.
* If it adds a new environment variable, document it under `## ENVIRONMENT` too.

## 4. Update specs/ADRs if this is framework-level

Per `CLAUDE.md`, framework-like features need a spec under `docs/specs/`. A flag that's a
small addition to already-documented behavior just needs the relevant spec's
`## How it works`/`## Usage` sections updated (e.g. `docs/specs/session-and-turns.md` for
anything touching `SessionConfig`, `docs/specs/terminal-repl.md` for REPL-visible
behavior). A flag whose default required a real design decision (e.g. why `--interactive`
defaults to following `--message`'s presence) may warrant a short ADR under `docs/adrs/`
recording the question/answer/reasoning.

## 5. Add/update tests in `klorb/tests/test_cli.py`

* Patch `klorb.cli.Session` (and `klorb.cli.run_repl` when the flag can route to the REPL)
  so tests never make real network or Textual-UI calls.
* Assert on the `SessionConfig` actually passed to `Session(...)`, via
  `mock_session_cls.call_args.args[0]`, rather than re-parsing `sys.argv` yourself.
* Cover at minimum: the flag's default, its explicit positive setting, its explicit
  negative setting (for booleans), and any interaction with other flags' defaulting logic
  (mirroring the `--interactive`/`--message` interaction tests already in that file).

## 6. Verify

Run `make -C klorb lint typecheck test` from the repo root (a single command — don't chain
it with `cd klorb &&`, see `CLAUDE.md`'s bash rules) and confirm it's clean before
considering the flag done.

## Worked example: `--interactive`

The `--interactive`/`--no-interactive` flag added alongside this skill is a complete
worked example of every step above:

* `build_parser()` adds it as a `BooleanOptionalAction` with `default=None`.
* `main()` resolves it (`args.prompt is None if args.interactive is None else
  args.interactive`) before constructing `SessionConfig`, and calls `parser.error(...)` for
  the invalid `--no-interactive` + no `--message` combination.
* `SessionConfig.interactive: bool` carries the resolved value into `Session`; `cli.main()`
  branches on it to choose `session.run_one_shot(prompt)` vs. `run_repl(session,
  initial_message=prompt)`.
* `klorb/usage.md` documents it in the synopsis, an `## OPTIONS` entry, and a new example.
* `docs/specs/session-and-turns.md` documents the defaulting rule; the ADR
  `docs/adrs/route-one-shot-and-repl-prompts-through-a-shared-session.md` records why a
  shared `Session` was needed once this flag made "one-shot" and "REPL" no longer mutually
  exclusive at the CLI's call site.
* `klorb/tests/test_cli.py` has dedicated tests for the default-with-message,
  explicit-true-with-message, and explicit-false-without-message cases.
