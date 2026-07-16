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

`BashTool` also runs each command inside a second, independent safety layer — a `bwrap`
(bubblewrap) sandbox boundary (`klorb.sandbox`) — whenever one can actually be created on the
host. Where it can't (no `bwrap` binary, or a kernel/container policy forbidding unprivileged user
namespaces), it falls back to unsandboxed execution with a one-time notice; the permission
classification above is enforced identically either way. See "Sandboxing" below.

Every call may also choose a `shell_lifetime`: `"command"` (a fresh, non-persistent shell for that
one call) or `"session"`/`"new"` (a single persistent shell reused across calls within a
`Session`, keeping `cd`/exported-variable/background-job state between them). `shell_lifetime` is
optional — a missing key, a `None`, or an empty string all default to `"command"`, the
command-scoped shell. See "Execution" and "Session-scoped terminals" below.

## How it works

### Parsing (`klorb.permissions.shell_parse`)

`parse_command(command, shfmt_command)` runs `shfmt --to-json` as a subprocess (resolving a bare
`shfmt_command` like the shipped default via `PATH` first, falling back to the directory
containing the running Python interpreter — see `_resolve_shfmt_command` — since `shfmt-py` is a
"scripts"-only wheel with no importable Python API, and its installed binary may not be on `PATH`
when klorb runs from an unactivated venv), parses the JSON, and walks the resulting tree into a
`BashCommandAnalysis`:

* `simple_commands: list[SimpleCommand]` — every command's own argv (argv0 first, `argv`) plus
  the exact original source text of the `CallExpr`/`DeclClause` node it came from (`source_text`,
  sliced from the raw command string via AST byte offsets rather than reconstructed from `argv`,
  which would lose original quoting/spacing) — extracted from every pipeline stage, `&&`/`||`/`;`
  list member, subshell, `$(...)`/backtick command substitution (including one embedded inside
  another word — e.g. a redirect target or a `case` pattern — walked for defense in depth even
  when the *outer* construct isn't itself a literal candidate), `if`/`while`/`for`/`case` body,
  function body, and `export`/`declare`/`local` (`DeclClause`) invocation.
* `redirects: list[RedirectTarget]` — every redirection's file target (`target`, `direction`:
  `"read"` for `<`, `"write"` for `>`/`>>`/`<>`/`>|`/`&>`/`&>>`, and `source_text`: the exact
  source of the *owning statement* — the whole command line the redirect belongs to, e.g.
  `"cat file > out.txt"`, not just the bare target — since a redirect target alone is meaningless
  without the command it's attached to). A redirect operator whose target is a bare digit/`-`
  (`2>&1`) is a file-descriptor duplication, not a filesystem path, and is skipped entirely. A
  heredoc/herestring (`<<`/`<<-`/`<<<`) has no filesystem target at all — its content is inline
  in the script — so instead of a `RedirectTarget`, the *owning* statement's command is checked
  against the safe-stdin-consumer allowlist (see below). An invocation of
  `cat`/`less`/`more`/`head`/`tail`/`sort`/`uniq`/`wc`/`jq`/`ls` (`IMPLICIT_READ_COMMANDS`) that is
  neither piped into from elsewhere nor itself redirected — a plain `cat file.txt`, not
  `foo | cat` or `cat < file.txt` — also produces a `RedirectTarget` with `direction="read"` for
  each of its non-flag literal arguments (`klorb.permissions.shell_parse.
  _maybe_add_implicit_reads`): these commands are common enough as simple file reads that
  checking their arguments against `readDirs` too — on top of, not instead of, the ordinary
  `CommandRules` check on the invocation itself — gives them the same protection a real
  `ReadFile` call already gets, rather than leaving `readDirs` blind to file paths a
  `CommandRules` rule has no notion of. `ls`'s non-flag arguments are directory/file paths to
  list rather than a stream to read line-by-line, but the same read-access check applies to them
  either way.
* `forced_ask_reasons: list[ForcedAskReason]` — every reason the walker itself escalated to
  `"ask"` (`reason`), paired with the exact source text of whichever node the reason is actually
  about (`source_text` — a `CallExpr`/`DeclClause` for a non-literal-argument or hidden-effect
  command, the owning `Stmt` for a backgrounded command/unsafe stdin consumer/redirect-level
  issue):
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

`source_text`, wherever it appears above, is computed on demand by `_node_text()` from a node's
AST `Pos`/`End` byte offsets against `BashCommandAnalysis.raw_command` (the original command
string, set once at construction) — see
[the per-item command-text ADR](../adrs/permission-ask-item-shows-its-own-command-text-not-the-full-compound.md)
for why each item needs its own scoped text rather than sharing the one whole-command
`command_text` every `PermissionAskItem` from the same call also carries.

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
special-cased by position), `"?"` (`OPTIONAL_TOKEN`, matching zero-or-one arbitrary token — also
always, at any position), or `"**"` (`UNBOUNDED_TOKEN`, matching any number of arbitrary tokens,
including zero, at any position). See
docs/adrs/command-rule-wildcards-double-star-unbounded-anywhere-question-mark-always-optional.md
for the reasoning behind giving "exactly one," "zero-or-one," and "unbounded" three distinct
symbols instead of overloading one of them by position.

| Rule | Matches |
| --- | --- |
| `["foo"]` | `foo` only (no args at all — "forcibly no args") |
| `["foo", "*"]` | `foo bar` only — exactly one more token, never zero, never two |
| `["foo", "?"]` | `foo` or `foo bar` — zero or one more token, never two |
| `["foo", "**"]` | `foo`, `foo bar`, `foo bar baz`, ... — unbounded, including zero |
| `["git", "status", "**"]` | `git status`, `git status -s`, `git status -s -b`, ... |
| `["git", "*", "status", "**"]` | `git <exactly-one-token> status <anything...>` |
| `["git", "**", "status", "**"]` | `git status`, `git -C dir status -s`, ... — `**` on both sides |
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
are meaningful (see `PermissionAskItem`'s own docstring). Every ask item a `BashTool` call
produces — structural or not — also carries `command_text`, the full unparsed command string,
separate from `resource_description`'s own per-item detail — see
docs/adrs/permission-ask-item-carries-raw-command-text-as-its-own-field.md — and
`item_command_text`, the `SimpleCommand`/`ForcedAskReason`/`RedirectTarget`'s own `source_text`
for just the one statement that particular item is about, distinct from every other item's from
the same call even though they all share the identical `command_text` — see
docs/adrs/permission-ask-item-shows-its-own-command-text-not-the-full-compound.md. Every ask item
also carries `intent`, identically across the whole call — see "Agent-stated intent" below.

### Agent-stated intent

`intent` is a required `BashTool` argument, separate from `command`: a short, plain-English
statement of what the command is trying to accomplish (e.g. `"List all _wait_until call sites in
test_tui_repl.py"` for a `grep -n _wait_until test_tui_repl.py` command). It is purely descriptive
— never parsed, matched against a rule, or otherwise treated as part of the command itself — and
flows alongside `command_text` everywhere that already threads through a `BashTool` call:

* `klorb.permissions.table.PermissionAskItem.intent` and `klorb.session.PermissionAskContext.
  intent` carry it onto every ask item a call produces, identically across items, the same way
  `command_text` does (see "Combining verdicts" above).
* `klorb.tui.permission_ask_panel.PermissionAskPanel` shows an "Intent: ..." line beneath the risk
  badge, above the command preview, whenever `ask_ctx.intent` is set; `format_ask_context_body()`
  includes the same line in the permanent history-scroll record `ReplApp` leaves behind once the
  panel is dismissed.
* `BashTool.summary()` — the one-line rendering any history view (approval or not) shows for a
  finished call — leads with `intent` ahead of the command itself (`Bash: <intent> ($ <command>)
  ...`), falling back to the bare command when `intent` is absent (e.g. a call whose `args` predate
  this field).
* `klorb.permissions.risk_classifier.classify_command_risk()` is given `intent` as a
  `<StatedIntent>` element alongside `<FullCommandText>` (see "LLM risk classifier" below): the
  classifier is instructed to treat a command that's deceptively different from what its own
  stated intent describes as a risk signal in its own right, raising the score and naming the
  mismatch in the rationale — independent of how risky the command would otherwise look in
  isolation.

Making `intent` required (not optional) means the risk classifier's intent-vs-command comparison
always has something to compare against, and the approval dialog/history always has a
human-readable "what for" line rather than showing it only some of the time.

### LLM risk classifier (`klorb.permissions.risk_classifier`)

Once a `BashTool` call has resolved to `"ask"`, `klorb.tui.repl.ReplApp._confirm_permission_ask`
(never `BashTool`/`Session` themselves) optionally sends the whole compound command plus every
one of its `PermissionAskItem`s to a small, cheap model, via
`klorb.permissions.risk_classifier.resolve_item_risk_assessment()`, before `PermissionAskPanel`
is shown. `resolve_item_risk_assessment()` — not `ReplApp` — owns gating (is the classifier even
enabled? is this a `BashTool` ask at all?), batching, and caching; `ReplApp` just pulls an
`ItemRiskAssessment` out of it, so any other UI layer driving `Session` (a future non-TUI
consumer, e.g. a VSCode plugin) can call the exact same function rather than re-implementing this
logic against its own UI. This is a UX layer on top of, never a replacement for, the
deterministic pipeline above: the classifier only ever runs on an item that has already resolved
to `"ask"`, and never itself promotes anything to `"allow"` or `"deny"` — see
docs/adrs/bubblewrap-is-defense-in-depth-not-a-classifier-substitute.md for the same reasoning
applied to a different probabilistic layer (`bwrap`).

When the call's `intent` is set (see "Agent-stated intent" above), it's included in the request as
a `<StatedIntent>` element alongside the command text; the classifier's system prompt instructs it
to score a command that's deceptively different from what its own stated intent describes — one
that plausibly accomplishes something broader, unrelated, or more dangerous than the intent
describes — as more risky than the command would otherwise look on its own, and to name the
mismatch explicitly in the rationale rather than just describing the command.

For each item, it returns a `risk_score` (0-10), a one-sentence plain-English `rationale`, and a
`suggested_pattern` (the same `*`/`?`/`**` token grammar `CommandPermissionsTable` matches
against) that replaces `klorb.permissions.command_grant.compute_command_grant_patterns()`'s
literal-argv fallback as the pattern a persistent-scope grant actually records, when a report is
available. A `suggested_pattern` is only trusted after it's been tested against the very command
it was proposed for: `classify_command_risk` runs each `"command"`-kind item's pattern through
`klorb.permissions.command_access.pattern_matches_argv` (the same matcher `CommandPermissionsTable`
uses at evaluation time) and blanks any pattern that doesn't actually match that item's argv, so a
hallucinated abstraction — a mistyped token, a dropped required argument, an over-narrow literal —
is never shown or persisted; a blanked pattern makes the UI fall back to the deterministic
literal-argv grant exactly as if the model had returned no pattern for that item. `PermissionAskPanel` shows the score as a badge near its header and the rationale
always in italics (additionally colored by score band) beneath the command preview; a score at or
above `tools.bash.riskClassifier.tooRiskyThreshold` pre-selects `Deny, once` as the panel's
starting cursor cell — a nudge, never a block: every grid cell stays reachable and confirmable
regardless of score.

Because `MultiPermissionAskRequired`'s several items are asked about serially, one panel at a
time (see "Multi-item asks" in docs/specs/permissions.md), `Session._resolve_multi_permission_ask`
threads the full sibling-item list onto every `PermissionAskContext` it builds
(`PermissionAskContext.sibling_items`) so `resolve_item_risk_assessment()` can classify a whole
compound command in one request the first time any of its items is looked up, caching each
`ItemRiskAssessment` in `session.tool_state["BashRiskClassifier"]` keyed by its own
`item_command_text` — every other item in the same batch, and a byte-identical item asked about
again later in the session (e.g. a retried "once" decision), reuses the cached result instead of
spending another round trip. See
docs/adrs/risk-classifier-siblings-threaded-through-permissionaskcontext.md for why the actual
classifier call site is `ReplApp` rather than `Session`, despite `Session` being where the full
item list is first available, and why the gating/batching/caching logic itself instead lives in
`klorb.permissions.risk_classifier` rather than `klorb.tui.repl`.

`tools.bash.riskClassifier.enabled` (default `true`) is a full escape hatch: `false` sends no
command text to a second LLM call at all, and behavior is exactly as if the classifier didn't
exist. `tools.bash.riskClassifier.model` (default unset) is independent of the main
conversation's own model — an ask can happen regardless of which model is driving the
conversation — and is the one model used for every classification request regardless of how
concerning the deterministic layer's own findings are; conservatism for an item carrying a
`ForcedAskReason` is achieved by varying the prompt (naming the specific reason and asking the
model to score upward), not by escalating to a costlier model. Left unset (the default), klorb
picks the model itself — see [[model-framework]]'s note on
`ModelRegistry.find_by_capability("BASH_SAFETY_EVAL")` — rather than a hardcoded literal;
setting this key explicitly always overrides that pick.
`tools.bash.riskClassifier.timeout` (default `5.0`) bounds this one request's wall-clock time,
separate from `tools.bash.timeout` (which bounds the shell command's own runtime once it
actually runs).

**Prior-decision history.** Every `classify_command_risk()` call remains a single, independent,
stateless request — no conversation with the classifier model persists across calls (see
docs/adrs/bounded-explicit-history-not-a-persistent-classifier-conversation.md for why this was
chosen over keeping the classifier itself alive as a growing per-session conversation). Instead,
right after the user's own `PermissionDecision` comes back, `ReplApp._confirm_permission_ask`
calls `klorb.permissions.risk_classifier.record_decision_history()`, which appends one
`HistoryEntry` (the item's own command text plus the rendered decision) to a plain list in
`session.tool_state["BashRiskClassifierHistory"]`, trimmed to the most recent
`tools.bash.riskClassifier.historySize` entries (default `20`) on every append. The next
`resolve_item_risk_assessment()` call for that session reads that bounded window back out and
passes it into `classify_command_risk(..., history=...)`, which renders it as a
`<PriorDecisionsHistory>` element in the user message — distinct from, and always listed ahead of,
`<CommandUnderReview>` — with the system prompt explicitly instructing the model to treat it as
calibration context only (e.g. proposing a more broadly generalized `suggested_pattern` when the
user has repeatedly approved a similar shape of command) and never as an item being scored.
`record_decision_history()` is a no-op whenever `resolve_item_risk_assessment()` itself would be
(no `command_text` on the ask, or `tools.bash.riskClassifier.enabled` is off), so nothing is
recorded for a session that never reads it back.

Structured audit logging of a command's risk assessment and of the user's own decision are both
not built yet — see the `TODO(aaron)` markers in `klorb.permissions.risk_classifier.
classify_command_risk` and `ReplApp._confirm_permission_ask` respectively. This is a separate,
durable concern from the bounded, in-memory `HistoryEntry` history above, which exists solely to
feed the classifier's own next prompt.

### Execution

`shell_lifetime` selects how long the underlying shell process lives. It's optional — a missing
key, a `None`, or an empty string all default to `"command"`:

* `"command"` — each call spawns its own fresh, non-persistent shell that exits when the command
  finishes: no `cd`, exported variable, or background job carries over to the next call.
* `"session"` — reuse this `Session`'s one live persistent shell if it has one, or create it
  otherwise, so `cd`/exported-variable/background-job state persists across calls.
* `"new"` — kill any existing persistent shell first, then create a fresh one that becomes the
  persistent shell for subsequent `"session"` calls.

`"command"` runs as `bash --rcfile ${HOME}/.bashrc -i -c "unset PS1; unset PS2; <command>"` (no
`--login`) — `-i --rcfile` is what makes bash source `~/.bashrc` despite being non-login (a plain
`bash -c` silently skips it for the vast majority of real `.bashrc` files; see
docs/adrs/bash-env-uses-clearenv-plus-shareenv-setenv-plus-forced-rcfile.md for why), so PATH/
toolchain setup gets recomputed the same way the user's own shell already does it. Two fixed,
content-independent stderr lines `bash -i` always emits with no controlling tty are stripped from
the front of captured stderr (`klorb.tools.bash._strip_bash_shell_noise`) before anything is
returned to the model. The child runs via `start_new_session=True` (`setsid()` before `exec()`),
so it has no controlling terminal at all regardless of whether klorb itself is running attached
to one — see docs/adrs/shelled-out-children-run-in-their-own-session-via-start-new-session.md.

`"session"`/`"new"` run through a different execution path entirely — see "Session-scoped
terminals" below.

**Environment**: `klorb.tools.bash.build_bash_env(session_config, bash_command)` builds an
explicit dict (never inherits the klorb process's full environment): `HOME`/`USER` always,
`WORKSPACE_ROOT` unconditionally set to the resolved workspace root (not gated on any config, so
a command can always locate the workspace regardless of `shareEnv`), `SHELL`/`BASH` both
unconditionally set to `bash_command` (`ProcessConfig.bash_command`, the actual bash binary the
command runs under — not whatever `$SHELL`/`$BASH` happen to be in klorb's own environment), then
every `SessionConfig.share_env` name (on-disk `shareEnv`, concatenated across layers) that's
actually set in the klorb process's own environment, then `SessionConfig.set_env` (on-disk
`setEnv`, merged key-by-key, later layers replacing earlier ones for the same key) as overrides
applied last.

**Timeout**: `ProcessConfig.bash_timeout_seconds` (`tools.bash.timeout`, default `120.0`) — always
enforced (unlike the REPL's `!`-prefixed `shell_timeout_seconds`, which may be `None`). On
timeout, the child's whole process group is killed with SIGKILL (`os.killpg`, not a single-pid
`kill`) — safe because `start_new_session=True` above made the child its own process group
leader — so a background job the command (or its sourced `~/.bashrc`) started doesn't survive
the timeout as an orphan.

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

### Session-scoped terminals (`shell_lifetime="session"`/`"new"`)

At most one persistent shell exists per `Session` at a time, held at
`session.tool_state["Bash"]["persistent_shell"]` (`klorb.tools.bash.PersistentShell`) — see
docs/adrs/cap-persistent-shells-at-one-per-session.md for why this first version doesn't support
addressing several concurrent persistent shells.

**Launch shape.** Unlike `"command"`, a persistent shell is launched as plain, non-interactive
`bash` — no `-i`, no `--rcfile`. `BashTool._spawn_persistent_shell` instead runs an explicit
bootstrap command through the shell's own ordinary execution channel as its first command:
`PS1=x` (satisfying the `[ -z "$PS1" ] && return` guard most real `.bashrc` files start with),
`source ~/.bashrc`, then `unset PS1 PS2` again. See
docs/adrs/persistent-shell-skips-i-flag-and-bootstraps-rcfile-itself.md for why this differs from
`"command"`'s `-i --rcfile` invocation — in short, `-i` puts an interactively-fed, never-exiting
shell into a real prompt-printing read loop, which corrupts the sentinel-delimited output stream
below; a plain non-interactive `bash` never prints a prompt at all, so there's no analogous noise
to strip.

**Command-boundary detection.** The shell never exits between commands, so there's no process-exit
signal to wait on. Each command is followed, in the same script written to the shell's stdin, by
`__klorb_ec=$?` (captured before anything else can clobber `$?`) and two `printf` statements
emitting a `__KLORB_DONE_<token>__[ <exit_code>]` line to stdout and stderr respectively, where
`<token>` is a fresh `uuid4().hex` per call. Two background reader threads relay every line from
each stream onto a shared queue; `PersistentShell._run_raw` consumes it until both sentinel lines
are seen, treating everything before them as the command's output — see
docs/adrs/sentinel-tokens-not-a-pty-delimit-persistent-shell-commands.md for the full reasoning,
including why a pty/OSC-133 approach was considered and deferred. After a command completes, the
shell's cwd is refreshed via one more sentinel-delimited `pwd` round-trip
(`PersistentShell._refresh_cwd`) and reported as `terminal_cwd` below.

**Timeout.** `tools.bash.timeout` applies the same as `"command"`. On timeout, `SIGINT` is sent to
the shell's whole process group first, with a `_TIMEOUT_GRACE_SECONDS` grace period for the
sentinel to still appear before escalating to `SIGKILL` on the process group — which ends the
persistent shell itself, not just the stuck command, since there's no way to kill only the stuck
command without a pty/job-control layer. Because the shell runs non-interactively, its own default
`SIGINT` disposition is to terminate (not survive it the way an interactive shell would), so a
plain, non-trapping timed-out command ends the shell promptly on the first signal in the common
case; only a command that explicitly makes itself immune to `SIGINT` consumes the full grace
period before the `SIGKILL` escalation.

**Death and revival.** If the shell dies (timeout escalation, the model's own `exit`, or any other
reason) the response reports `terminal_alive: false`, and `tool_state["Bash"]["persistent_shell"]`
is cleared — a following `shell_lifetime="session"` call transparently creates a brand new shell
rather than erroring.

**Standing reminder.** Whenever `BashTool` creates or reuses a persistent shell, it registers a
`"SessionTerminal"` provider via `Session.register_standing_interjection()` — a message reminding
the model it has a live terminal open (and how to keep using or close it), included on every
subsequent turn for as long as the shell stays alive, even if the model doesn't call `Bash` again
that turn. See docs/adrs/standing-interjections-complement-one-shot-for-level-triggered-state.md
for how this differs from `docs/specs/permissions.md`'s one-shot `PermissionFramework` change
interjection.

**Cleanup.** `Session.close()` kills any live persistent shell via a `"Bash"`-keyed teardown
callback `BashTool` registers through `Session.register_teardown()`; `klorb.tui.repl.ReplApp.
clear_session()` calls `close()` on the outgoing `Session` before replacing it with a fresh one,
and `PersistentShell.__init__` also registers its own `kill()` directly against `atexit`, so a
klorb process exiting normally, via `^C`, or via an uncaught exception never leaves a bash process
behind.

**Sandbox reconcile-on-grow.** A `bwrap` mount namespace is fixed at launch and cannot be modified
for the life of that process, but a session's allowed-directory set can *grow* mid-session (the
user approves an `ask`, adding an `allow`). So a persistent shell records the
`klorb.sandbox.allowed_dir_snapshot()` its live sandbox was launched with, and before each reused
command `BashTool._reconcile_sandbox` compares it to the session's current snapshot. Unchanged (the
common case) or unsandboxed (`snapshot is None`): the command runs in the existing shell, no
rebuild, `sandbox_rebuilt=false`. Grown, and the shell has no live background jobs (`jobs -p`
empty): the sandbox is transparently rebuilt against the wider tables — `SIGKILL` the old `bwrap`,
relaunch, replay cwd + exported env (`export -p`) — and the command runs in the fresh shell with
`sandbox_rebuilt=true`. Grown, but the shell *has* live background jobs a rebuild would kill: the
command does not run, the shell is left untouched, and `failure_reason` tells the model to opt into
a `shell_lifetime="new"` respawn (accepting the loss of its background work) rather than have the
harness make that call silently. See
docs/adrs/rebuild-persistent-sandbox-when-grants-grow.md and
docs/adrs/rebuild-persistent-sandbox-only-when-no-live-jobs.md. On an unsandboxed host this is
entirely a no-op — there is no namespace to go stale — and directory grants remain enforced at the
classification layer per command exactly as for the one-shot fallback.

### Response shape

`BashTool`'s result dict uses this codebase's ordinary snake_case tool-response convention:
`command`, `exit_status`, `success`,
`failure_reason` (`None` on success), `stdout`/`stderr` (`None` when spilled), `stdout_file`/
`stderr_file` (`None` when not spilled — exactly one of each inline/file pair is non-`None`),
`runtime` (seconds), and an optional `sandbox_notice` (see "Sandboxing" below). For
`shell_lifetime` in `{"session", "new"}`, the response also includes `terminal_alive` (`bool`:
whether the persistent shell is still usable after this command), `terminal_cwd` (`str | None`:
the shell's cwd via the `pwd` round-trip described above, or `None` when `terminal_alive` is
`False`), and `sandbox_rebuilt` (`bool`: whether this call transparently rebuilt a stale sandbox
to pick up a mid-session grant before running — see "Session-scoped terminals" below).

## Sandboxing

`klorb.sandbox.bwrap_available()` runs a cheap, self-contained smoke test
(`bwrap --ro-bind / / --proc /proc --dev /dev -- true`) once per process, cached — the real
question is "can `bwrap` actually create a sandbox right now," not any `/proc`/`/.dockerenv`
environment fingerprinting (those are only used to *word* the fallback notice, never to make the
go/no-go decision).

When it can, `klorb.sandbox.build_bwrap_argv()` assembles the `bwrap ... --` argv prefix
`BashTool` wraps the shell invocation in, derived from the *same* `readDirs`/`writeDirs` the file
tools use (`klorb.sandbox.compute_sandbox_dirs` — one source of truth, not a second parallel
filesystem policy):

* Namespaces `--unshare-net` (all network denied until a proxy exists), `--unshare-ipc`,
  `--unshare-pid`, `--unshare-uts`, `--unshare-cgroup`, plus `--unshare-user`/`--disable-userns`
  with an identity uid/gid map so files the command creates in the binds stay owned by the real
  user — the explicit `--unshare-user` is required by `--disable-userns` on real `bwrap`, contrary
  to the plan's original guidance; see
  docs/adrs/pass-unshare-user-because-disable-userns-requires-it.md.
* Hardening `--hostname klorb-host`, `--die-with-parent`, `--new-session`, `--cap-drop ALL`; a
  `--clearenv` + one `--setenv` per `build_bash_env()` entry so the sandbox starts from exactly
  that dict.
* Whole-tree read-only `/usr`+`/etc` binds and the host's own merged-`/usr` symlink layout; a
  read-write whole-tree `$HOME` bind (the mechanism by which toolchains under `$HOME` — nvm,
  pyenv, cargo, ... — reach the sandbox), with the sensitive subdirectories masked back out;
  disposable `--tmpfs /tmp`, `--tmpfs /var`, `--dev /dev`, `--proc /proc`; read-write
  binds for the workspace root (when trusted) and every `writeDirs.allow` entry, read-only binds
  for `readDirs.allow` entries; PATH-derived read-only top-up binds for toolchains outside
  `/usr`/`$HOME`; then an empty `--tmpfs` mask over every `readDirs.deny` entry and every
  `privileged_dirs()` entry (`<workspace>/.klorb`, the klorb config/data/state dirs) — masked with
  `--tmpfs` rather than empty-placeholder binds, so no host-side placeholder needs cleanup; see
  docs/adrs/mask-sandbox-denyholes-with-tmpfs-not-placeholder-binds.md.
* **Individual files.** The same one-source-of-truth idea extends to `readFiles`/`writeFiles`,
  which name single files by exact path (`klorb.permissions.file_access`) rather than directories.
  An existing `readFiles.deny` file that lands inside a bound directory (e.g. `~/.git-credentials`
  sitting right in the read-write `$HOME` bind, where masking the whole parent would be far too
  broad) is masked with `--ro-bind /dev/null <file>`, the standard bwrap file-mask idiom — its
  content reads back as inaccessible while its siblings in the same directory stay readable. The
  mirror also holds: an existing `readFiles.allow`/`writeFiles.allow` file that *isn't* already
  reachable through a directory bind (a config file or device node the user allowed outside the
  workspace, or a single file explicitly allowed inside an otherwise-masked directory) is bound
  into place read-only/read-write, synthesizing any missing parent directories with `--dir` — so
  an exact file grant actually works inside the sandbox instead of failing with `ENOENT`. Both are
  applied after the directory binds/masks (so they win), file binds before deny-masks (so a deny
  still wins over a stray allow), and only for files that exist on disk (a `--*-bind` needs a real
  source). `klorb.resources.default-config.json` ships `readFiles.deny` entries for the common
  single-file home-directory credential stores — `~/.git-credentials`, `~/.netrc`, `~/.npmrc`,
  `~/.pypirc`, `~/.pgpass`, `~/.my.cnf` — that live directly in `$HOME` alongside unremarkable
  files.
* `--chdir workspace_root`.

Signal deaths inside the sandbox surface through the extra `bwrap` → `bash` → target layer as an
ordinary positive `128 + signum` exit code, decoded by `klorb.tools.bash._decode_exit` the same as
the unsandboxed forked-child case; timeout/`^C` teardown always `SIGKILL`s the outer `bwrap`
(destroying its pid namespace, which reaps everything inside).

**Fallback.** When `bwrap_available()` is `False`, `BashTool` runs the same command unsandboxed.
The first time that happens in a given session, the result carries a `sandbox_notice` string
explaining why (missing `bwrap` binary — also naming the `apt-get install bubblewrap` fix and that
klorb needs restarting afterward — or this environment disallowing unprivileged user namespaces,
common inside Docker/cloud-agent environments) and noting that command/redirect permission checks
are still fully enforced regardless. What's genuinely lost is the OS-level backstop on filesystem
access: `evaluate_write()` still gates an explicit redirect and `CommandRules` still gates argv0/
args, but neither can see what an *approved* command does with its own `open()`/`write()` calls —
a `python -c "..."` one-liner or a compiled binary can read or write anything the sandboxed
boundary would otherwise have prevented. The notice is never repeated for the rest of that
session: `session.tool_state["Bash"]["sandbox_warned"]` (see docs/specs/tool-framework.md's
`ToolSetupContext.session`/`Session.tool_state` description) tracks whether it's already fired,
naturally resetting on `/clear` since a fresh `Session` (and thus a fresh, empty `tool_state`) is
created then. A `BashTool` built from a `ToolSetupContext` with no real `Session` attached (e.g. a
caller that constructs one directly, without going through `Session`) has no `tool_state` to
dedupe against, so it shows the notice on every call instead of silently dropping it.

## Configuration

```json
{
  "sessionDefaults": {
    "commandRules": {
      "deny": [["rm", "-rf", "/"]],
      "ask": [["git", "push", "**"]],
      "allow": [["git", "**"], ["ls", "**"], ["cat", "**"]]
    },
    "shareEnv": ["NVM_DIR", "PYENV_ROOT"],
    "setEnv": {"CI": "true"}
  },
  "tools.bash.command": "/bin/bash",
  "tools.bash.timeout": 120.0,
  "tools.bash.spillBytes": 8192,
  "tools.bash.shfmtCommand": "shfmt",
  "tools.bash.riskClassifier.enabled": true,
  "tools.bash.riskClassifier.model": "openai/gpt-5-nano",
  "tools.bash.riskClassifier.timeout": 5.0,
  "tools.bash.riskClassifier.tooRiskyThreshold": 9,
  "tools.bash.riskClassifier.historySize": 20
}
```

`commandRules`/`shareEnv` merge by concatenation across config layers; `setEnv` merges key-by-key
(a later layer's value for a key replaces an earlier layer's) — see
docs/specs/process-and-session-config.md's "On-disk key naming" section for the full merge-mode
taxonomy this adds a third example of alongside `readDirs`/`writeDirs`.

## Out of scope

* Network egress permissioning (`TODO.md`'s "website access" item) — `bwrap --unshare-net` denies
  all network access unconditionally today; no domain-allowlist/proxy mechanism is designed yet.
* Growing a live persistent sandbox's mounts *in place* (rather than rebuilding it) via a
  privileged in-namespace mount helper — rejected as contradicting `--cap-drop ALL`; see
  docs/adrs/rebuild-persistent-sandbox-when-grants-grow.md.
* Structured audit logging of command requests/decisions/outcomes — a real goal, not required for
  this first version.
* macOS support (`sandbox-exec`/Seatbelt in place of `bwrap`) — Linux only.
* More than one concurrent session-scoped persistent shell per `Session`, and how the model would
  address a specific one — see docs/adrs/cap-persistent-shells-at-one-per-session.md.
* A real pty and OSC-133-style shell-integration escape codes for more robust persistent-shell
  command-boundary detection and support for interactive programs through that channel — see
  docs/adrs/sentinel-tokens-not-a-pty-delimit-persistent-shell-commands.md.

## See also

* docs/specs/permissions.md — the shared `PermissionsTable` abstraction, the interactive "ask"
  confirmation flow (including multi-item asks and the permission grid), and the directory-access
  resource kind this doc's `CommandPermissionsTable` mirrors.
* docs/adrs/shell-out-to-shfmt-for-bash-parsing.md
* docs/adrs/reject-trap-debug-as-a-security-boundary.md
* docs/adrs/bubblewrap-is-defense-in-depth-not-a-classifier-substitute.md
* docs/adrs/pass-unshare-user-because-disable-userns-requires-it.md
* docs/adrs/mask-sandbox-denyholes-with-tmpfs-not-placeholder-binds.md
* docs/adrs/rebuild-persistent-sandbox-when-grants-grow.md
* docs/adrs/rebuild-persistent-sandbox-only-when-no-live-jobs.md
* docs/adrs/command-rules-mirror-dirrules-deny-ask-allow-evaluation.md
* docs/adrs/command-rule-wildcards-double-star-unbounded-anywhere-question-mark-always-optional.md
* docs/adrs/bash-env-uses-clearenv-plus-shareenv-setenv-plus-forced-rcfile.md
* docs/adrs/shelled-out-children-run-in-their-own-session-via-start-new-session.md
* docs/adrs/ask-independent-items-serially-not-just-the-strictest.md
* docs/adrs/generalize-permission-override-to-a-set-of-resources.md
* docs/adrs/generalize-grant-writer-for-deny-and-mirror-it-for-commandrules.md
* docs/adrs/permission-ask-item-carries-raw-command-text-as-its-own-field.md
* docs/adrs/permission-ask-screen-shows-a-header-command-preview-and-detail.md
* docs/adrs/persistent-shell-skips-i-flag-and-bootstraps-rcfile-itself.md
* docs/adrs/sentinel-tokens-not-a-pty-delimit-persistent-shell-commands.md
* docs/adrs/standing-interjections-complement-one-shot-for-level-triggered-state.md
* docs/adrs/cap-persistent-shells-at-one-per-session.md
* docs/adrs/risk-classifier-siblings-threaded-through-permissionaskcontext.md
* docs/adrs/bash-tool-requires-a-stated-intent-argument.md
* docs/adrs/bounded-explicit-history-not-a-persistent-classifier-conversation.md
