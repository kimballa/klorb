# CLI package layout

## Summary

`klorb.cli` (`klorb/src/klorb/cli/`) is a package: `main.py` holds the default (no-subcommand)
entry point — parse a one-shot `-m`/`--message` prompt or start the interactive REPL — and
each `klorb <subcommand>` gets its own sibling module. This keeps `main()` itself from
accumulating every subcommand's argument parsing and business logic as new subcommands are
added. See [[session-and-turns]] for what `main()` does with the flags it parses.

## How it works

* `klorb/src/klorb/cli/main.py` holds `build_parser()` (the top-level, no-subcommand
  `argparse.ArgumentParser`) and `main()`, the function registered as the `klorb` console
  script (`klorb.cli.main:main` in `pyproject.toml`). `main()` checks `sys.argv[1]` against
  each subcommand name before running its own `argparse` parser, so a subcommand can't be
  confused with a one-shot prompt's own text — see [[klorb-init]] for why this check has to
  happen ahead of normal parsing.
* Each subcommand lives in its own module, named after the subcommand
  (`klorb/src/klorb/cli/init.py`, `system_prompt.py`, `models.py`, `show_config.py`,
  `server.py`), and exports a `build_<name>_parser()` and a `run_<name>_cli(argv: list[str])
  -> int` — the pair `main()` dispatches to. A subcommand module imports whatever library code
  it needs directly; it does not go through `main.py`.
* `klorb/src/klorb/cli/_common.py` holds values shared across more than one of these
  modules: the subcommand name constants `main()`'s dispatch checks against, and
  `TABLE_GUTTER`, the column spacing both `klorb models`' table and `klorb system-prompt`'s
  tool token-count table render with.
* `klorb/src/klorb/cli/__init__.py` re-exports every subcommand's `build_<name>_parser`/
  `run_<name>_cli`, `main`, `build_parser`, and the subcommand name constants, so other code
  (tests, or a caller driving a subcommand programmatically) can import them from `klorb.cli`
  directly instead of reaching into a specific submodule.

## Adding a subcommand

* Add a new module `klorb/src/klorb/cli/<name>.py` with its own `build_<name>_parser()` and
  `run_<name>_cli(argv)`.
* Add its dispatch name to `_common.py` and a matching `sys.argv[1] ==
  <NAME>_SUBCOMMAND` check plus `raise SystemExit(run_<name>_cli(sys.argv[2:]))` call in
  `main()`, following the existing subcommands' pattern.
* Re-export `build_<name>_parser`/`run_<name>_cli` from `__init__.py`.
* Mention the subcommand in `build_parser()`'s epilog and in `docs/user/usage.md` — see
  docs/adrs/00096-mention-subcommands-in-main-argparser-epilog.md.
* Add `klorb/tests/klorb/cli/test_<name>.py`, patching names at `klorb.cli.<name>.*` (the
  module the code under test actually imports them into), not `klorb.cli.*`.
