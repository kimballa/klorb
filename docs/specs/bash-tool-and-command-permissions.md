# BashTool and command permissions

## Summary

`BashTool` (`klorb.tools.bash`) lets the model run a shell command, gated by the same
deny/ask/allow `PermissionsTable` abstraction the file tools already use
(docs/specs/permissions.md), applied to a second resource kind: parsed shell command argv
(`klorb.permissions.command_access.CommandPermissionsTable`). A command string is never
classified by regexp or lexical pattern matching — it's parsed into a real AST via `shfmt
--to-json` (`klorb.permissions.shell_parse`) and walked structurally. Anything the walker can't
confidently classify — a non-literal token, an unrecognized construct, a backgrounded top-level
`&`, a heredoc/pipe feeding a command outside a fixed safe-stdin-consumer allowlist (`cat`,
`less`, `more`, `head`, `tail`, `grep`/`egrep`/`fgrep`, `sort`, `uniq`, `wc`, `jq`, `git` — see
`SAFE_STDIN_CONSUMERS` below) — escalates to `"ask"`, never silently to `"allow"`. See the ADRs
listed at the end of this doc for the reasoning behind its most consequential decisions.

`BashTool` is designed for a second, independent safety layer — a `bwrap` (bubblewrap) sandbox
boundary — but building the actual sandbox argv is not implemented yet (`klorb.sandbox`); every
command runs unsandboxed today. See "Sandboxing" below.

## How it works

### Parsing (`klorb.permissions.shell_parse`)

`parse_command(command, shfmt_command)` runs `shfmt --to-json` as a subprocess (resolving a bare
`shfmt_command` like the shipped default via `PATH` first, falling back to the directory
containing the running Python interpreter — see `_resolve_shfmt_command` — since `shfmt-py` is a
"scripts"-only wheel with no importable Python API, and its installed binary may not be on `PATH`
when klorb runs from an unactivated venv), parses the JSON, and walks the resulting tree into a
`BashCommandAnalysis`:

* `simple_commands: list[list[str]]` — every command's own argv (argv0 first), extracted from
  every pipeline stage, `&&`/`||`/`;` list member, subshell, `$(...)`/backtick command
  substitution (including one embedded inside another word — e.g. a redirect target or a
  `case` pattern — walked for defense in depth even when the *outer* construct isn't itself a
  literal candidate), `if`/`while`/`for`/`case` body, function body, and `export`/`declare`/
  `local` (`DeclClause`) invocation.
* `redirects: list[RedirectTarget]` — every redirection's file target (`target`, `direction`:
  `"read"` for `<`, `"write"` for `>`/`>>`/`<>`/`>|`/`&>`/`&>>`). A redirect operator whose target
  is a bare digit/`-` (`2>&1`) is a file-descriptor duplication, not a filesystem path, and is
  skipped entirely. A heredoc/herestring (`<<`/`<<-`/`<<<`) has no filesystem target at all — its
  content is inline in the script — so instead of a `RedirectTarget`, the *owning* statement's
  command is checked against the safe-stdin-consumer allowlist (see below). A `cat`/`less`
  invocation that is neither piped into from elsewhere nor itself redirected — a plain
  `cat file.txt`, not `foo | cat` or `cat < file.txt` — also produces a `RedirectTarget` with
  `direction="read"` for each of its non-flag literal arguments (`klorb.permissions.shell_parse.
  _maybe_add_implicit_reads`, `IMPLICIT_READ_COMMANDS`): these two commands are common enough as
  simple file reads that checking their arguments against `readDirs` too — on top of, not instead
  of, the ordinary `CommandRules` check on the `cat`/`less` invocation itself — gives them the
  same protection a real `ReadFile` call already gets, rather than leaving `readDirs` blind to
  file paths a `CommandRules` rule has no notion of.
* `forced_ask_reasons: list[str]` — every reason the walker itself escalated to `"ask"`:
  * A token (argv0 or any argument) that isn't a plain literal — built from a variable,
    parameter expansion, command/arithmetic substitution, or anything else that isn't a `Lit`/
    `SglQuoted`/all-literal-`DblQuoted` word. Quoting alone is **not** a bypass signal:
    `git commit -m "a message"` and `echo 'text'` are ordinary literals — only unresolved
    interpolation (`"$VAR"`, `` `cmd` ``, `$(cmd)`) triggers this.
  * `eval`, `exec`, `source`, or `.` as argv0 — these hide their real effect from their own argv.
  * A top-level `&` (backgrounding a command) — rejected at parse time.
  * A pipe, heredoc, or herestring feeding a command outside `SAFE_STDIN_CONSUMERS` (`cat`,
    `less`, `more`, `head`, `tail`, `grep`/`egrep`/`fgrep`, `sort`, `uniq`, `wc`, `jq`, `git` —
    commands that only ever consume stdin as inert data to read, search, filter, or summarize,
    never execute; deliberately excludes `tee`/`xargs`, which can turn piped-in data into further
    commands or file writes) — anything else that treats stdin as code to execute, `sh`/`bash`/
    `python`/etc., escalates regardless of what an allow-rule for its own argv0 would say.
  * An AST node type the walker doesn't recognize (protects against a `shfmt` version-shape
    drift surfacing a construct this walker predates).

A `shfmt --to-json` parse failure (real shell syntax error) raises `ShellParseError`, surfaced to
the model as an ordinary tool error so it can retry with simpler syntax — not routed through the
permissions system, since a syntax error isn't a permission verdict.

### Command permissions (`klorb.permissions.command_access`)

`CommandRules` (`deny`/`ask`/`allow`, each `list[list[str]]`) is a `SessionConfig.command_rules`
field, on-disk `commandRules` (nested under `sessionDefaults`, concatenated across config layers
exactly like `readDirs`/`writeDirs` — see docs/specs/permissions.md and
docs/adrs/command-rules-mirror-dirrules-deny-ask-allow-evaluation.md).
`CommandPermissionsTable(PermissionsTable[list[str]])` matches a rule against a candidate argv
positionally: each rule token is either a literal (exact match at that position), `"*"`
(`WILDCARD_TOKEN`, matching exactly one arbitrary token — always, at any position, never
special-cased by position), or `"?"` (`OPTIONAL_TOKEN`, matching zero-or-one arbitrary token at
any position *except* the rule's own last token, where it instead matches any number of further
tokens, including zero). See
docs/adrs/command-rule-wildcards-bounded-star-trailing-unbounded-question-mark.md for the
reasoning behind splitting "exactly one" and "unbounded" onto two symbols.

| Rule | Matches |
| --- | --- |
| `["foo"]` | `foo` only (no args at all — "forcibly no args") |
| `["foo", "*"]` | `foo bar` only — exactly one more token, never zero, never two |
| `["foo", "?"]` | `foo`, `foo bar`, `foo bar baz`, ... — unbounded, since `?` is last |
| `["git", "status", "?"]` | `git status`, `git status -s`, `git status -s -b`, ... |
| `["git", "*", "status", "?"]` | `git <exactly-one-token> status <anything...>` |
| `["git", "?", "status"]` | `git status` (zero) or `git --no-pager status` (one) — not two or more |
| `["foo", "--bar", "--baz"]` | exactly `foo --bar --baz`, nothing more or less |

A candidate argv with no matching rule in any list evaluates to `None` from
`PermissionsTable.evaluate()`; `BashTool` normalizes that to `"ask"` (never a permissive
default — no command runs merely because nothing explicitly denied it).

### Combining verdicts (`klorb.tools.bash.BashTool._classify`)

`BashTool.apply()` evaluates every contributor independently:

* Every `simple_commands` entry against `CommandPermissionsTable`.
* Every `redirects` entry: a `"write"` target through `resolve_within_workspace()` +
  `evaluate_write()` (the same check `EditFile` uses), a `"read"` target through
  `resolve_and_evaluate_read()` (the same check `ReadFile` uses).
* One `"ask"` contribution per `forced_ask_reasons` entry.

A single `"deny"` anywhere short-circuits the whole command: `BashTool.apply()` raises a plain
`PermissionError` immediately, with no further items collected. Otherwise, every individual
`"ask"` contributor becomes its own `klorb.permissions.table.PermissionAskItem` — not just the
strictest one — and `apply()` raises `MultiPermissionAskRequired` carrying all of them, so
`Session` asks about each in series rather than collapsing a compound command's several
independent concerns into one prompt (see docs/specs/permissions.md's "Multi-item asks" section
and
[the serial multi-item ask ADR](../adrs/ask-independent-items-serially-not-just-the-strictest.md)).
A redirection target's item carries its resolved `Path` and read/write direction, so it flows
through `Session`'s existing directory-access grant machinery exactly like `EditFile`'s does. A
bare command-pattern item (no filesystem resource involved) carries a `command` argv instead,
persisted through `klorb.permissions.command_grant.apply_command_permission_grant` on a
persistent-scope decision — it is *not* automatically failed closed the way a path-less
single-item `PermissionAskRequired` still is, so a plain command like `make test` needing
confirmation actually reaches a prompt instead of being refused outright. A `forced_ask_reasons`
entry with neither a path nor a command becomes a structural item, for which only `"once"`/`deny`
are meaningful (see `PermissionAskItem`'s own docstring).

### Execution

`apply()` requires the model to pass `shell_lifetime="command"` — the only value the tool's
JSON-schema `enum` currently accepts — so each call spawns its own fresh, non-persistent shell
that exits when the command finishes: no `cd`, exported variable, or background job carries over
to the next call. This is deliberately explicit in the schema rather than an implicit fact of the
implementation, so a future `shell_lifetime="session"` mode (one shell surviving across several
calls) is an additive, opt-in schema change for existing callers rather than a silent behavior
change (`TODO.md`/an inline `TODO(aaron): ...` on `BashTool` tracks this as unbuilt).

Once permitted, the command runs as `bash --rcfile ${HOME}/.bashrc -i -c "unset PS1; unset PS2;
<command>"` (no `--login`) — `-i --rcfile` is what makes bash source `~/.bashrc` despite being
non-login (a plain `bash -c` silently skips it for the vast majority of real `.bashrc` files; see
docs/adrs/bash-env-uses-clearenv-plus-shareenv-setenv-plus-forced-rcfile.md for why), so PATH/
toolchain setup gets recomputed the same way the user's own shell already does it. Two fixed,
content-independent stderr lines `bash -i` always emits with no controlling tty are stripped from
the front of captured stderr (`klorb.tools.bash._strip_bash_shell_noise`) before anything is
returned to the model.

**Environment**: `klorb.tools.bash.build_bash_env(session_config)` builds an explicit dict (never
inherits the klorb process's full environment): `HOME`/`USER` always, `WORKSPACE_ROOT`
unconditionally set to the resolved workspace root (not gated on any config, so a command can
always locate the workspace regardless of `shareEnv`), then every `SessionConfig.share_env` name
(on-disk `shareEnv`, concatenated across layers) that's actually set in the klorb process's own
environment, then `SessionConfig.set_env` (on-disk `setEnv`, merged key-by-key, later layers
replacing earlier ones for the same key) as overrides applied last.

**Timeout**: `ProcessConfig.bash_timeout_seconds` (`tools.bash.timeout`, default `120.0`) — always
enforced (unlike the REPL's `!`-prefixed `shell_timeout_seconds`, which may be `None`). The
process is killed with SIGKILL on timeout.

**stdout/stderr capture**: each invocation gets its own `mkdtemp()` directory (registered with
`atexit` for cleanup if klorb itself dies mid-command); `stdout`/`stderr` files are `chmod 0600`
before any content can reach them. A stream at or under `ProcessConfig.bash_spill_bytes`
(`tools.bash.spillBytes`, default `8192`) is returned inline (`stdout`/`stderr` in the result); a
larger stream is left on disk and reported as `stdout_file`/`stderr_file` instead, with the
per-invocation directory auto-added to `SessionConfig.read_dirs.allow` so a follow-up `ReadFile`/
`Grep` on it doesn't itself hit an `"ask"`. A call whose output didn't spill has its temp
directory removed immediately.

**Exit status decoding** (`klorb.tools.bash._decode_exit`): `0` is success. A signal death shows
up one of two ways, both decoded to the same human-readable signal name — verified directly
against this project's own environment, not assumed from documentation:

* **Positive**, `128 + signum` — the outer `bash` forked a real child for the target command
  (it wasn't alone in tail position — part of a pipeline, or one of several statements) and
  observed *that* child die by signal, then itself exited normally with the conventional code.
* **Negative**, `-signum` — Python's ordinary direct-child-signaled convention. This happens when
  bash's own tail-call optimization `exec()`s directly into a simple trailing external command
  instead of forking, so there is no separate bash process left to translate the signal into a
  positive code — confirmed empirically (see `_decode_exit`'s docstring).

Any other non-zero, non-signal exit reports `"Process completed normally with non-zero status"`.

### Response shape

`BashTool`'s result dict uses this codebase's ordinary snake_case tool-response convention:
`command`, `exit_status`, `success`,
`failure_reason` (`None` on success), `stdout`/`stderr` (`None` when spilled), `stdout_file`/
`stderr_file` (`None` when not spilled — exactly one of each inline/file pair is non-`None`),
`runtime` (seconds), and an optional `sandbox_notice` (see "Sandboxing" below).

## Sandboxing

`klorb.sandbox.bwrap_available()` runs a cheap, self-contained smoke test
(`bwrap --ro-bind / / --proc /proc --dev /dev -- true`) once per process, cached — the real
question is "can `bwrap` actually create a sandbox right now," not any `/proc`/`/.dockerenv`
environment fingerprinting (those are only used to *word* the fallback notice, never to make the
go/no-go decision). `klorb.sandbox.build_bwrap_argv()` — the function that would build the actual
mount/namespace/env argv for the sandboxed process — is a stub that raises `NotImplementedError`
and is never called: developing and verifying it requires a
host where unprivileged user namespaces actually work, which this project's own dev and
cloud-agent environments do not provide. See
docs/adrs/bubblewrap-is-defense-in-depth-not-a-classifier-substitute.md for why this is a
deliberate, temporary degradation rather than a reason to skip the permission-classification
layer.

`BashTool` therefore runs every command unsandboxed today. The first time that happens in a given
session, the result carries a `sandbox_notice` string explaining why (missing `bwrap` binary —
also naming the `apt-get install bubblewrap` fix and that klorb needs restarting afterward for it
to take effect — this environment disallowing unprivileged user namespaces, or `bwrap` being
available but klorb's sandboxed execution simply not being built yet) and noting that
command/redirect permission checks are still fully enforced regardless. The notice is never
repeated for the rest of that session: `session.tool_state["Bash"]["sandbox_warned"]` (see
docs/specs/tool-framework.md's `ToolSetupContext.session`/`Session.tool_state` description)
tracks whether it's already fired, naturally resetting on `/clear` since a fresh `Session` (and
thus a fresh, empty `tool_state`) is created then. A `BashTool` built from a `ToolSetupContext`
with no real `Session` attached (e.g. a caller that constructs one directly, without going
through `Session`) has no `tool_state` to dedupe against, so it shows the notice on every call
instead of silently dropping it.

## Configuration

```json
{
  "sessionDefaults": {
    "commandRules": {
      "deny": [["rm", "-rf", "/"]],
      "ask": [["git", "push", "?"]],
      "allow": [["git", "?"], ["ls", "?"], ["cat", "?"]]
    },
    "shareEnv": ["NVM_DIR", "PYENV_ROOT"],
    "setEnv": {"CI": "true"}
  },
  "tools.bash.command": "/bin/bash",
  "tools.bash.timeout": 120.0,
  "tools.bash.spillBytes": 8192,
  "tools.bash.shfmtCommand": "shfmt"
}
```

`commandRules`/`shareEnv` merge by concatenation across config layers; `setEnv` merges key-by-key
(a later layer's value for a key replaces an earlier layer's) — see
docs/specs/process-and-session-config.md's "On-disk key naming" section for the full merge-mode
taxonomy this adds a third example of alongside `readDirs`/`writeDirs`.

## Out of scope

* `bwrap` argv construction (`klorb.sandbox.build_bwrap_argv`) — see "Sandboxing" above.
* Network egress permissioning (`TODO.md`'s "website access" item) — `bwrap --unshare-net` is
  planned to deny all network access unconditionally once sandboxing exists; no allowlist
  mechanism is designed yet.
* Structured audit logging of command requests/decisions/outcomes — a real goal, not required for
  this first version.
* macOS support (`sandbox-exec`/Seatbelt in place of `bwrap`) — Linux only.
* A `shell_lifetime="session"` mode keeping one shell process alive across multiple `Bash` calls
  (persisting `cd`, exported variables, and background jobs between them) — see "Execution" above.

## See also

* docs/specs/permissions.md — the shared `PermissionsTable` abstraction, the interactive "ask"
  confirmation flow (including multi-item asks and the permission grid), and the directory-access
  resource kind this doc's `CommandPermissionsTable` mirrors.
* docs/adrs/shell-out-to-shfmt-for-bash-parsing.md
* docs/adrs/reject-trap-debug-as-a-security-boundary.md
* docs/adrs/bubblewrap-is-defense-in-depth-not-a-classifier-substitute.md
* docs/adrs/command-rules-mirror-dirrules-deny-ask-allow-evaluation.md
* docs/adrs/command-rule-wildcards-bounded-star-trailing-unbounded-question-mark.md
* docs/adrs/bash-env-uses-clearenv-plus-shareenv-setenv-plus-forced-rcfile.md
* docs/adrs/ask-independent-items-serially-not-just-the-strictest.md
* docs/adrs/generalize-permission-override-to-a-set-of-resources.md
* docs/adrs/generalize-grant-writer-for-deny-and-mirror-it-for-commandrules.md
