# Mention subcommands in the main argparser's `epilog`

* Date: 2026-07-08 00:00
* Question: `klorb init` (see [[klorb-init]]) is dispatched in `klorb.cli.main()` by a
  literal `sys.argv[1] == "init"` check ahead of the main `argparse` parser, rather than
  registered as an argparse subparser, so `klorb --help` lists only the one-shot/REPL flags
  and says nothing about `init`'s existence. How should a subcommand like this stay
  discoverable from the top-level help?
* Answer: Keep the ahead-of-argparse dispatch (it preserves the property that `init` is only
  recognized as the very first argument, never confused with a later flag value or prompt
  text), and advertise the subcommand in the main parser's `epilog` instead of converting it
  to an argparse subparser. `build_parser()` now passes
  `formatter_class=argparse.RawDescriptionHelpFormatter` with an `epilog` naming `init` and
  pointing at `klorb init --help`. The subcommand's own `--help` is already handled by its
  standalone `argparse.ArgumentParser` (`build_init_parser()`), so no custom `--help` logic
  is needed there — argparse auto-implements it.
* Reasoning: The dispatch-by-`argv[1]` design is documented in [[klorb-init]] as deliberate,
  and converting to `subparsers.add_parser` would either weaken that (subparsers accept the
  subcommand token anywhere the positional allows) or require extra `argparse` plumbing to
  preserve it. An `epilog` is the smallest change that surfaces the subcommand in
  `klorb --help` without touching dispatch. `RawDescriptionHelpFormatter` is used (rather
  than the default formatter) so the epilog's literal newlines render as a clean
  subcommand listing instead of being collapsed into wrapped prose; it leaves the existing
  option-help wrapping untouched. Going forward, any new `klorb <subcommand>` dispatched the
  same way should be added to this same `epilog`, and its own flags should continue to come
  from a standalone parser's auto-generated `--help` rather than a hand-rolled help string.
