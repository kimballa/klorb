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
  of both the CLI and the TUI. `run_init()` runs three independent, idempotent steps: two
  scoped to either `"system"` or `"user"`, and a third, unscoped step:
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
  * Each of these two scoped steps is a no-op if its target already exists — it returns a
    single "already exists; not overwriting" message and leaves the target untouched — unless
    `force=True`, in which case it removes the existing file/symlink first (with its own
    preceding warning message) before writing the fresh one.
  * `copy_tiktoken_cache()` recursively copies the packaged `tiktoken-cache` resource tree
    (`klorb.resources/tiktoken-cache/`, one subdirectory per tiktoken encoding name — today,
    only `o200k_base`) into `klorb.token_estimate.tiktoken_cache_target_dir()`
    (`$KLORB_DATA_DIR/tiktoken-cache`), creating it as needed, via
    `klorb.token_estimate.install_tiktoken_cache()`. Not scoped to `"system"`/`"user"` and has
    no `force` gate: `$KLORB_DATA_DIR` has no `"system"`-scope counterpart, and it's package
    data the running klorb version ships with (not something a user hand-edits), so it's
    cheap and safe to re-copy on every `klorb init` run. See [[paths-and-logging]] for
    `$KLORB_DATA_DIR` and `configure_tiktoken_cache_env()`'s call below for how this copy gets
    used.
  * `run_init(scope, force=...)` runs all three steps in that order and returns their
    combined, ordered progress messages. It refuses a `"system"` scope outright, before any
    step runs, unless the process's effective uid is `0` (raises `InitError`). Any step's own
    `InitError` (e.g. a permission-denied `mkdir`) also propagates immediately out of
    `run_init`, stopping the remaining steps — but a scoped step's own "already exists"
    outcome is not an error and never blocks the others.
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
* `klorb.tui.commands.init_commands.InitCommandProvider` offers "Init local klorb config" via the
  command palette (`Ctrl+P`, or `>init` narrowed from the prompt — see
  [[command-palette-from-prompt]]), always running `run_init("user", force=False)` for the
  REPL's own user and reporting the outcome via `App.show_notice()` — appending an `.error`
  item to the history scroll if `run_init` raised `InitError`, otherwise a `.notice` item with
  the join of its progress messages (see [[avoid-toasts-prefer-history-notices]]). It's
  registered in `ReplApp.COMMANDS` alongside `ModelCommandProvider`/`SessionCommandProvider`/
  `ThinkingCommandProvider`. It yields no hits at all when
  `klorb.klorb_init.is_user_scope_initialized()` is `True` (both the user-scope config file
  and the `klorb` symlink already exist on disk), since `run_init("user", force=False)`
  would be a pure no-op in that case — the command hides itself rather than offering a
  confusing "already exists" notice. `is_user_scope_initialized()` reuses
  `config_target_path("user")` and `symlink_target_dir("user")`, and its symlink-existence
  test mirrors `create_symlink`'s own (`is_symlink() or exists()`), so the two agree on
  what "already there" means.
* `ReplApp.on_mount()` mounts a `Static` (CSS class `notice`) into the `#history` scroll the
  first time the REPL starts, if `klorb.process_config.user_config_path()` doesn't exist yet,
  reading `CONFIG_MISSING_MESSAGE`: "Klorb configuration file not found. Run `>Init local
  klorb config` to set up."
* `klorb.token_estimate.configure_tiktoken_cache_env()` runs once per process, always before
  either the REPL or a one-shot prompt can trigger a token estimate: `klorb.cli.main()` calls
  it directly for a one-shot prompt (right after `configure_logging()` runs, so its log
  message is actually visible on stderr), while `klorb.tui.ReplApp.on_mount()` calls it
  for an interactive session, once the Textual app itself is running — see
  docs/adrs/configure-tiktoken-cache-env-after-repl-app-mounts.md for why the REPL doesn't
  just call it from `main()` the same way. It checks whether `klorb.token_estimate.
  tiktoken_cache_encoding_dir()` (`$KLORB_DATA_DIR/tiktoken-cache/o200k_base`, populated by
  `copy_tiktoken_cache()` above) exists; if so, it sets the `TIKTOKEN_CACHE_DIR` environment
  variable to that directory and logs (`logger.info`) that it did, so `klorb.token_estimate.
  estimate_tokens()`'s first `tiktoken.get_encoding("o200k_base")` call reads the bundled BPE
  file from disk instead of downloading it from OpenAI's blob storage. A no-op — no env var
  set, nothing logged — on a fresh install that hasn't run `klorb init` yet; tiktoken falls
  back to its own default cache/download behavior in that case.

## Usage

```bash
klorb init                    # --user, unless running as root
klorb init --user
klorb init --system           # must be run as root; refuses otherwise
klorb init --force            # overwrite an existing config file/symlink instead of skipping it
```

Typing `>Init local klorb config` (or a narrowing prefix like `>init`) in the REPL prompt
runs the equivalent of `klorb init --user` for the signed-in user, reporting the result as a
history notice instead of on stderr.

## Out of scope

* Packaging never writes to `/etc/klorb` or `$KLORB_CONFIG_DIR` on its own — only running
  `klorb init` does, on demand. There is deliberately no install-time hook that runs it
  automatically; see [[ship-reference-klorb-config-as-package-data]] for why absolute
  install-time host-path placement isn't reliably available to a `pip`/`uv`-installed wheel
  in the first place.
* Per-project (`.klorb/`) bootstrapping and workspace trust prompts are a separate flow, built
  on top of this one's config-file layers — see [[projects-and-trust]].
