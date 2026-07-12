
# klorb

klorb is an agent harness for coding and other tasks.

## docs

All feature tasks must have a spec. Specs are written in docs/specs/. They explain
how the feature works and how it's built. These are especially important for framework-like
features that other features are built upon.

Key architecture decisions are captured in architecture decision records (ADRs). ADRs
are short documents that record a decision, with the format:

* date and time
* question
* answer
* reasoning

ADRs are stored in docs/adrs/.

ADR filenames should have a reasonable slug (`do-foo-by-doing-bar.md`) so that useful
ADRs can be quickly accessed by agents just by the filename. Don't waste filename on
filler words (`should-we-do-foo.md`); try to include the answer.

Any JSON file klorb writes to disk that's meant to be read back later (config, saved
session state, etc.) must include a `schema: {name, version}` envelope so a later klorb
version can detect and upgrade an old file instead of misreading it. See
`docs/specs/persisted-json-schema-versioning.md` for the convention and the shared helper
that implements it.

User-facing, hand-authored config file keys (`klorb-config.json`) use dot-delineated,
lowerCamelCase namespacing (`thinking.effort`, `terminal.input.maxLines`) — the same vibe as
VSCode's and Claude Code's own settings files — not the snake_case used for internal Python
identifiers. See `docs/specs/process-and-session-config.md`'s "On-disk key naming" section.

## subprojects

The Klorb project is organized as a collection of subprojects:

* `klorb/` - python library that is the actual harness itself. Everything that the system can
  "do", is done here. Also includes the command-line interface.  Includes both a
  TUI for interactive use as well as the ability to run a prompt in headless
  mode. Written in python. The CLI code should have a strict firewall where the
  actual agentic logic is all in "library" code that can be invoked without any CLI / UI
  whatsoever (so that the VSCode plugin, or other mechanisms, can use it too). The CLI is
  included in the same python packages as the library logic for convenience and harmonized
  dependencies, but none of the agentic stuff should be directly intertwined in the CLI side.
* `vscode-plugin` - Plugin for VSCode to use the Klorb harness.

# rules for development

## General Software Development Principles

* Start all new or blank files with a copyright header:
  * `# © Copyright <current year> Aaron Kimball` in python,
  * `// © Copyright <current year> Aaron Kimball` in javascript/typescript/react.
  * The current year is 2026.
  * Do not modify any existing copyright header or license information.
* It is important to use explicit typing as often as possible. At minimum, every method
  argument and method return type must be declared.
  * In python, methods that return nothing should explicitly `-> None`.
  * Typescript methods without any return value should explicitly `: void`.
* When revising or refactoring, make the smallest code change necessary to effect the change.
* Do not make unrelated changes while revising or refactoring a file.
* Do not try to be an auto-formatter or lint tool. Use deterministic formatting and linting
  tools configured for use with this source repository to perform these operations.
* Do not delete comments unless the related code or logic is also deleted.
* Do not revise jsdoc comments or python docstrings for existing methods except to clarify
  newly-added functionality.
* When possible, try to reuse existing API endpoints rather than make new ones.
* Never duplicate a constant (a magic number, default value, etc.) across files as a
  workaround for a circular import or any other reason. Duplicated constants drift out of
  sync silently and are a form of tech debt. Instead:
  * Define the constant in one canonical location and have every consumer import it from
    there.
  * If a circular import is genuinely in the way, fix the import direction (the module that
    should own the constant usually shouldn't be the one importing from the module that
    merely consumes it), or hoist the constant into a small shared module (e.g.
    `foo_constants.py`) that both sides can depend on without a cycle.
  * Only duplicate a constant's value across files with the user's *explicit* permission for
    that specific case.
* Work is not done until, at minimum, all existing tests pass.
  * Ideally, for nontrivial improvements, new unit tests are also added to cover new
    functionality or bugfixes, and those must also pass.
  * If a test fails, consider that the most likely reason is because a change to the main
    application code caused a regression. Consider the source and fix the application.
  * It is less likely that the test should be modified to pass given the updated application
    source. Only make such a change after careful consideration, and be explicit in your
    output to me when you have modified tests in this way.
* Do not add comments or docstrings that reference TODO.md, or point at "an item"/"a bullet" in
  it, as a way of explaining why something is incomplete. TODO.md's bullets get reworded,
  reordered, and removed independently of the code, so a cross-reference like that goes stale
  silently and is hard to verify as fully scrubbed once the backlog item is actually done.
  * If there's a specific incomplete case or follow-up tied to the exact line or method you're
    writing, say so directly inline: `TODO(aaron): <specific, self-contained description of what
    still needs to happen here>`. It should make sense to a reader who has never opened TODO.md.
  * Don't use a bare `TODO:` (no owner) for this — always `TODO(aaron): ...`.
  * This doesn't apply to docs/specs/ or docs/adrs/ files, which are expected to narrate how a
    feature relates to backlog items as part of explaining the design.
* Docstrings and comments must describe the code as a static snapshot: how and why it currently
  works, never how it changed. Don't write "the old six-step chain", "previously", "no longer",
  "this replaces/fixes/regresses X", "unlike before", or similar diff-against-history framing —
  that phrasing is accurate only until the *next* change, at which point nothing updates it and
  it goes stale and misleading. This applies to docs/specs/ too: a spec may explain why a
  feature exists (including its relationship to a backlog item, per the TODO.md rule above),
  but should describe the resulting behavior as current fact, not narrate the diff from a prior
  version. Record change history — what changed, why, and what alternatives were rejected — in
  an ADR (docs/adrs/) instead; cross-reference it by name from the docstring/comment/spec if the
  current behavior's rationale needs a pointer.

## Important SDLC CI/CD commands

Always run lint, typecheck, and test through the Makefile. Don't freelance with pyflakes
or pytest.

Here are the officially-sanctioned CI commands:

* use `make lint` for linting.
* use `make typecheck` for typechecking.
* use `make test` to invoke test suites.

## Import Rules

* Only use relative imports within the same feature or module.
* Use absolute imports for other features or modules within the codebase.
* If possible, put imports at the top of the file or module. Do not use
  inline imports within a method body unless absolutely required to break
  a detected circular import.

## Cloud / Remote Agent Behavior

* The environment variable `CLAUDE_CODE_REMOTE` is set to the literal string `"true"` when
  Claude Code is running as a remote agent (e.g., a claude.ai cloud agent). It is unset or
  set to another value during interactive terminal sessions.
* When `CLAUDE_CODE_REMOTE=true`, submit completed work as a pull request using the `gh` CLI
  rather than presenting changes interactively:
  ```
  gh pr create --title "..." --body "..."
  ```
* **Never push directly to `main`.** Always work on a named feature branch and open a PR.
* When running as an interactive Claude Code terminal session (`CLAUDE_CODE_REMOTE` is not
  `"true"`), do **not** submit a PR automatically — present your changes to the user for review.

## Important Rules for using tools and bash shell commands

The following are **critical** instructions for invoking shell commands:

* It is important that you be able to operate autonomously. To do so, you must adhere to
  approved bash shell commands.
* All the commands necessary to perform the full software development / test / review loop
  are already pre-approved. You should not need per-tool-call approval from the user.
* Do not pipe the output of one command directly to another; doing so voids prior approval.
  (The following are examples of forbidden patterns: `command1 | grep <pattern>` or
  `command1 | jq <expr>`). Direct the output of `command1` in each case into a temp file
  and then read it into the second command from the file.
* Do not redirect stderr to stdout with `2>&1`. You can read both output streams.
* Do not use env variable substitution. This wastes time on automatic command approval.
* Do not quote special characters like `#` or `"` or `'` or `|`, as doing so voids prior
  command approval. Instead, write such expressions into a temp file and use files as
  arguments.
* Do not use subshells with `$(...)` or backtick-quoted strings as these void prior
  approval. Run the would-be subshell command first and save its output to a file, and
  then read it in to the chained command, or read the file yourself and reproduce the
  output in an environment variable for a second command if needed.
* Do not pipe commands into `tail` in order to save tokens. Commands required for
  SDLC verification generally produce minimal output beyond what you would otherwise need
  to read anyway.

Examples of GOOD bash commands:
* `make lint typecheck test`
* `make test`

Examples of BAD bash commands:
* `PYTHONPATH=./src:./tests venv/bin/pytest -q tests/ 2>&1 | tail -30`
* `make test | tail -30`

When you make up complex commands, you waste more time waiting for user approval than if
you had just stuck to using the pre-approved "make" commands, even if `make test`, etc,
would run a larger number of tests or typecheck more files than an alternative you can
generate.  CPU time is fast. User effort is slow. The user is very sad when you make him
proofread bash statements if a clean alternative was already provided for you.
