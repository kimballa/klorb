# Auto queue

Tasks for software-factory mode (see `docs/specs/software-factory.md`). Each top-level bullet
below is one self-contained task an agent can pick up on its own branch. Headings, prose, and
blank lines are not tasks — only unindented `-`/`*` bullets are.

Besides this file, any other `.md`/`.txt` file placed directly under `docs/plans/auto/` is also
picked up, as a single whole-file task.

* [harness/subagents feature] In klorb's subagent group mechanism, notify all subagents in a
  group when a new subagent is created or removed from the group, and broadcast active/idle
  state changes to the group. Today the `AgentGroup` interjection (see
  docs/specs/chainlink-task-tracking.md's "AgentGroup interjection" section) is a one-shot
  snapshot sent only on a subagent's first turn, so it goes stale the moment group membership
  or activity changes afterward — this needs an ongoing update mechanism, not just the initial
  snapshot.
