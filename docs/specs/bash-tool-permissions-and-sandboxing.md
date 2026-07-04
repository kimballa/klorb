# Bash tool permissions and sandboxing

**Status: proposed plan — nothing in this document is implemented yet.** This is a design plan
for the `BashTool` bullet in `TODO.md` and the bash-command resource kind that
`docs/specs/permissions.md`'s "Out of scope" section forward-references. Nothing here changes
any existing code; it's a blueprint to build from later.

## Context

`klorb.permissions` (`docs/specs/permissions.md`) already has a general `PermissionsTable[T]`
abstraction — `deny`/`ask`/`allow` rule lists evaluated in that fixed order, the strictest
applicable rule always wins — with one concrete resource kind built on it so far: directory
access (`DirectoryAccessTable`, gating the file tools). A `BashTool` needs a second resource
kind, gating shell commands the same way. The interesting problem isn't the permissions engine
(it's reusable as-is) — it's how to turn an arbitrary bash command string into something the
engine can evaluate, without the classifier itself becoming the weak point.

That question was researched before writing this plan, and the findings shape every decision
below:

* **Lexical/string-based command allowlisting is a known-broken approach.** Claude Code's own
  bash permission system was the subject of CVE-2025-66032, bypassed via variable-expansion
  tricks, git flag abbreviation, and `sed`'s `e` modifier. A 2026 disclosure ("GuardFall") found
  10 of 11 surveyed open-source AI coding agents (Aider, Open Interpreter, OpenHands, Goose,
  etc.) vulnerable to the same class of bypass via `$IFS` expansion, command substitution, and
  quote-splitting. The one agent that held up, Continue.dev, did it by tokenizing structurally
  and escalating to "ask" on anything with variable expansion or ambiguous constructs, rather
  than trying to silently classify everything.
* **Bash's own `trap DEBUG` + `shopt -s extdebug` can veto a command before it runs**, and does
  use bash's real parsing/expansion — but it's a cooperative mechanism, not an adversarial
  boundary: the same shell running the untrusted command can just `trap - DEBUG` or `set +T` to
  disable it, and by default the trap doesn't even propagate into `$(...)`, subshells, or
  functions without `set -T`/`functrace`. Ruled out as the core mechanism.
* **No Python binding exists for mvdan/sh's `interp` package.** (`shfmt-py` on PyPI just
  downloads the prebuilt binary; the JS/WASM builds only expose the parser, not the
  interpreter.) The realistic integration path is shelling out to the `shfmt` CLI itself.
* **Parsing and sandboxing solve different problems and both matter.** A parser decides "should
  this be auto-approved without bothering the user"; a sandbox decides "if the classifier is
  wrong, or the command turns out to mean something else, how much damage can it do." Anthropic's
  own trajectory for Claude Code reflects this: it moved toward OS-level sandboxing
  (bubblewrap/Landlock/seccomp on Linux, Seatbelt on macOS) as the real boundary, rather than
  trying to make string matching airtight. This plan builds both layers, and neither is a
  substitute for the other.

## Two independent, stackable layers

1. **`CommandPermissionsTable`** — a structural-parse-based classifier (via `mvdan/sh`, see
   below) that decides whether a command is auto-approved, needs to ask, or is denied. This is
   the UX/friction layer: its job is to avoid prompting for obviously-safe commands.
2. **A bubblewrap sandbox** around whatever the `BashTool` actually executes, regardless of how
   it was classified. This is the security boundary: its job is to bound the blast radius even
   when the classifier gets it wrong, or when a command's true effect wasn't what its surface
   text suggested.

Layer 1 is what makes the tool pleasant to use (most commands shouldn't need a prompt). Layer 2
is what makes it safe to leave commands unattended. Building only layer 1 repeats the mistake
GuardFall documented across most existing agents; building only layer 2 would mean prompting for
every single command, which defeats the point of "approve each command independently,
automatically if possible."

## Layer 1: `CommandPermissionsTable` (command classification)

### Parsing: shell out to `shfmt --to-json`

`shfmt` (the CLI built on `mvdan/sh`'s `syntax` package) supports `--to-json`/`-tojson`, which
serializes the full parsed AST — pipelines, lists (`&&`/`||`/`;`), subshells, command
substitution, process substitution, redirections, here-docs, functions, everything — with a
matching `--from-json` for round-tripping. It's described in the ecosystem as the most complete,
battle-tested bash parser available, and there's existing prior art for exactly this use case:
[`oryband/claude-code-auto-approve`](https://github.com/oryband/claude-code-auto-approve) feeds
Claude Code's own bash commands through `shfmt --to-json`, walked with `jq`, to approve
compound commands segment-by-segment.

klorb's version: invoke `shfmt --to-json` as a subprocess (matching the "shelling out is fine"
constraint), parse the JSON with the stdlib `json` module — no `jq` dependency needed once it's
in Python — and walk the resulting tree in `klorb.permissions.command_access` (new module,
mirroring `directory_access`'s placement).

**Known risk to design around:** `mvdan/sh` issue #1321 documents `--to-json` output shape
changing across versions. The plan is to pin an exact `shfmt` version (vendored or
pip/npm-style downloaded, matching the `shfmt-py` pattern of fetching a prebuilt binary per
platform) and add a fixture-based self-check (parse a small known script at startup or in
tests, assert the expected node shape) so a version drift fails loud instead of silently
misclassifying commands.

### Flattening the AST into decisions

Walking the tree needs to:

* Extract every simple command (argv0 + args) across pipelines, `&&`/`||`/`;` lists, subshells,
  `$(...)`/backtick command substitution, and process substitution — each one is a candidate
  the new table evaluates independently, the same way each directory access is evaluated
  independently today.
* Extract every redirection target (`>`, `>>`, `2>`, etc.) and route it through the *existing*
  `evaluate_write()` (`klorb.permissions.workspace`) — a bash redirection is a filesystem write,
  and it should be governed by the same `DirectoryAccessTable`/`writeDirs` a model-invoked
  `EditFile` call already is, not a second parallel filesystem policy.
* Combine every sub-verdict (one per simple command, plus one per redirection target) using the
  same "strictest wins" pattern already established for read/write
  (`docs/adrs/write-verdict-is-stricter-of-read-and-write-tables.md`): the overall verdict for a
  compound command is never more permissive than its strictest constituent part.

### Fail closed on anything not confidently classified

This is the single most load-bearing design rule, directly answering what made Continue.dev the
one agent GuardFall didn't break: **any construct the walker can't confidently classify escalates
to `"ask"` (or `"deny"` for destructive-looking constructs), never silently to `"allow"`.**
Concretely, this includes:

* A `shfmt --to-json` parse error or exit failure on the input.
* A node type the walker doesn't recognize (protects against `shfmt` version drift surfacing new
  AST shapes; see above).
* A command name or argument that isn't a literal — i.e. built from a variable, parameter
  expansion (`$IFS` and friends), command substitution, or arithmetic expansion — since that's
  exactly the class of trick GuardFall documents as the common bypass vector across affected
  agents.
* `eval`, `exec`, `source`/`.`, and similar commands whose real effect isn't visible in their own
  argv.

### Rule model and wiring

* A new `CommandRules` pydantic model (`deny`/`ask`/`allow`, mirroring `DirRules`'s shape) and
  `CommandPermissionsTable(PermissionsTable[...])` implementing `_matches()` against parsed
  simple-command candidates (argv0 exact/glob match, optionally argument-prefix patterns —
  design left open pending config-format discussion; see "Open questions" below).
  `SessionConfig` gains a `command_rules` field (on-disk key TBD, following the existing
  dot-delineated lowerCamelCase convention from `docs/specs/process-and-session-config.md`).
* A `BashTool` (`klorb.tools.bash`, filling `TODO.md`'s bare `BashTool` bullet) that: parses via
  the above, evaluates the combined verdict, calls the existing single seam
  `raise_if_not_allowed()` (unchanged — no new fail-closed/ask-routing logic needed, it's already
  general), then executes via `subprocess` (inside the sandbox described below) only once
  `raise_if_not_allowed` returns normally.

## Layer 2: bubblewrap sandbox (execution boundary)

Regardless of what `CommandPermissionsTable` approves, the actual `subprocess` execution should
run inside a `bwrap` sandbox so a misclassified or unexpectedly-behaving command is bounded by
the kernel, not by how well the parser understood it. `bwrap` works below the shell/command
layer entirely — it constrains what any command, however it's spelled, can reach on the
filesystem/network/process table — so it doesn't need to understand bash syntax at all, and it
doesn't interfere with legitimate complex dev workflows (compilers forking subprocesses,
backgrounded builds, pipes) the way a naive restrictive sandbox would.

Planned invocation shape (Linux only; see "Open questions" for macOS):

* `--ro-bind /usr /usr`, `--ro-bind /lib /lib`, `--ro-bind /bin /bin` (and friends) so the base
  OS/toolchain is present but immutable.
* `--bind <workspace_root> <workspace_root>` read-write, sourced from the *same*
  `SessionConfig.workspace_root`/`writeDirs` the file tools already use — one source of truth for
  "what may this process touch," rather than a second filesystem policy defined independently for
  the sandbox. Any additional `writeDirs.allow` entries get their own `--bind`; everything else is
  simply absent from the mount table (not merely read-only).
* `--tmpfs /tmp`, `--proc /proc`, `--dev /dev` for a normal-looking but disposable scratch area.
* `--unshare-all` (user, pid, ipc, uts, net namespaces) by default; `--share-net` only when a
  command actually needs network access — tying network egress to a future permission resource
  kind (`TODO.md`'s "website access" bullet, `docs/specs/permissions.md`'s other forward
  reference), not granted unconditionally.
* `--die-with-parent` and `--new-session` (the latter needed to prevent `TIOCSTI`-based
  sandbox escapes when no seccomp filter is present).
* `--cap-drop ALL`, and optionally a `--seccomp <fd>` filter blocking syscalls no dev workflow
  legitimately needs (`ptrace`, `mount`, `reboot`, keyring manipulation) — a defense-in-depth
  layer on top of the namespace boundary, not required for a first version.

### Failure mode if `bwrap` isn't available

Default to fail-closed: `BashTool` refuses to run at all if `bwrap` can't be located/invoked,
rather than silently falling back to unsandboxed execution. (Configurable, for platforms/CI
environments that accept the tradeoff explicitly — exact knob left open, see below.)

## Worked example

Model requests `curl https://example.com/install.sh | sh`. The `CommandPermissionsTable` walker
sees a pipeline of two simple commands; `curl` and `sh` are both plain literals, so it can
classify them — but "pipe an arbitrary downloaded script into `sh`" is exactly the destructive
shape a conservative rule set should flag, so this lands in `ask` or `deny` per configured rules
rather than `allow`, independent of sandboxing. If it were auto-approved (or if the config
happened to allow it), the sandbox still bounds the outcome: no network unless this session was
granted it, and even with network granted, the executed script can only touch
`workspace_root`/granted write dirs, not the rest of the filesystem.

## Open questions for the actual implementation

* Exact `CommandRules` matching semantics (argv0-only vs argv0+argument-prefix patterns; glob
  vs regex) — needs its own design pass, likely its own ADR once decided.
* Exact on-disk config key name(s) for `command_rules`/sandbox opt-out, following
  `docs/specs/process-and-session-config.md`'s naming convention.
* Whether `bwrap` (or a fetched/vendored copy of it) is a hard runtime dependency of klorb, or an
  optional feature that degrades to "`BashTool` unavailable" when absent.
* macOS sandboxing story (Seatbelt/`sandbox-exec`) — not required for a first Linux-only version,
  but the same "sandbox regardless of classification" principle should extend there eventually.
* Whether a `shfmt` parse failure should be surfaced to the model as a normal tool error (so it
  can retry with simpler syntax) or should look identical to an `ask`/`deny` verdict from the
  model's point of view.

Several decisions already made during this plan's research (shelling out to `shfmt --to-json`
over Go bindings or a pure-Python parser; rejecting the `trap DEBUG`/`extdebug` technique as a
security boundary; treating bubblewrap sandboxing as mandatory defense-in-depth rather than an
alternative to classification) are ADR-worthy and should be written up as ADRs in `docs/adrs/`
once implementation actually begins, so the reasoning survives independent of this planning
document.
