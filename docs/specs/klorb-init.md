# `klorb init`

## Summary

`klorb init` bootstraps a `klorb-config.json` file and a `klorb` executable symlink, for
either the current user or the whole machine, so a fresh `pip`/`uv` install has a starting
config to edit and a stable `klorb` command on `$PATH` even when the install's own `bin/`
directory isn't. It's reachable as a CLI subcommand
(`klorb init [--system|--user] [--force]`) and, for the per-user case, from the command
palette ("Init local klorb config"). See [[process-and-session-config]] for the config file
layers this feeds and [[command-palette-from-prompt]] for the palette mechanism the TUI
command reuses.

## How it works

* `klorb.klorb_init` (`klorb/src/klorb/klorb_init.py`) holds the library logic, independent
  of both the CLI and the TUI, as two independent, idempotent steps, each scoped to either
  `"system"` or `"user"`:
  * `write_config_file(scope, force=...)` copies the packaged, spartan starter file
    `template-config.json` (`template_config_text()`, read from `klorb.resources` via
    `importlib.resources` — see [[ship-reference-klorb-config-as-package-data]] and
    [[split-packaged-config-into-default-and-template]]) to `config_target_path(scope)`:
    `klorb.process_config.etc_config_path()` (honoring `$KLORB_ETC_CONFIG`) for `"system"`,
    `klorb.process_config.user_config_path()` (honoring `$KLORB_CONFIG_DIR`) for `"user"` —
    the same two file locations `load_process_config()` reads back as config layers. Creates
    the target's parent directory first. `template-config.json` is a *different* packaged
    resource from `klorb.process_config`'s `default-config.json` (the always-merged,
    built-in-defaults config layer — see [[process-and-session-config]]): it's deliberately
    spartan, omitting most keys, since it's meant for a user to read and hand-edit, not to
    enumerate every recognized setting. `write_config_file` copies its text verbatim, with no
    parsing or schema validation — `klorb init` just places it on disk.
  * `create_symlink(scope, force=...)` creates a `klorb` symlink in `symlink_target_dir(scope)`
    (`/usr/bin` for `"system"`, `~/.local/bin` for `"user"`, created first if missing)
    pointing at `sys.argv[0]` resolved to an absolute path — whatever launcher script the
    running `klorb` process was actually invoked as (ordinarily the `pip`/`uv`-installed
    console-script shim).
  * Each step is a no-op if its target already exists — it returns a single "already exists;
    not overwriting" message and leaves the target untouched — unless `force=True`, in which
    case it removes the existing file/symlink first (with its own preceding warning message)
    before writing the fresh one.
  * `run_init(scope, force=...)` runs both steps in that order and returns their combined,
    ordered progress messages. It refuses a `"system"` scope outright, before either step
    runs, unless the process's effective uid is `0` (raises `InitError`). Either step's own
    `InitError` (e.g. a permission-denied `mkdir`) also propagates immediately out of
    `run_init`, so a failure in the config-file step means the symlink step never runs — but
    a step's own "already exists" outcome is not an error and never blocks the other step.
  * `default_scope()` returns `"system"` when the effective uid is `0`, `"user"` otherwise —
    the same check `run_init` itself uses to reject `"system"`, so the unqualified `klorb
    init` never picks a scope it would then immediately refuse.
* `klorb.cli.main()` recognizes `klorb init ...` only when `init` is literally `sys.argv[1]`
  — the very first argument — checked before the normal one-shot/REPL `argparse` parser ever
  runs, so it can't be confused with `init` appearing later in `argv` (e.g. as a one-shot
  prompt's own text, or a `--message` value). `klorb.cli.run_init_cli(argv)` parses
  `--system`/`--user` (a mutually exclusive pair; `default_scope()` picks one if neither is
  given) and `--force`, prints `run_init()`'s messages to stderr one per line, and returns `0`
  on success or `1` if `run_init` raised `InitError` (whose message is also printed to
  stderr). `klorb.cli.main()` calls `load_dotenv()` before this dispatch, so a `.env`-provided
  `$KLORB_ETC_CONFIG` is honored for `--system` the same way it already is for
  `load_process_config()` — `etc_config_path()` resolves it lazily at call time rather than
  caching it at import time. `$KLORB_CONFIG_DIR` does not get the same treatment (`user`
  scope reads `klorb.paths.KLORB_CONFIG_DIR`, a module-level constant computed at import
  time) — a pre-existing limitation tracked in `TODO.md`'s "Bugs" section, not something
  `klorb init` fixes on its own.
* `klorb.tui.init_commands.InitCommandProvider` offers "Init local klorb config" via the
  command palette (`Ctrl+P`, or `>init` narrowed from the prompt — see
  [[command-palette-from-prompt]]), always running `run_init("user", force=False)` for the
  REPL's own user and reporting the outcome via `App.notify()` — an `error`-severity toast if
  `run_init` raised `InitError`, otherwise the join of its progress messages. It's registered
  in `ReplApp.COMMANDS` alongside `ModelCommandProvider`/`SessionCommandProvider`/
  `ThinkingCommandProvider`.
* `ReplApp.on_mount()` mounts a `Static` (CSS class `notice`) into the `#history` scroll the
  first time the REPL starts, if `klorb.process_config.user_config_path()` doesn't exist yet,
  reading `CONFIG_MISSING_MESSAGE`: "Klorb configuration file not found. Run `>Init local
  klorb config` to set up."

## Usage

```
klorb init                    # --user, unless running as root
klorb init --user
klorb init --system           # must be run as root; refuses otherwise
klorb init --force            # overwrite an existing config file/symlink instead of skipping it
```

Typing `>Init local klorb config` (or a narrowing prefix like `>init`) in the REPL prompt
runs the equivalent of `klorb init --user` for the signed-in user, reporting the result as a
toast instead of on stderr.

## Out of scope

* Packaging never writes to `/etc/klorb` or `$KLORB_CONFIG_DIR` on its own — only running
  `klorb init` does, on demand. There is deliberately no install-time hook that runs it
  automatically; see [[ship-reference-klorb-config-as-package-data]] for why absolute
  install-time host-path placement isn't reliably available to a `pip`/`uv`-installed wheel
  in the first place.
* Per-project (`.klorb/`) bootstrapping and workspace trust prompts are a separate,
  not-yet-built flow — see `TODO.md`'s `PLAN-003` item.
