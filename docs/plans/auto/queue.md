# Auto queue

Tasks for software-factory mode (see `docs/specs/software-factory.md`). Each top-level bullet
below is one self-contained task an agent can pick up on its own branch. Headings, prose, and
blank lines are not tasks — only unindented `-`/`*` bullets are.

Besides this file, any other `.md`/`.txt` file placed directly under `docs/plans/auto/` is also
picked up, as a single whole-file task.

* [documentation] The top-level README.md should do a markdown img embed of
  docs/assets/klorb-tui.png so users can see a screenshot of the Klorb session.

* [harness bug] In the klorb Python harness, `KLORB_CONFIG_DIR`/`KLORB_STATE_DIR`/
  `KLORB_DATA_DIR` (defined in `klorb/src/klorb/paths.py`) are resolved from `os.environ`
  eagerly at module import time. `klorb.cli.main()` calls `load_dotenv()` afterward
  (`klorb/src/klorb/cli.py`), so a `.env` file can never override these three paths — only a
  real shell/process env var can. Fix so these three paths are resolved after dotenv loading
  (e.g. lazily, or by having `cli.main()` re-resolve them once `load_dotenv()` has run), so
  `.env`-based overrides work the same way they do for other config.

* [TUI feature] Add a "Rename Session" command-palette action to the klorb TUI (see
  `klorb/src/klorb/tui/commands/session_commands.py`, which already has a
  `SessionCommandProvider` and a modal for picking among sessions) that lets the user change
  the current session's title.

* [TUI feature] In the klorb TUI's workspace trust prompt (see
  `klorb/src/klorb/tui/commands/trust_commands.py` and
  `klorb/src/klorb/workspace/trust_manager.py`), when querying the user about workspace trust,
  list any workspace skills that are already auto-allowed by config, so the user can see what
  they'd be trusting before confirming.

* [harness/subagents feature] In klorb's subagent group mechanism, notify all subagents in a
  group when a new subagent is created or removed from the group, and broadcast active/idle
  state changes to the group. Today the `AgentGroup` interjection (see
  docs/specs/chainlink-task-tracking.md's "AgentGroup interjection" section) is a one-shot
  snapshot sent only on a subagent's first turn, so it goes stale the moment group membership
  or activity changes afterward — this needs an ongoing update mechanism, not just the initial
  snapshot.
