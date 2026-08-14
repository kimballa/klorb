# Auto queue

Tasks for software-factory mode (see `docs/specs/software-factory.md`). Each top-level bullet
below is one self-contained task an agent can pick up on its own branch. Headings, prose, and
blank lines are not tasks — only unindented `-`/`*` bullets are.

Besides this file, any other `.md`/`.txt` file placed directly under `docs/plans/auto/` is also
picked up, as a single whole-file task.

* TUI (Bug): The skill fuzzy finder shows the name and namespace for skills in the panel as
  soon as you type `/` but the description of each skill only shows up once you start typing
  more. These descriptions should be shown next to the skills.