
# klorb

klorb is an agent harness for coding and other tasks.

If you are reading this, you are helping to extend and modify this harness. The user will refer to
tools like BashTool, or prompts, tool call responses, etc. These are not referring to *your* own
environment: they refer to the Klorb codebase, which you have access to. Do not try to reconfigure
yourself on-the-fly; extend another agent's reach by improving the harness codebase.

## Docs

All feature tasks must have a spec. Specs are written in docs/specs/. They explain how the feature
works and how it's built. Especially important you write one for internal platform features.

Don't create new spec files if an existing spec can be revised or extended. For incremental work,
make incremental edits to the corresponding spec. Search for a file that already covers the area
you're touching.

Code comments or docstrings must *never* reference any file in `docs/plans/`. Capture important
durable explanations in `docs/specs/` or an ADR.

### Architecture decision records (ADRs)

Key decisions are captured in architecture decision records (ADRs). ADRs
are short documents that record a decision, with the format:

* date and time
* question
* answer
* reasoning

ADRs are stored in docs/adrs/.

ADR filenames are sequentially numbered by creation date: `NNNNN-slug-name.md` (e.g.
`00042-edit-file-auto-creates-via-empty-subject-insert-shape.md`). When creating a new ADR, find
the highest existing number and use the next one. Use `ls docs/adrs/ | tail -1` to find it.

ADR filenames should have a reasonable slug and include the answer (`00123-do-foo-by-using-bar.md`) for
quick filename access, not wasteful filler words (`00123-how-should-we-do-foo.md`).

ADRs are not specs that are kept up-to-date as the code changes; they represent a decision made
at a point in time. If some part of the decision is superseded then (1) create a new,
higher-numbered ADR, recording the new decision, and (2) add a **short** note at the top of the
older ADR explaining that it (or parts of it) are superseded by the new one, with a reference to the
new ADR file.

### TODO.md

Various bugs or planned tasks are enumerated in `/TODO.md`. Add new follow-up tasks there.

If a task is **completed**, do not mark it complete -- remove it entirely!

Before declaring your own task complete, check whether `/TODO.md` already has an item you can remove.

## Subprojects

The Klorb project is organized as a collection of subprojects:

* `klorb/` - python library that is the actual harness itself. Everything that the system can
  "do", is done here. Also includes CLI tools, TUI, and ACP server for harness/plugin communication.
  Written in python. Enforce a a strict firewall where the actual agentic logic is all in "library"
  code that can be invoked headless, tui, or over remote ACP connection. Keep agent functionality
  reachable from `Session`; don't pollute the Session with TUI- or ACP-specific connection. Use a
  callback instead.
* `vscode-plugin` - Plugin for VSCode to use the Klorb harness. See the `vscode-plugin-architecture`
  skill for how it's organized.

## Rules for development

### General principles

* Start all new or blank files with a copyright header:
  * `# © Copyright <current year> Aaron Kimball` in python,
  * `// © Copyright <current year> Aaron Kimball` in javascript/typescript/react.
  * Do not modify existing copyright header or license information.
* Use explicit typing as often as possible. At minimum, every method and method return type must be declared.
  * Python methods that return nothing explicitly declare `-> None`.
  * For Typescript methods, `: void`.
* Encapsulate related state and behavior in a class, even for only one instance
  (a singleton). No module-level mutable variables + functions that read/write
  them (Function/var declarations outside a class); wrap them in a class, with private
  (`_`-prefixed) attributes and public methods. Reach a singleton instance through an
  accessor function. Do not import the module global directly. Don't return a
  bare tuple of unnamed fields (dicts, primitives, other tuples) from a
  function when those fields are conceptually one thing — make a small class (a pydantic
  `BaseModel` or a `@dataclass`) instead of positional tuple unpacking. See
  `.claude/skills/encapsulate-in-classes/SKILL.md`.
* Name a method/field after what it does or holds, not after whichever caller happens to use it
  today. Name it for its effects, not its current consumer.
* Do not import protected methods, except for testing. If a foreign protected method must be
  imported, consider refactoring to make that method public.
* When adding new functionality, if it's very similar to another existing piece of functionality, consider
  refactoring the existing code to admit the additional use case. Creating methods or classes / interfaces
  that are clones or near-clones of one another is a bad architectural practice. Doing this right may
  mean expanding the set of files touched.
* When revising or refactoring, make the smallest code change necessary to effect the change. But **do** do
  what needs to be done to actually make the change.
* Do not make unrelated changes while revising or refactoring a file.
* Data access or mutation possible by multiple threads should be done through well-defined methods (not direct field access) that enforce synchronization or locking. Multi-step read-analyze-write or multiple-read field use must have locking applied to the end-to-end process. True even in python - the GIL is insufficient to synchronize multi-threaded programs!
* Do not try to be an auto-formatter or lint tool. Use `make lint_fix` or other tools configured in this repository.
* When possible, reuse existing API endpoints rather than make new ones.
* Never duplicate a constant (a magic number, hardcoded string, default value, etc.) across files.
  * Define the constant in one canonical location and have every consumer import it from
    there.
  * If a circular import, fix the import direction or hoist the constant into a small shared module (e.g.
    `foo_constants.py`) that both sides can depend on without a cycle.
* Work is not done until, at minimum, all existing tests pass.
  * For nontrivial improvements, add new unit tests; those must also pass.
  * If a test fails, the likely reason is because an application change caused a regression.
  * It is less likely but not impossible that the test should be modified to pass given the updated application
    source. Be explicit in your output to me when you have modified tests in this way.
* Add `logger.debug()` calls at consequential moments: creating or removing files, registering
  cleanup handlers, granting permissions, spawning subprocesses, and similar state-changing
  operations. Err on the side of debug logging more than feels necessary. This is distinct from
  user-facing info/warning/error logs, which should stay reserved for what a user actually needs to
  see.
* Prefer `map()`/`filter()` (wrapped in `list()`/`set()` as needed) over bracket-notation
    comprehensions for a plain transform-only or filter-only list/set build — e.g.
    `list(filter(lambda x: x.is_open, items))` over `[x for x in items if x.is_open]`.

### Comments and docstrings

* Do not add comments or docstrings that reference TODO.md or its contents.
  * If there's a specific incomplete case or follow-up tied to the exact line or method you're
    writing, say so directly inline: `TODO(aaron): <specific, self-contained description of what
    still needs to happen here>`.
  * Always put an owner in a TODO comment: `TODO(aaron): ...`, not `TODO: ...`
* Docstrings and comments must describe the code as a frozen snapshot: how and why it currently
  works, never how it changed. Don't write "the old heuristic", "previously", "no longer", "this
  replaces/fixes/regresses X", "unlike before", or similar diff-against-history framing - that goes
  stale. This applies to docs/specs/ too: specs describe current behavior, not history.
* Method docstring comments should succinctly describe the *purpose* of the method.
  Do not narrate every step it performs. Do not list its callers, or more than 2 examples of something.
  * For how non-obvious code works, add a regular comment at that
    specific point in the method body explaining what it does or why.
  * If you edit a method or function, you *rarely* need to make its docstring longer.
* Default to no comment; add one only when the WHY is genuinely non-obvious. 1-2 sentences.
* Existing comments and docstrings are **much** too long! That history is not license to keep doing
  it. Existing lengthy comments are a mistake. Follow these rules, not the example in the code.
* Never narrate what a method *isn't* doing, alternatives it doesn't take, or where else some
  concern is handled. State what the code does, not a tour of the design space around it.
* If a rationale needs more than a sentence or two, put it in a spec or ADR, not the docstring.
* This especially applies to code review responses and to docstrings on methods that are
  straightforward once named well: don't pre-empt questions they might not even ask.
* State your invariants; don't cite another class's behavior as explanations for them.
  `"raises ValueError if session is None, the same contract BashTool._execute_persistent enforces"`
  is bad. Make explanations self-contained. Good: `"raises ValueError if session is None."`
* Do **not** enumerate every item a rule applies to! If a comment needs to illustrate that a rule
  covers several things (config keys, call sites, directories...), name *one or two* concrete
  examples and stop. Some lists are already too long. Do not follow their example or make them
  longer.
* Never repeat an explanation. If you have already stated *why* something is anywhere in the same
  file, docstring, or commit, do not restate it at the next place it comes up.
* In a docstring, when you put a long dash (" -- "), think about whether what follows would actually
  meaningfully contribute to the understanding of the current method. If it says "mirroring Foo"
  or otherwise restates existing information, just drop it.

### Json file format and style

Any JSON file klorb writes must include a `schema: {name, version}` envelope to detect out-of-date
persisted data. See `docs/specs/persisted-json-schema-versioning.md` for the convention and shared
implementation. But do not explicitly bump a schema version or add code for backward compatibility
unless explicitly requested by the user.

User-facing, hand-authored config file keys (`klorb-config.json`) use dot-delineated,
lowerCamelCase namespacing (`thinking.effort`, `terminal.input.maxLines`) - same vibe as
VSCode's and Claude Code's own settings files - not the snake_case used for internal Python
identifiers. See `docs/specs/process-and-session-config.md`: "On-disk key naming".

### Important SDLC CI/CD commands

*Always* run lint, typecheck, and test through the Makefile. Do not run pyflakes, mypy,
pytest, prettier, etc. directly!

Here are the officially-sanctioned CI commands:

* use `make lint` for linting.
  * Within `vscode-plugin/`, use `make lint_fix` to reformat files and
    auto-fix issues identified by `eslint`.
* use `make typecheck` for typechecking.
* use `make test` to invoke test suites; `make TEST_SUITE=<pytest -k expr> test` restricts the
  run to matching tests (see `klorb/README.md`'s "Testing" section).

*Where* you run these commands is important:

* When working on the agent / harness itself, the ACP server, or the TUI, run in the `klorb/`
  subdir, or from the root with `make -C klorb <target>`.
* When working on the VSCode plugin, run in the `vscode-plugin/`
  subdir, or from the root with `make -C vscode-plugin <target>`.
* If you edit `TODO.md` or any documentation (specs, ADRs, etc), run `make lint_docs` from
  the root directory to run markdownlint.

The full `klorb` test suite takes a few minutes, so within a dev loop run `make
TEST_SUITE=<keyword> test` against the suite(s) covering the code you're touching, and save one
unscoped `make test` for the end, before declaring the task done.

### Import rules

* Only use relative imports within the same feature or module.
* Use absolute imports for other features or modules within the codebase.
* If possible, put imports at the top of the file or module. Do not use
  inline imports within a method body unless absolutely required to break
  a detected circular import.
* Use `isort`-compatible import order

### vscode-plugin source tree

See the `vscode-plugin-architecture` skill (`.claude/skills/vscode-plugin-architecture/SKILL.md`)
for how `vscode-plugin/src/` and `vscode-plugin/test/` are organized: the host/webview/shared
split, the `features/<name>/` barrel pattern, tsconfig path aliases, and the React
default-export convention.
