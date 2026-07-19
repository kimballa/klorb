
# klorb

klorb is an agent harness for coding and other tasks.

## docs

All feature tasks must have a spec. Specs are written in docs/specs/. They explain
how the feature works and how it's built. These are especially important for framework-like
features that other features are built upon.

Prefer updating and embellishing an existing spec over creating a new file when you add to or
rework a feature incrementally. Search docs/specs/ for a file that already covers the area
you're touching (e.g. a tool's existing behavior spec) before writing a new one — folding new
semantics into the existing spec keeps one current-state document per feature area instead of
scattering related facts across several. Only start a new spec file for a genuinely new
feature that isn't an extension of something already documented.

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
* `vscode-plugin` - Plugin for VSCode to use the Klorb harness. (Not yet implemented)

## rules for development

### General Software Development Principles

* Start all new or blank files with a copyright header:
  * `# © Copyright <current year> Aaron Kimball` in python,
  * `// © Copyright <current year> Aaron Kimball` in javascript/typescript/react.
  * The current year is 2026.
  * Do not modify any existing copyright header or license information.
* It is important to use explicit typing as often as possible. At minimum, every method
  argument and method return type must be declared.
  * In python, methods that return nothing should explicitly `-> None`.
  * Typescript methods without any return value should explicitly `: void`.
* Encapsulate related state and behavior in a class, even when there's only ever one instance
  (a singleton). Avoid module-level mutable globals paired with free functions that read/write
  them (`global` statements outside a class); wrap them in a class instead, with private
  (`_`-prefixed) attributes and public methods, and reach the one shared instance through a
  single accessor function rather than importing the module global directly. Avoid returning a
  bare tuple of unrelated/loosely-related values (dicts, primitives, other tuples) from a
  function when those values are conceptually one thing — give it a small class (a pydantic
  `BaseModel` or a `@dataclass`) with named fields instead of positional tuple unpacking. See
  `.claude/skills/encapsulate-in-classes/SKILL.md` for the checklist and worked examples this
  rule expands into. `klorb.models.registry.ModelRegistry`/`klorb.tools.registry.ToolRegistry`
  are existing examples of the class-based shape to follow for a stateful registry.
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
* Add `logger.debug()` calls around consequential actions and workflows: creating or removing
  files/directories, registering cleanup handlers (`atexit`, etc.), granting or widening
  permissions, spawning subprocesses or sessions, and similar state-changing or multi-step
  operations. Err on the side of logging more of these than feels necessary — they're what makes
  a failure or a surprising side effect diagnosable after the fact, and `debug` level keeps them
  out of the way otherwise. This is distinct from user-facing `logger.info()`/`logger.warning()`
  calls, which should stay reserved for what a user actually needs to see.
* Default to no comment; add one only when the WHY is genuinely non-obvious. Keep it to a
  sentence or two.
  * Don't narrate what a method *isn't* doing, alternatives it doesn't take, or where else a
    concern is handled instead ("this doesn't do X because Y handles it in Z" style asides).
    State what the code does, not a tour of the design space around it.
  * If the rationale needs more than a sentence or two, that's a sign it belongs in a spec or
    ADR, not the docstring — write it there and leave a short pointer (`see docs/specs/foo.md`)
    in the code instead of inlining it.
  * This applies double to code review responses and to docstrings on methods that are
    straightforward once named well: prefer trusting the reader over pre-empting every question
    they might not even ask.

### Important SDLC CI/CD commands

*Always* run lint, typecheck, and test through the Makefile. Do not run pyflakes, mypy,
or pytest directly!

Here are the officially-sanctioned CI commands:

* use `make lint` for linting.
* use `make typecheck` for typechecking.
* use `make test` to invoke test suites.
* These are run in the `klorb/` subdir, or from the root with `make -C klorb <target>`

### Import Rules

* Only use relative imports within the same feature or module.
* Use absolute imports for other features or modules within the codebase.
* If possible, put imports at the top of the file or module. Do not use
  inline imports within a method body unless absolutely required to break
  a detected circular import.
* Use `isort`-compatible import order
