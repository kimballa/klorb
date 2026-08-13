# Auto queue

Tasks for software-factory mode (see `docs/specs/software-factory.md`). Each top-level bullet
below is one self-contained task an agent can pick up on its own branch. Headings, prose, and
blank lines are not tasks — only unindented `-`/`*` bullets are.

Besides this file, any other `.md`/`.txt` file placed directly under `docs/plans/auto/` is also
picked up, as a single whole-file task.

* (TUI) The `/skill` fuzzy-finder currently just concatenates the skill-name and description into
  one big string. The description must be in a severely muted color so it doesn't look like too
  much clash of info and a single run-on line but two very visually distinct fields per line.
