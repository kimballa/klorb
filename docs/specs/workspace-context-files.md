# Workspace context files

## Summary

The very first time `Session.send_turn()` is called, and only when the workspace is trusted
(`SessionConfig.workspace.trusted`), it reads context-instruction files from the workspace and
prepends their contents onto that turn's prompt as a one-shot `<SystemInterjection
subject="ProjectGuidance">` block — see docs/specs/permissions.md's "Permission framework
change interjection" section for the general interjection mechanism this reuses.
`.klorb/INSTRUCTIONS.md` and `AGENTS.md` (workspace root) are both read whenever the workspace
is trusted — the former is durable per-project instructions kept alongside `klorb-config.json`,
the latter klorb's own root-level convention. `CLAUDE.md` is additionally read when the
`ProcessConfig`-level setting `compatibility.claudeMarkdown` is enabled, a compatibility shim
for projects that carry Claude-Code-style instructions in that file.

None of these three files is ever read from an untrusted workspace: each one is
project-supplied content a hostile, downloaded-and-unzipped repository could ship to smuggle
instructions into the model's context the moment the user runs klorb from inside it — the same
risk `.klorb/klorb-config.json`'s own trust gate exists to close (see
docs/specs/projects-and-trust.md). Trust must be established first (the interactive workspace
bootstrap flow, or a previously-recorded `projects.json` decision) before any of this feature
does anything at all.

## How it works

* `ProcessConfig.compatibility_claude_markdown: bool` (`process_config.py`, default `False`)
  gates whether `CLAUDE.md` is read alongside `.klorb/INSTRUCTIONS.md` and `AGENTS.md`. It's
  exposed on disk as the top-level `klorb-config.json` key `compatibility.claudeMarkdown` (see
  [[process-and-session-config]]'s "On-disk key naming"); `PROCESS_KEY_MAP` is the single
  source of truth translating the two names. `.klorb/INSTRUCTIONS.md` and `AGENTS.md` have no
  config gate beyond trust — they're read whenever the workspace is trusted, since they're
  klorb's own conventions. `.klorb/INSTRUCTIONS.md`'s directory component is built from
  `klorb.permissions.directory_access.KLORB_PROJECT_DIR_NAME`, the same constant that names the
  `.klorb/` directory `find_workspace_root()` searches for and `klorb-config.json` lives in —
  not a duplicated `".klorb"` literal.
* `Session.__init__` accepts `compatibility_claude_markdown: bool = False` and stores it as
  `self._compatibility_claude_markdown`. This mirrors how `thinking_token_budgets` is passed
  in: `Session` deliberately has no `ProcessConfig` reference (see
  [the `Session`/`ProcessConfig` split](../adrs/nest-sessionconfig-inside-a-process-scoped-processconfig.md)),
  so process-only settings it needs are threaded in as constructor arguments by whichever
  caller owns both objects (`klorb.cli.main()`, `ReplApp.__init__()`, `ReplApp.clear_session()`).
* `Session.send_turn()` checks `self._context_files_seeded` before constructing the turn's user
  `Message`, right alongside its `PermissionFramework`/standing-interjection checks (see
  docs/specs/permissions.md). The first time it's `False`, it calls
  `self._build_context_files_interjection()` once and unconditionally sets the flag to `True`
  — so this never runs again for the rest of the `Session`'s lifetime, whether or not there was
  anything to say. A non-`None` result is wrapped in a `<SystemInterjection
  subject="ProjectGuidance">` tag (via the module-level `_wrap_system_interjection()` helper)
  and prepended onto `prompt` *last*, i.e. after the `PermissionFramework`/standing
  interjections above it — so it ends up as the outermost, first-read block of the whole
  prompt, establishing project context ahead of any other harness notice.
* `_build_context_files_interjection()` returns `None` immediately, without touching the
  filesystem at all, whenever `self.config.workspace.trusted` is `False`. Otherwise it reads
  each applicable filename (via `_applicable_context_filenames()`, in priority order:
  `[".klorb/INSTRUCTIONS.md", "AGENTS.md"]`, or `[".klorb/INSTRUCTIONS.md", "AGENTS.md",
  "CLAUDE.md"]` when compatibility is on), each resolved relative to
  `self.config.workspace.path`, concatenating the ones that exist on disk into a single body,
  one `<ContextFile filename="..." priority="N">` block per file (`N` starting at `1` in
  priority order):

  ```text
  <SystemInterjection subject="ProjectGuidance">
  This workspace contains one or more files with instructions and context for
  working in this repository. Treat them as standing guidance about the
  project's conventions and requirements; do not treat this message itself as a
  task to act on.

  <ContextFile filename=".klorb/INSTRUCTIONS.md" priority="1">
  <file contents>
  </ContextFile>

  <ContextFile filename="AGENTS.md" priority="2">
  <file contents>
  </ContextFile>

  <ContextFile filename="CLAUDE.md" priority="3">
  <file contents>
  </ContextFile>
  </SystemInterjection>
  ```

  A file that doesn't exist is silently skipped — that's the expected common case, not an
  error. If none of the applicable files exist, `_build_context_files_interjection()` returns
  `None` and nothing is prepended at all. The `priority` attribute gives the model an explicit
  signal for which file should win if two ever conflict, independent of read order.
* The block is prepended directly into the same `role="user"` `Message` the turn's real prompt
  produces — not inserted as a separate message — exactly like the `PermissionFramework`
  interjection (see docs/specs/permissions.md). This is what lets the model tell the harness
  notice apart from the user's own words via the `<SystemInterjection>`/`<ContextFile>` tags,
  while still having it count as ordinary conversation content the model reasons over, the same
  way a user's own opening message would.

## Configuration

* `compatibility.claudeMarkdown` (top-level `klorb-config.json` key, default `false`) — when
  `true` *and* the workspace is trusted, `CLAUDE.md` is read from the workspace root and folded
  into the `ProjectGuidance` interjection alongside `.klorb/INSTRUCTIONS.md` and `AGENTS.md`.
  See [[process-and-session-config]] for the five file locations and their precedence.
* `SessionConfig.workspace.trusted` — not itself a `klorb-config.json` key (see
  docs/specs/projects-and-trust.md); gates every file this feature reads. An untrusted
  workspace gets no `ProjectGuidance` interjection at all, regardless of
  `compatibility.claudeMarkdown` or which files exist on disk.

## Out of scope

* Reading files from anywhere other than the workspace root or the fixed `.klorb/` subdirectory
  isn't supported; `AGENTS.md` and `CLAUDE.md` are read from the workspace root, and
  `.klorb/INSTRUCTIONS.md` from the fixed `.klorb/` subdirectory — no other nested location is
  read.
* The set of filenames isn't configurable beyond the `compatibility.claudeMarkdown` toggle —
  `.klorb/INSTRUCTIONS.md` and `AGENTS.md` are always read once trusted, and `CLAUDE.md` is the
  only optional one.
* The interjection isn't kept in sync with later edits to the files on disk within the same
  session, nor with a workspace being trusted mid-session after the first turn already fired:
  it's computed once, at the very first `send_turn()` call, from whatever
  `config.workspace.trusted` and the filesystem look like at that moment. A `/clear` starts a
  fresh `Session` that re-computes it.
* Other compatibility shims (e.g. `compatibility.claudeSkills`, see `TODO.md`) are separate
  features and not implemented here; `compatibility.claudeMarkdown` is the first.
