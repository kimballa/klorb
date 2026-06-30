# Filesystem paths and logging setup

## Summary

klorb defines a small set of conventional filesystem locations (config, data, state) and a
single entry point that configures process-wide logging on top of them. This is
framework-level: any future feature that needs a place to read/write config, persist data, or
emit log output builds on `klorb.paths` and `klorb.logging_config` rather than hardcoding
paths or calling `logging.basicConfig` itself.

## How it works

* `klorb.paths` (`klorb/src/klorb/paths.py`) defines three directory constants, computed once
  at import time:
  * `KLORB_CONFIG_DIR` — defaults to `~/.config/klorb`, overridable via the `KLORB_CONFIG_DIR`
    environment variable.
  * `KLORB_DATA_DIR` — defaults to `~/.local/share/klorb`, overridable via `KLORB_DATA_DIR`.
  * `KLORB_STATE_DIR` — defaults to `~/.local/state/klorb`, overridable via `KLORB_STATE_DIR`.
  * `SESSION_LOGS_DIR` — always `KLORB_STATE_DIR / "session-logs"`.
  * Each env var, where set, is interpreted as a filesystem path and takes precedence over
    the default. See [the XDG-style directories ADR](
    ../adrs/use-xdg-style-dirs-overridable-by-klorb-env-vars.md) for why these are
    klorb-specific env vars rather than the shared `XDG_*` ones.
* `klorb.logging_config` (`klorb/src/klorb/logging_config.py`) exposes
  `configure_logging() -> Path`, which:
  * Creates `SESSION_LOGS_DIR` if it doesn't already exist.
  * Builds a session log file path of the form `yyyy-mm-dd-hh-mm-<nonce>.log`, where
    `<nonce>` is a two-word kebab-case slug from `coolname.generate_slug(2)` (e.g.
    `2026-06-30-15-38-festive-frog.log`).
  * Calls `logging.basicConfig(level="NOTSET", handlers=[TextualHandler(), FileHandler(...)],
    force=True)`, so every log record (at every level) reaches both the Textual console (or
    stderr, when no Textual app is running) and the session log file.
  * Returns the session log file's path, so callers can report it (e.g. for users debugging a
    run). See [the logging handler ADR](
    ../adrs/log-via-textualhandler-and-session-file-with-coolname-nonce.md) for why this
    combination of handlers and naming scheme was chosen.
* `klorb.cli.main()` (`klorb/src/klorb/cli.py`) calls `configure_logging()` immediately after
  `load_dotenv()`, so a `.env` file can supply `KLORB_STATE_DIR` (or the other `KLORB_*`
  directory env vars) before logging is set up. Modules across the codebase obtain a
  `logging.Logger` the standard way (`logging.getLogger(__name__)`) and log through it; no
  module other than `klorb.logging_config` calls `logging.basicConfig`.

## Configuration

* `KLORB_CONFIG_DIR`, `KLORB_DATA_DIR`, `KLORB_STATE_DIR` — override the corresponding
  default directory. Read once, at process start (when `klorb.paths` is first imported).

## Out of scope

* Log rotation/pruning of old files under `session-logs/` is not implemented; every run
  leaves its log file in place indefinitely.
* `KLORB_CONFIG_DIR` and `KLORB_DATA_DIR` are defined but nothing yet reads or writes to
  them — they exist so future features (e.g. persisted settings, cached data) have a
  conventional location to build on.
