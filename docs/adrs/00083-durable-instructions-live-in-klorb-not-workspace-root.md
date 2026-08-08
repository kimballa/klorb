# Durable per-project instructions live in `.klorb/INSTRUCTIONS.md`, not the workspace root

## 2026-07-08

## Question

Klorb needs a place for durable, per-project instructions that aren't `AGENTS.md` — content
that's specifically klorb's own convention (unlike the `CLAUDE.md` compatibility shim) but
shouldn't compete with `AGENTS.md` for the workspace root, or require a config toggle to opt
into. Where should this file live, and should reading it be gated behind a
`ProcessConfig`/`klorb-config.json` setting the way `CLAUDE.md` is?

## Answer

`.klorb/INSTRUCTIONS.md`, always read alongside `AGENTS.md`, with no config gate.

## Reasoning

`.klorb/` already exists as klorb's own per-project directory — it's where `klorb-config.json`
lives, and `find_workspace_root()` (`klorb.permissions.directory_access`) already ancestor-
searches for it to locate the workspace root. Putting durable instructions there keeps klorb's
own on-disk footprint contained to one directory instead of adding a second root-level
dotfile, and it means projects that don't customize klorb (the common case) see no new file
at their root.

No config gate, unlike `CLAUDE.md`: `compatibility.claudeMarkdown` exists because `CLAUDE.md`
is *someone else's* convention (Claude Code's) that klorb optionally honors for compatibility —
opting in is meaningful because the file might carry Claude-Code-specific assumptions.
`.klorb/INSTRUCTIONS.md` is klorb's own convention, like `AGENTS.md`; there's no
compatibility question to gate, so it's read unconditionally whenever present, exactly like
`AGENTS.md` is.

Implementation-wise this slots into the existing mechanism from
[[../specs/workspace-context-files.md]] and
[inject-workspace-context-files-as-a-user-turn.md](inject-workspace-context-files-as-a-user-turn.md):
`Session._applicable_context_filenames()` gains a second always-on entry alongside `AGENTS.md`,
resolved relative to `self.config.workspace.path` like the others. The `.klorb` directory
component is taken from `klorb.permissions.directory_access.KLORB_PROJECT_DIR_NAME` — the same
constant `find_workspace_root()` and the config loader already use — rather than a second
`".klorb"` string literal, per the project's rule against duplicating constants.
