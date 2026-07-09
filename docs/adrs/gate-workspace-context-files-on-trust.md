# Gate workspace context files on workspace trust

## 2026-07-09

## Question

`.klorb/INSTRUCTIONS.md`, `AGENTS.md`, and (when `compatibility.claudeMarkdown` is enabled)
`CLAUDE.md` were read into the model's context on every session, regardless of whether the
workspace had ever been trusted (see docs/specs/projects-and-trust.md). Should reading any of
these three files require the same `workspace.trusted` gate `.klorb/klorb-config.json`'s config
layer already requires?

## Answer

Yes. `Session._build_context_files_interjection()` now returns `None` immediately — without
opening or even `stat`-ing any file — whenever `config.workspace.trusted` is `False`. All three
files are unreadable from an untrusted workspace; there is no per-file distinction (e.g. reading
`AGENTS.md` but not `.klorb/INSTRUCTIONS.md`).

## Reasoning

All three files are project-supplied content: they live inside the workspace, and are written
(or shipped) by whoever controls that workspace, not by the klorb user necessarily. A hostile,
downloaded-and-unzipped repository can ship any of the three to smuggle arbitrary instructions
into the model's context the moment a user runs klorb from inside it — before the user has ever
been asked whether they trust the directory. That is exactly the risk
`docs/adrs/gate-read-hard-boundary-on-workspace-trust.md` and the `.klorb/klorb-config.json`
config-layer gate (docs/specs/projects-and-trust.md) already exist to close for tool permissions
and config; leaving the context-file feature ungated left an equally direct injection channel
wide open, arguably a more dangerous one, since these files are deliberately designed to read as
authoritative "standing project guidance" rather than as data the model should treat with
suspicion.

`docs/specs/projects-and-trust.md` already flagged this as an open item under "Project-level
system-prompt overrides don't exist yet ... Whenever such a per-project tier is built, it must
be gated on `SessionConfig.workspace.trusted`" — this change is that same principle applied to
the context-files feature, which ships today (system-prompt overrides still don't exist).

No new config key or bypass was added: an untrusted workspace simply gets no `ProjectGuidance`
interjection at all (see
docs/adrs/interject-context-files-as-systeminterjection-not-user-turn.md), the same
all-or-nothing shape `.klorb/klorb-config.json`'s own gate uses. `compatibility.claudeMarkdown`
remains an independent, additional gate on top — a project must be both trusted *and* opted
into the compatibility shim before `CLAUDE.md` is read.
