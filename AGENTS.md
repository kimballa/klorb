
# klorb

klorb is an agent harness for coding and other tasks.

If you are reading this, you are helping to extend and modify this harness. 
The user will refer to tools like BashTool, or prompts, tool call responses, etc. These
are not referring to *your* own environment: they refer to the Klorb
codebase, which you have access to. Do not try to reconfigure yourself on-the-fly;
extend another agent's reach by improving the harness codebase.

## docs

All feature tasks must have a spec. Specs are written in docs/specs/. They explain
how the feature works and how it's built. Especially important you write one for internal platform features.

Don't create new spec files if an existing spec can be revised or extended. For incremental work, make incremental edits to the corresponding spec. Search for a file that already covers the area you're touching.

Code comments or docstrings must *never* reference any file in `docs/plans/`. Capture important durable explanations in `docs/specs/` or an ADR.

Key decisions are captured in architecture decision records (ADRs). ADRs
are short documents that record a decision, with the format:

* date and time
* question
* answer
* reasoning

ADRs are stored in docs/adrs/.

ADR filenames should have a reasonable slug and include the answer (`do-foo-by-using-bar.md`) for quick filename access, not wasteful 
filler words (`how-should-we-do-foo.md`).

### TODO.md

Various bugs or planned tasks are enumerated in `/TODO.md`. Add new follow-up tasks there. 

If a task is **completed**, do not mark it complete -- remove it entirely!

Before declaring your own task complete, check whether `/TODO.md` already has an item you can remove.

## subprojects

The Klorb project is organized as a collection of subprojects:

* `klorb/` - python library that is the actual harness itself. Everything that the system can
  "do", is done here. Also includes CLI tools, TUI, and ACP server for harness/plugin communication. Written in python. Enforce a a strict firewall where the
  actual agentic logic is all in "library" code that can be invoked headless, tui, or over remote ACP connection.
  Keep agent functionality reachable from `Session`; don't pollute the Session with TUI- or ACP-specific connection. Use a callback instead.
* `vscode-plugin` - Plugin for VSCode to use the Klorb harness. See "vscode-plugin source tree"
  for how it's organized.

## Rules for development

### General Software Development Principles

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
  today. Encapsulation means a method has no real perspective on its callers.  Name it for its actual effect
  and let the caller carry the feature-specific framing instead.
* Do not import protected methods, except for testing. If a foreign protected method must be imported, consider refactoring to make that method public.
* When revising or refactoring, make the smallest code change necessary to effect the change.
* Do not make unrelated changes while revising or refactoring a file.
* Do not try to be an auto-formatter or lint tool. Use `make lint_fix` or other tools configured in this repository. 
* When possible, reuse existing API endpoints rather than make new ones.
* Never duplicate a constant (a magic number, hardcoded string, default value, etc.) across files.
  * Define the constant in one canonical location and have every consumer import it from
    there.
  * If a circular import, fix the import direction or hoist the constant into a small shared module (e.g.
    `foo_constants.py`) that both sides can depend on without a cycle.
* Work is not done until, at minimum, all existing tests pass.
  * For nontrivial improvements, add new unit tests; those must also pass.
  * If a test fails, the likely reason is because an application
    change caused a regression.
  * It is less likely but not impossible that the test should be modified to pass given the updated application
    source. Be explicit in your
    output to me when you have modified tests in this way.
* Do not add comments or docstrings that reference TODO.md or its contents.
  * If there's a specific incomplete case or follow-up tied to the exact line or method you're
    writing, say so directly inline: `TODO(aaron): <specific, self-contained description of what
    still needs to happen here>`.
  * Don't use a bare `TODO:` (no owner) for this — always `TODO(aaron): ...`.
* Docstrings and comments must describe the code as a static snapshot: how and why it currently
  works, never how it changed. Don't write "the old heuristic", "previously", "no longer",
  "this replaces/fixes/regresses X", "unlike before", or similar diff-against-history framing - that goes stale.
  This applies to docs/specs/ too: specs may explain why a
  feature exists (including its relationship to a backlog item/TODO),
  but describe the current behavior, don't narrate the diff from a prior
  version. Record change history in
  an ADR (docs/adrs/) instead; cross-reference  from the docstring/comment/spec if necessary.
* Method docstring comments should succinctly describe the *purpose* of the method. Why would
  someone else call this method? Do not narrate every step it performs. Do not list every caller, or more than 2 examples of something.
  * For how non-obvious code works, add a regular comment at that
  specific point in the method body explaining what it does or why.
  * If you edit a method or function, you *rarely* need to make its docstring longer.
* Add `logger.debug()` calls at consequential moments: creating or removing
  files, registering cleanup handlers, granting
  permissions, spawning subprocesses, and similar state-changing
  operations. Err on the side of debug logging more than feels necessary.
  This is distinct from user-facing info/warning/error logs,
  which should stay reserved for what a user actually needs to see.
* Default to no comment; add one only when the WHY is genuinely non-obvious. 1-2 sentences.
* Existing comments and docstrings are **much** too long!
    That history is not license to keep doing it.
    Lengthy comments in the codebase are not a template to match: they are
    a mistake this rule exists to stop you from repeating. Judge each new comment
    against the rule above, not against the longest nearby example.
* Don't narrate what a method *isn't* doing, alternatives it doesn't take, or where else a
    concern is handled instead.
    State what the code does, not a tour of the design space around it.
* If a rationale needs more than a sentence or two, put it in a spec or
    ADR, not the docstring. Leave a  pointer (`see docs/specs/foo.md`)
    in the code instead of inlining it.
* This especially applies to code review responses and to docstrings on methods that are
    straightforward once named well: trust the reader, don't pre-empt questions
    they might not even ask.
* State your invariants; don't cite another class's behavior as evidence for them. `"raises
    ValueError if session is None, the same contract BashTool._execute_persistent enforces"` is
    bad. Explanations are self-contained. Only reference
    another symbol when it is directly coupled to the current method and its invariants. 
* Do **not** enumerate every item a rule applies to! If a comment needs to illustrate that a rule
    covers several things (config keys,  call sites, directories...), name
    *one or two* concrete examples and stop.
    Some lists are already too long. Make them shorter as you see them.
* Never repeat an explanation. If you have already stated *why* something is
    anywhere in the same file, docstring, or commit, do not restate
    it at the next place it comes up. Before
    writing a sentence that justifies something, Grep for whether
    that justification already exists; if it does, just leave a pointer.
* Prefer `map()`/`filter()` (wrapped in `list()`/`set()` as needed) over bracket-notation
    comprehensions for a plain transform-only or filter-only list/set build — e.g.
    `list(filter(lambda x: x.is_open, items))` over `[x for x in items if x.is_open]`.

### Json file format and style 

Any JSON file klorb writes must include a `schema: {name, version}` envelope to detect out-of-date persisted data. See
`docs/specs/persisted-json-schema-versioning.md` for the convention and shared implementation. But do not explicitly bump a schema version or add code for backward compatibility unless explicitly requested by the user.

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
(`{"extends": "../../tsconfig.webview.json"}`) purely so VSCode's editor tooling
picks the right project: it only auto-discovers a file literally named `tsconfig.json` by
walking up from whatever file is open, so without these, opening a file under `src/webview/` or
`test/webview/` would find the *host* `tsconfig.json` (which excludes that subtree entirely) and
fall back to an "orphan file" with no `paths` aliases at all. The actual `tsc`/`tsgo`/`esbuild`
invocations always pass `-p tsconfig.webview.json` (or `-p ./`) explicitly, so these two files
are never referenced by any script and exist only for the editor's benefit.

Within `src/host/` and `src/webview/`, most code lives under a `features/<name>/` folder
(`src/webview/features/history/`, `src/host/features/acp/`, ...), following the "bulletproof
react" style: a feature's `index.ts` is the *only* module anyone outside that feature may
import — never deep-import a file from inside another feature
(`webview/features/history/historyModel` from outside `features/history/` is wrong; import
`webview/features/history` and let its `index.ts` re-export what's needed). Enforced by
`eslint.config.mjs`'s `no-restricted-imports` rule. Inside a feature, organize submodules
as-needed (`components/`, `hooks.ts`/`hooks/`, `types.ts`/`types/`, or plain
files). The barrel is what's contractual, not the internal shape.

Top-level `src/webview/
components/` and `src/webview/hooks/` (outside any `features/` folder) hold only pieces
genuinely universal across features (e.g. `VsCodeApiProvider`/`useVsCodeApi`), not specific to
one.

Every tsconfig (`tsconfig.json` for the host, `tsconfig.webview.json` for the webview) declares
`paths` aliases rooted at `src/`: `shared/*`, plus `host/*` (host tsconfig only) or `webview/*`
(webview tsconfig only). Never both in the same config. The host and webview must not
import each other's code. Applying the general Import Rules to vscode-plugin
specifically: relative imports (`./foo`, `../foo`) are reserved for imports inside
the *same* `features/<name>/` folder; every other import — including between top-level
non-feature files — uses the rooted alias form (`import PromptInput from
'webview/components/PromptInput'`, not `'./components/PromptInput'`).

`vitest.config.mts` uses
the `vite-tsconfig-paths` plugin (pointed at both tsconfigs via its `projects` option) so tests
resolve the same aliases; adding a new subtree under `test/` also requires adding it to the
matching tsconfig's `include` (see that file's comments) or the alias won't resolve for tests
rooted there.

React component/hook files (not plain utility/model modules like `historyModel.ts` or
`keyHandling.ts`, which keep named exports) export their component or hook as `export default`.
A feature's `index.ts` barrel re-exports a default-exported item by name (`export 
HistoryView from './components/HistoryView';`). Consumers still get a named import from the
barrel.
