
# klorb

klorb is an agent harness for coding and other tasks.

If you are reading this, you are helping to extend and modify this harness. Requests from
the user will refer to tools like the BashTool, or prompts, tool call responses, etc. These
are not referring to *your* tools, prompts, or responses: they are referring to the Klorb
codebase, which you have access to here. You are not asked to reconfigure yourself on-the-fly;
you are asked to extend another agent's reach by improving the harness codebase.

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

Various bugs or  planned tasks are enumerated in `/TODO.md`. New follow-up tasks may be added there,
but if a task is **completed**, do not mark it complete -- remove it entirely!

Before declaring your own task complete, check whether `/TODO.md` already has a bullet describing
it (the task you were asked to do may have started life as a TODO item). If it does, remove that
bullet entirely as part of finishing the task -- don't leave a stale entry for work that's done.

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
* `vscode-plugin` - Plugin for VSCode to use the Klorb harness. See "vscode-plugin source tree"
  below for how its source is organized.

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
* Do not import protected methods from other modules, except for testing. If you see a line
  like `from foo import _bar`, that's a sign that `_bar` should be explictly made public as `bar`.
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
* Method docstring comments should succinctly describe the *purpose* of the method. Why would
  someone else call this method? Do not narrate every step it performs; the code does that
  implicitly. If the reasoning behind some code is not obvious, add a regular comment at that
  specific point in the method body explaining what it does, why, or why at that particular point.
  If you edit a method or function, you almost *never* need to make its docstring longer.
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
  * State your own invariants; don't cite another class's behavior as evidence for them. `"raises
    ValueError if session is None, the same contract BashTool._execute_persistent enforces"` is
    dumping "how klorb works" research into a comment — the reader doesn't need or want a pointer
    to an unrelated tool's implementation to understand *this* one's precondition. Only reference
    another symbol when it names the actual call/construction flow that leads to *this* code
    running (e.g. "constructed fresh by each tool's `apply()`, mirroring how `ToolRegistry.
    instantiate_tool()` builds a `Tool`" is fine — it's describing this object's own lifecycle,
    not borrowing a justification from elsewhere).
  * Prefer `map()`/`filter()` (wrapped in `list()`/`set()` as needed) over bracket-notation
    comprehensions for a plain transform-only or filter-only list/set build — e.g.
    `list(filter(lambda x: x.is_open, items))` over `[x for x in items if x.is_open]`. A
    comprehension that combines a transform *and* a filter (or builds a dict) is fine as-is;
    forcing that into nested `map(filter(...))` calls is less readable, not more.

### Important SDLC CI/CD commands

*Always* run lint, typecheck, and test through the Makefile. Do not run pyflakes, mypy,
pytest, prettier, etc. directly!

Here are the officially-sanctioned CI commands:

* use `make lint` for linting.
  * Within the `vscode-plugin/` dir, use `make lint_fix` to reformat files and attempt to
    auto-fix issues identified by `eslint`.
* use `make typecheck` for typechecking.
* use `make test` to invoke test suites.

*Where* you run these commands is important:

* When working on the agent / harness itself, the ACP server, or the TUI, run in the `klorb/`
  subdir, or from the root with `make -C klorb <target>`.
* When working on the VSCode plugin, run in the `vscode-plugin/`
  subdir, or from the root with `make -C vscode-plugin <target>`.
* If you edit `TODO.md` or any documentation (specs, ADRs, etc), run `make lint_docs` from
  the root directory to run markdownlint.

### Import Rules

* Only use relative imports within the same feature or module.
* Use absolute imports for other features or modules within the codebase.
* If possible, put imports at the top of the file or module. Do not use
  inline imports within a method body unless absolutely required to break
  a detected circular import.
* Use `isort`-compatible import order

### vscode-plugin source tree

`vscode-plugin/src/` is split by JavaScript runtime, not by feature, at the top level:

* `src/host/` — extension-host code (runs under Node, `require()`d by VS Code). The activation
  entry point (`extension.ts`, matching `package.json`'s `main`) stays directly under `src/`,
  sibling to `host/`, the same way the webview's entry point (`main.tsx`) stays directly under
  `src/webview/` rather than nested in a feature.
* `src/webview/` — webview UI code (runs in a sandboxed `vscode-webview://` document; React).
* `src/shared/` — types/utilities included by both the host and webview tsconfigs
  (`tsconfig.json` and `tsconfig.webview.json`) — e.g. the host↔webview message protocol.
* `types/` — ambient `.d.ts` declarations (e.g. the vendored `vscode-elements` JSX typings).
* `test/` mirrors the `src/` tree file-for-file (`test/host/`, `test/webview/`, `test/shared/`),
  including the `features/` nesting described below.

`src/webview/tsconfig.json` and `test/webview/tsconfig.json` are tiny pointer files
(`{"extends": "../../tsconfig.webview.json"}`) that exist purely so VS Code's editor tooling
picks the right project: it only auto-discovers a file literally named `tsconfig.json` by
walking up from whatever file is open, so without these, opening a file under `src/webview/` or
`test/webview/` would find the *host* `tsconfig.json` (which excludes that subtree entirely) and
fall back to an "orphan file" with no `paths` aliases at all — the actual `tsc`/`tsgo`/`esbuild`
invocations always pass `-p tsconfig.webview.json` (or `-p ./`) explicitly, so these two files
are never referenced by any script and exist only for the editor's benefit.

Within `src/host/` and `src/webview/`, most code lives under a `features/<name>/` folder
(`src/webview/features/history/`, `src/host/features/acp/`, ...), following the "bulletproof
react" style: a feature's `index.ts` is the *only* module anyone outside that feature may
import — never deep-import a file from inside another feature
(`webview/features/history/historyModel` from outside `features/history/` is wrong; import
`webview/features/history` and let its `index.ts` re-export what's needed). This is enforced by
`eslint.config.mjs`'s `no-restricted-imports` rule. Inside a feature, organize submodules
however the feature needs (`components/`, `hooks.ts`/`hooks/`, `types.ts`/`types/`, or plain
files) — the barrel is what's contractual, not the internal shape. A top-level `src/webview/
components/` and `src/webview/hooks/` (outside any `features/` folder) hold pieces that are
genuinely universal across features (e.g. `VsCodeApiProvider`/`useVsCodeApi`), not specific to
one.

Every tsconfig (`tsconfig.json` for the host, `tsconfig.webview.json` for the webview) declares
`paths` aliases rooted at `src/`: `shared/*`, plus `host/*` (host tsconfig only) or `webview/*`
(webview tsconfig only) — never both in the same config, since the host and webview must not
import each other's code. Applying this repo's general Import Rules (above) to vscode-plugin
specifically: relative imports (`./foo`, `../foo`) are reserved for imports between files inside
the *same* `features/<name>/` folder; every other import — including between two top-level,
non-feature files in the same directory — uses the rooted alias form (`import PromptInput from
'webview/components/PromptInput'`, not `'./components/PromptInput'`). `vitest.config.mts` uses
the `vite-tsconfig-paths` plugin (pointed at both tsconfigs via its `projects` option) so tests
resolve the same aliases; adding a new subtree under `test/` also requires adding it to the
matching tsconfig's `include` (see that file's comments) or the alias won't resolve for tests
rooted there.

React component/hook files (not plain utility/model modules like `historyModel.ts` or
`keyHandling.ts`, which keep named exports) export their component or hook as `export default`.
A feature's `index.ts` barrel re-exports a default-exported piece by name (`export { default as
HistoryView } from './components/HistoryView';`) so consumers still get a named import from the
barrel.
