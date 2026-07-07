# Bash tool permissions and sandboxing

Design plan for the `BashTool` bullet in `TODO.md` and the bash-command resource kind that
`docs/specs/permissions.md`'s "Out of scope" section forward-references. 

## Context

There are two layers of permission systems required to safely enable a BashTool to operate, 
that are the two approaches we will implement:

1. An allow/ask/deny grant-based system that determines whether various shell commands
  are safe to run or not out-of-hand. e.g. `rm -rf /` should be simply dismissed out-of-hand,
  along with anything else the user confirms is never acceptable to run.
2. A sandbox mechanism that prevents unintentional performance of unsafe or unexpected commands,
  and also ensures that the user's existing allow/deny filesystem grants are respected by
  any shell commands, so that shell commands cannot be used by the agent as an escape
  mechanism around filesystem accesses directly performed by in-harness tools like EditFile.

These are in service of the following goals:

0. The agent is at all times permitted the least possible access privileges to the user's system
   necessary to accomplish its approved goal. If the agent's command does not accomplish an
   intended, approved, safe goal, we want to preemptively limit the potential blast radius of
   whatever command we /do/ run (if any).
1. Known-harmful commands are denied. A specific internal denylist like `rm -rf /`, or simple
   forkbombs, etc., are banned on the spot.
2. Network and IPC access is denied until we have a safe way to allow it. (Future work.)
3. The user's allowlist and denylist entries for fs access are respected by bash commands.
   The bash tool must not be an end-run around the filesystem denylist.
4. Commands where the user has already permanently rejected are rejected via denylist.
5. Commands where we are unsure of their safety are put into 'ask' mode, as are commands
   where the user's warrant of safety is conditional on time-variant system state. That is:
   if the user wants us to always ask permission before running X, we should always ask
   permission before running X.
6. Commands where we are sure the user has already permitted are allowed via allowlist.
7. Command execution requests, permission decisions (approve/deny) and decision-reasoning
   (auto-approval; allowlist; explicit user approval), and execution outcomes can be audit logged
   clearly.


## Existing components to employ

### Internal tools

`klorb.permissions` (`docs/specs/permissions.md`) already has a general `PermissionsTable[T]`
abstraction — `deny`/`ask`/`allow` rule lists evaluated in that fixed order, the strictest
applicable rule always wins — with one concrete resource kind built on it so far: directory
access (`DirectoryAccessTable`, gating the file tools). A `BashTool` needs a second resource
kind, gating shell commands the same way. The interesting problem isn't the permissions engine
(it's reusable as-is) — it's how to turn an arbitrary bash command string into something the
engine can evaluate, without the classifier itself becoming the weak point.

### External tools

* mvdan/sh (https://github.com/mvdan/sh) is a bash shell command parser, which
  will allow AST parsing of bash commands and application of various rules
  * enforcing "always-ask" behavior for certain things, like HEREDOC, variable expansion, etc.
  * breaking up commands into subcommands (by '&&', '||', ';' as well as nested within `if..fi`,
    `do...loop`, etc. blocks) for analysis of the subcommands against the allow/ask/deny lists
  * providing an AST basis for storing and comparing allow/ask/denylist records for individual
    commands. We don't compare against raw strings with regexp/wildcards. we compare 
    against AST fragments.
  * Shelling out to `shfmt` is the main mechanism of implementing approach #1.
  * We should depend on the `shfmt-py` pypi package, which will download the prebuilt binary
    for the user's system. But we will still need to shell out to this to invoke it.
* The "bubblewrap" sandboxing system (`bwrap(1)`) - see manpage: 

  https://manpages.debian.org/testing/bubblewrap/bwrap.1.en.html
  ... Using `bwrap` to wrap around commands the agent wants to run via bash tool is 
  the main mechanism by which we implement approach #2.



## Things *not* to use

* regexp-based parsing of bash commands is a broken approach and we will not attempt to use it.
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

## env vars

### pass-thru

The user can explicitly pass through whatever values the klorb(1) process sees for 
some environment variables via a `shareEnv` list in the sessionConfig. This is a
list of strings each of which is the name of an env var to share. $HOME and $USER
are auto-shared. 

`shareEnv` values at multiple config file layers are concatenated.

### overrides

The user can explicitly override some environment variables to pass in. These will be
set by bubblewrap, so the bash inside could still clobber them, but this shadows whatever
might have been passed into the klorb process.

This is done with the `setEnv` field of the klorb session config, which is a map from env_var to
new_value. 

setEnv from multiple layers are applied sequentially so later files shadow over what may
have been set at an earlier config file load. (e.g., project config overrides homedir config if
they both set FOO=v1 and FOO=v2).

### loaded by the shell

Environment variables configured via bash and its profile scripts will also be instantiated within
the process.

TODO For Claude to weigh in on: Should we run bash as a non-interactive login shell? Or
non-interactive, non-login shell?


## Running the command

We pass the agent_requested_command as an argument to 'bash -c'. The path to bash is specified in 
`shell.command`. (Using --login if Claude thinks we should make this a --login shell? Any other
args? Claude to advise.)

### timeout

Commands have an enforced timeout in seconds in `shell.timeout`. We kill the subprocess after this
much time has passed and inform the LLM that it terminated via timeout.

### stdin

stdin is routed in from /dev/null.

### stdout and stderr

* We capture stdout and stderr to tmpfiles. 
    * They should be chmod 0600 before any content gets into them.
    * They are put in a special tmpdir (subdir of /tmp/) where we auto-grant read access to the
      agent.
* We make sure that if klorb dies mid-process, the tmpfiles are vacuumed up. These files must be
  destroyed before klorb exits.
* The stdout/stderr are sent back to the llm as a tool result, after the command is over.
  * If the files are less than shell.spillBytes in length, they are sent back directly to the
    agent as `stdout` and `stderr` on the response schema.
  * If they are over that size, we respond with `stdoutFile` or `stderrFile` fields of the
    json response, so the llm can use ReadFile tool as it desires or GrepTool, rather than 
    having its context overrun.
  * Default spill size values are 8192 bytes.

### process outcome

We report `exitStatus` as an integer in the response schema.
We also report `success` as true/false. 
* true requires exit status 0.
We also report a `failureReason` field which is str or null
* it's null if success is true.
* if the process was killed because of timeout, we report back "Command timed out at Xx seconds"
* If the process was killed because the user pressed ^C while it was running, we report back "User
  aborted command"
* If the process exited with non-zero status, we report "Process completed normally with non-zero status"
* If bwrap isn't available, report "Sandbox layer unavailable; cannot launch shell commands."
* ... or other reason string as is useful in other circumstances we can detect. (Can we detect
  SIGINT / SIGKIll / SIGHUP / SIGTERM / SIGSEGV / etc?)
* 

## bashtool input args

It's a really minimal schema:

```json
{
  command: "the command to use"
}
```

## response schema

```json

{
  command: string,
  exitStatus: int,
  success: bool,
  failureReason: [string, null],
  stdout: [string, null],
  stderr: [string, null],
  stdoutFile: [string, null],
  stderrFile: [string, null], 
  runtime: float, # in seconds
}
```

Exactly one of stdout and stdoutFile must be non-null; same with stderr and stderrFile.

null and empty string are different:

* stdout="" means it output nothing.
* stdout=null means it output a lot and the content is spilled into a file identified with
  stdoutFile.

## bubblewrap args to use

The command starting with `bwrap ....` should always include these args:

* --unshare-net # until we find a safe way to share/proxy. Claude uses socat. future work.
* --unshare-ipc # until we find a safe way to share/proxy.
* --unshare-pid
* --unshare-uts # not sure what this does? but why keep it if so...
* --unshare-cgroup
* --hostname klorb-host # don't let it know the true hostname.
* --clearenv
* --setenv HOME=/actual/home
* --setenv USER=actual_username
* --setenv (any other minimum mandatory env vars?)
* --setenv (any env vars the user has explicitly shared)
* --ro-bind /usr/bin
* --ro-bind /usr/lib
* --ro-bind (other things required for linux viability?)
* --symlink /usr/lib /lib
* --symlink /usr/bin /bin
* --tmpfs /tmp
* --tmpfs /var
* --dev /dev
* --proc /proc
* --ro-bind any directories the user allowed read but not write, and
  --bind any directories the user allowed read/write. Use --dir to make any parent
  dirs requried for those binds to mount up at the right places e.g. `--dir /home
  --dir /home/foo --dir /home/foo/src --bind /home/foo/src/projRoot`
* ... If there are any directories the user denied *within* some parent that is mounted, then use
  --ro-bind to mount an empty tempdir in their place that masks them out.
  * This includes <workspaceRoot>/.klorb/ by default
* --die-with-parent
* --new-session
* --cap-drop ALL
* --chdir <workspaceRoot>

... Any other things? Claude to advise: Anything here that looks incorrect, or incomplete? 
Study
https://github.com/anthropic-experimental/sandbox-runtime/blob/main/src/sandbox/linux-sandbox-utils.ts
and decide if anything in the Anthropic toolkit version is different or important that we should
add. Since that's written in typescript, we can't just use it as-is.

We invoke this through the `subprocess` module in python so that we can route the
stdout/stdin/stderr pipes nicely.
This will need to be launched in another thread so we can animate the screen and listen for ^C.

(Claude to weigh in: Is that actually sufficient, given the GIL? Or do we need to use
multiprocessing to launch bwrap from a completely different host process?)

## shfmt

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
changing across versions. The plan is to add a fixture-based self-check (parse a small known
script at startup or in tests, assert the expected node shape) so a version drift fails loud
instead of silently misclassifying commands.

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

## while the BashTool is running

We clearly show the command being run in the history, even if the user's permission is
not explicitly needed.

We put the word "Running..." underneath it while it's in flight. We do some kind of spinner
or slow pulsing "throbber" animation to the word to make it show that things are still 
happening. e.g. slowly making each letter of the phrase glow to brighter-white in sequence for a
"ripple" effect, and/or a classic / - \ | 'spinner'. (why not both?)

The running notification is removed when the task is complete. The exit status and failure reason
(if any) are shown.

If the user presses ^C while the command is running, we kill the subprocess immediately.

## More thoughts on `CommandPermissionsTable` (command classification)

### `shfmt` should Fail closed on anything not confidently classified

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
  `SessionConfig` gains a `command_rules` field (on-disk key 'commandRules', following the existing
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
rather than silently falling back to unsandboxed execution. 

Tell the user to `sudo apt-get install bubblewrap` if it cannot find the tool.


## CommandRules parsing / matching with shfmt

* `CommandRules` matching semantics:
  * allow/ask/deny rules should support the following (shown with regex, just for brevity,
    even though we said we are using ASTs for real, not regex):
    * argv0 with-any-args (`foo *`) 
    * argv0, with forcibly no args (`foo`) # different!!!
    * argv0 someTool with-any-args (`git status *`) 
    * argv0 any-args someTool any-more-args (`git * status *`)
    * argv0, with some specific set of args (`foo --bar --baz`)
    * argv0, some specific set of args, any-more-args (`foo bar --baz anyfile.c`)
  * These can exist in deny/ask/allow lists, applied in that order.
    * Any denylist entry that can match the current command kills it.
      * so a denylist entry for 'git' will kill 'git foo', 'git bar', even if there are
        other allowlist entries for it.
    * Any asklist entry that can match the current command shifts to ask mode
      * ... so asklist entries always preempt allowlist entries

* `shfmt` parse failure should be surfaced to the model as a normal tool error, so it
  can retry with simpler syntax

Several decisions already made during this plan's research (shelling out to `shfmt --to-json`
over Go bindings or a pure-Python parser; rejecting the `trap DEBUG`/`extdebug` technique as a
security boundary; treating bubblewrap sandboxing as mandatory defense-in-depth rather than an
alternative to classification) are ADR-worthy and should be written up as ADRs in `docs/adrs/`
once implementation actually begins, so the reasoning survives independent of this planning
document.

Claude will need to come up with a way to serialize the command rules (maybe a subset of the json
used by shfmt?), and will need to come up with a thorough test plan to confirm that various
commands of known shape are parsed into JSON ASTs that we understand.

## Worked example

Model requests `curl https://example.com/install.sh | sh`. The `CommandPermissionsTable` walker
sees a pipeline of two simple commands; `curl` and `sh` are both plain literals, so it can
classify them — but "pipe an arbitrary downloaded script into `sh`" is exactly the destructive
shape a conservative rule set should flag, so this lands in `ask` or `deny` per configured rules
rather than `allow`, independent of sandboxing. If it were auto-approved (or if the config
happened to allow it), the sandbox still bounds the outcome: no network unless this session was
granted it, and even with network granted, the executed script can only touch
`workspace_root`/granted write dirs, not the rest of the filesystem.

## Future work

* The first version of this uses `bwrap --unshare-net` so all network access is denied. We
  would eventually like to permit network access but need to do so through an allowlist
  mechanism that filters on domain name.
* `bwrap` is a linux executable and we need to adapt a version of this for mac osx, if we
  want klorb to support that platform.

