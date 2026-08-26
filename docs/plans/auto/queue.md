# Auto queue

Tasks for software-factory mode (see `docs/specs/software-factory.md`). Each top-level bullet
below is one self-contained task an agent can pick up on its own branch. Headings, prose, and
blank lines are not tasks — only unindented `-`/`*` bullets are.

Besides this file, any other `.md`/`.txt` file placed directly under `docs/plans/auto/` is also
picked up, as a single whole-file task.

* In the VSCode plugin, the PostChat/ReadChat tools should pretty-print nicely with a
  `<detail>` that can unfold. The tool chip should have a "chat bubble" logo (like the speech
  bubble in a comic strip). The detail for ReadChat should truncate at 4 messages or 8 lines
  with a "..." on a line by itself afterward, if truncated.
