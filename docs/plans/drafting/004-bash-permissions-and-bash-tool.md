# Bash tool permissions and sandboxing

Design plan for the `BashTool` bullet in `TODO.md` and the bash-command resource kind that
`docs/specs/permissions.md`'s "Out of scope" section forward-references.


Claude: This plan is **partially ready**.
- shfmt / command parsing: ready
- bashtool overarching implementation / availability: ready
- permissions allowlist/denylist: ready
- running commands without bubblewrap: ready
- everything to do with sessionConfig / config files: ready

The only thing that is **not ready** is actually building bubblewrap command lines for sandboxing of
subprocesse. Since we are currently doing all our work in cloud environments or dev containers,
bubblewrap does not work in those environments, and thus we cannot properly develop the command line
for them. So we should implement all the **other** parts of this project, i.e., "everywhere klorb
would 'fall back' to a non-bubblewrapped execution, make it work, and the _actual_ do-the-bubblewrap
part should just be left as a stub for now."


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

### loaded by the shell (rc-file sourcing)

Requiring the user to enumerate every toolchain-related environment variable (`NVM_DIR`,
`PYENV_ROOT`, `CARGO_HOME`, ...) in `shareEnv` is too cumbersome — most of these are set up by
whatever lines the user already has in `~/.bashrc` for their own interactive shell, and that
logic shouldn't need to be duplicated into klorb config by hand. The bash invocation should run
non-login (so `/etc/profile`/`~/.bash_profile` are never read) but should still source
`~/.bashrc`, so PATH/toolchain setup gets recomputed the same way the user's own shell already
does it, using whatever rc/init files are visible through the (now read-write, see "bubblewrap
args to use" below) homedir bind.

This is trickier than it sounds because of how bash decides whether to read `~/.bashrc` at all.
Tested directly against this repo's own dev environment:

* Pointing `BASH_ENV` at `~/.bashrc` for a plain `bash -c` invocation **silently does nothing**
  for the overwhelming majority of real `.bashrc` files, because the standard Debian/Ubuntu
  skeleton (and countless files copying its idiom) begins with `[ -z "$PS1" ] && return` — and
  `$PS1` is never set for a `bash -c` invocation, `BASH_ENV` or not. Confirmed with a canary
  variable placed after that guard line: it never gets set when sourced via `BASH_ENV`.
* `bash --rcfile ~/.bashrc -i -c "<command>"` **does** work — `-i` causes bash to set `$PS1` to a
  non-empty default before running rc files, which satisfies the guard, and the same canary
  variable reliably gets set this way. This is the mechanism to use.
* The unavoidable cost of `-i` with no controlling tty (stdin is `/dev/null` per this plan's
  design) is two fixed, deterministic bash-internal stderr lines on every single invocation:
  `bash: cannot set terminal process group (-1): Inappropriate ioctl for device` and
  `bash: no job control in this shell`. These must be stripped from the front of captured stderr
  before it's returned to the model — via an exact-string match on these two specific, well-known
  bash-internal messages, not a general regex/lexical classifier. This is filtering known
  harness-induced noise (a side effect of the no-controlling-tty design), not classifying a
  command's safety, so it does not conflict with this plan's "no regexp-based classification"
  rule elsewhere — that rule is about deciding what a command is *allowed to do*, not about
  scrubbing a fixed, content-independent string bash itself always prints in this exact
  configuration.
* `-i` making bash "interactive" is a property of bash's *own* behavior (job control, alias/
  history expansion, and reading rc files) — it is unrelated to, and does not fake, whether
  child processes see a real controlling terminal. Verified directly: a Python child process
  launched under `bash --rcfile ~/.bashrc -i -c "..."` with stdin from `/dev/null` correctly
  reports `isatty()` as `False` on stdin/stdout/stderr, identical to the non-`-i` case — so
  well-behaved tools (git, npm, pip, etc.) that check `isatty()` before deciding whether to launch
  an editor, prompt interactively, or emit color/ANSI codes will behave the same as they would
  under any other non-interactive/CI invocation. `isatty()` is the check that matters here and it
  comes back correctly negative regardless of `-i`.
* One narrower, verified wrinkle: unlike `isatty()`, whether `$PS1` itself ends up *exported* into
  the target command's own child processes' environment depends on what the sourced `~/.bashrc`
  (or the distro's `/etc/bash.bashrc`, also read for interactive shells) does — on this repo's own
  dev machine, sourcing the real `~/.bashrc` under `-i` does export `PS1`, even though a plain
  `bash -c` (no `-i`) never does. This only matters if some downstream tool uses "is `$PS1` set"
  as its own (nonstandard, rare) interactivity heuristic rather than `isatty()`, but since it's
  cheap to close off entirely: prepend `unset PS1; unset PS2; ` to the actual command string
  within the same `-c` argument, *after* rc-file sourcing has already happened but before the
  model's requested command runs — verified this reliably removes `PS1` from the target command's
  own child environment without needing `--rcfile`/`-i` changes.

So: the bash invocation is `bash --rcfile ${HOME}/.bashrc -i -c "<command>"` (no `--login`),
started from an environment built by `--clearenv` plus the `setEnv`/`shareEnv`-derived variables
described above — `~/.bashrc` then runs inside the sandbox on top of that clean baseline and
fills in PATH/toolchain variables the same way it would for the user's own shell, using the
mounted-through home directory. `shareEnv`/`setEnv` remain useful for anything that genuinely
isn't derivable by re-running `.bashrc` (e.g. ambient session-only state like `SSH_AUTH_SOCK`),
but no longer need to enumerate ordinary toolchain setup.


## Running the command

We pass the agent_requested_command as an argument to `bash -c`. The path to bash is specified in
`tools.bash.command`. Invocation is `bash --rcfile ${HOME}/.bashrc -i -c "<command>"` — no
`--login`, so `/etc/profile`/`~/.bash_profile` are never read, but `-i --rcfile` deliberately
forces `~/.bashrc` to be sourced despite being non-login (see "env vars" above for why, and for
the resulting stderr-stripping requirement).

### timeout

Commands have an enforced timeout in seconds in `tools.bash.timeout`. We kill the subprocess after
this much time has passed and inform the LLM that it terminated via timeout.

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
  * If the files are less than `tools.bash.spillBytes` in length, they are sent back directly to
    the agent as `stdout` and `stderr` on the response schema.
  * If they are over that size, we respond with `stdoutFile` or `stderrFile` fields of the
    json response, so the llm can use ReadFile tool as it desires or GrepTool, rather than
    having its context overrun.
  * Default spill size values are 8192 bytes.
  * The stdout/stderr files live in a per-invocation directory created fresh via `mkdtemp()` for
    this one `BashTool` call. Since nothing else is ever placed in that directory, a read grant
    scoped to exactly that directory can be attached automatically for the lifetime of the tool
    call with no risk of it covering anything beyond this invocation's own two output files.

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
* If bwrap isn't available at all (binary not found), report "Sandbox layer unavailable; cannot
  launch shell commands. Run `sudo apt-get install bubblewrap`."
* If bwrap is present but refuses to create a sandbox (unprivileged user namespaces unavailable —
  confirmed directly against this repo's own dev environment, which fails this way when run
  inside a nested container), report a distinct reason: "Sandbox layer unavailable: this
  environment does not permit unprivileged sandboxing (nested container or restrictive kernel
  policy). BashTool cannot run here." These two are different failures with different fixes
  (install the package vs. reconfigure the host/outer-container security policy), so must not
  share one generic message.
* Signal detection: yes, this is detectable, but not via Python's usual negative-`returncode`
  convention, because bwrap sits between Python and the actual command (bwrap -> bash -> the
  target process, e.g. `git`). Verified directly: when a process dies from an uncaught signal,
  its immediate parent shell reports `128 + signum` as *its own* (normal, non-signaled) exit
  status, not the raw signal — confirmed with `bash -c 'kill -SEGV $$'` invoked as a child of
  another bash, which reports `$?` == 139 while the parent bash itself keeps running normally.
  `bwrap`'s own default reaper (see "bubblewrap args to use" below) is documented to behave the
  same way, propagating a signaled child's death as its own ordinary `128+signum` exit. So the
  chain is: `git` dies of SIGSEGV (a real signal) -> `bash -c` observes this and itself *normally*
  exits 139 -> bwrap's reaper observes bash's normal exit(139) and itself *normally* exits 139 ->
  Python's `subprocess.Popen` sees a **positive** 139 return code from its direct child (bwrap),
  not a negative one. Only a signal delivered directly to the tracked `subprocess.Popen` object
  (i.e. klorb killing bwrap itself for timeout/^C — already covered by the two dedicated reason
  strings above) would show up as a negative Python return code. So: decode
  `bwrap_exit_code > 128` as `signal.Signals(bwrap_exit_code - 128).name` for "the sandboxed
  command died by a signal" (SIGSEGV, SIGABRT, etc. — something *inside* the sandbox died, not
  something klorb itself killed); this should be validated with a fixture test (self-signaling
  child run through the real bwrap sandbox, once available) rather than trusted on documentation
  alone. Prefer bwrap's `--json-status-fd`, if its schema turns out to report the sandboxed
  command's own exit/signal status directly, over inferring from the aggregate process exit
  code — investigate its exact schema during implementation.
* Always kill the outer bwrap process with **SIGKILL**, never SIGTERM, for timeout/^C teardown.
  `--unshare-pid` puts the sandboxed command at PID 2 under a default reaper at PID 1 (see
  "bubblewrap args to use" below), and PID 1 processes can silently ignore signals they haven't
  installed a handler for; SIGKILL can't be ignored by any process regardless of PID, so it's the
  only reliable way to guarantee teardown. This also matches bwrap's own documented behavior for
  `--die-with-parent`, which is specifically defined in terms of SIGKILL.

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
* --unshare-uts # required: `--hostname` only takes effect with its own UTS namespace, since
  changing the apparent hostname needs CAP_SYS_ADMIN and would otherwise mutate the real host's
  hostname for every other process sharing that namespace.
* --unshare-cgroup
* --disable-userns # prevents the sandboxed process from creating further nested user namespaces
  itself (defense-in-depth against a common container-escape/nesting technique); confirmed via
  `bwrap --help` to be a real, independent flag.
* --hostname klorb-host # don't let it know the true hostname.
* --clearenv
* --setenv HOME=/actual/home
* --setenv USER=actual_username
* --setenv (any other minimum mandatory env vars?)
* --setenv (any env vars the user has explicitly shared)
* --ro-bind /usr /usr # whole tree, not just /usr/bin and /usr/lib — this also picks up
  /usr/local and /usr/share, which real dev toolchains lean on constantly.
* --ro-bind /etc /etc # whole tree. Linux's own file permissions already protect /etc from
  unwanted writes; read access to all of it (locale data, nsswitch.conf, ssl certs, passwd/group
  for uid/username lookups, etc.) avoids a long tail of "some libc call mysteriously fails inside
  the sandbox" bugs that are hard for the model to self-diagnose.
* merged-/usr handling, conditional on the host's actual layout (check via `readlink` at launch
  time, don't hardcode):
  * If the host already has `/lib` -> `/usr/lib` and `/bin` -> `/usr/bin` as symlinks (true on
    most modern Debian/Ubuntu/Fedora installs): `--symlink /usr/lib /lib` and
    `--symlink /usr/bin /bin` (and their multiarch/lib64 equivalents as applicable).
  * Otherwise (non-merged-`/usr` host, `/lib`/`/bin` are real separate directories): mount them
    directly as their own binds instead — `--ro-bind /lib /lib`, `--ro-bind /bin /bin` — matching
    the host's actual layout rather than synthesizing a merged view that isn't real.
* --bind $HOME $HOME # read-write, whole tree. This is the answer to "how do toolchains outside
  /usr get in" (nvm, pyenv, rbenv, cargo, etc. all live under the homedir) — rather than trying
  to enumerate every possible toolchain-install location, mount the whole home directory and
  punch read-only or fully-masked holes over the specific things that are too sensitive to expose
  by default:
  * --ro-bind or --tmpfs over: ~/.ssh, ~/.aws, ~/.gnupg, ~/.config/gcloud, ~/.netrc, ~/.docker
    (credential helper config) — masked by default.
  * Anything already covered by the existing `is_privileged_path()`/`privileged_dirs()`
    mechanism (e.g. `~/.klorb/`) is masked the same way `<workspaceRoot>/.klorb/` already is —
    this reuses the *same* deny-list source of truth already used elsewhere in
    `klorb.permissions`, rather than inventing a second, bwrap-only hardcoded list.
* PATH-derived ro-binds: walk the resolved `$PATH` (after `shareEnv`/rc-file processing — see "env
  vars") and `--ro-bind` any existing directory not already covered by the `/usr`, `/etc`, or
  `$HOME` binds above (e.g. `/opt/sometoolchain/bin`). This is now a narrow top-up rather than the
  primary mechanism, since whole-tree `/usr` and `$HOME` binds already cover the common cases.
* --tmpfs /tmp
* --tmpfs /var
* --dev /dev
* --proc /proc
* --ro-bind any directories the user allowed read but not write, and
  --bind any directories the user allowed read/write, beyond the workspace root and homedir
  already covered above. Use --dir to make any parent dirs required for those binds to mount up
  at the right places e.g. `--dir /home --dir /home/foo --dir /home/foo/src
  --bind /home/foo/src/projRoot`
* ... If there are any directories the user denied *within* some parent that is mounted, then use
  --ro-bind to mount an empty tempdir in their place that masks them out.
  * This includes <workspaceRoot>/.klorb/ by default
* --die-with-parent
* --new-session
* --cap-drop ALL
* --chdir <workspaceRoot>

**uid/gid**: do *not* pass `--unshare-user` or `--uid`/`--gid` explicitly. Per `bwrap --help`,
unprivileged (non-setuid) bwrap creates a user namespace implicitly as needed regardless of
whether `--unshare-user` is passed — that's how it gains the privilege to set up its other
namespaces/mounts at all — and its default behavior in that implicit namespace is an identity
mapping (current uid/gid map to themselves). That's exactly what's wanted: files the sandboxed
command creates in the workspace/homedir binds end up owned by the real user with no extra
id-mapping bookkeeping needed. `--uid`/`--gid` only matter if the sandboxed process should
*appear* as a different uid than the real one, which isn't a goal here.

**Mount-point cleanup**: masking a path with an empty placeholder directory/file requires bwrap
to create that placeholder on the host as a bind target, and these persist after the sandbox
exits unless explicitly removed. Both (a) clean up proactively as soon as each invocation's
sandbox process ends, and (b) register an `atexit` handler so anything still around gets swept if
klorb itself dies mid-command. Keep these placeholders in the same per-invocation tmpdir already
used for stdout/stderr capture where possible, so one cleanup pass covers all of it.

**Known critical risk, confirmed directly against this repo's own dev environment**: bwrap's
implicit user-namespace creation (needed for *any* invocation, not just ones that pass
`--unshare-user`) fails outright inside a nested container without the right permissions —
running `bwrap --ro-bind / / --proc /proc --dev /dev -- id` (no other unshares at all) here
returns `bwrap: No permissions to create new namespace, likely because the kernel does not allow
non-privileged user namespaces`, and confirmed at the `unshare --user --map-root-user` syscall
level too — this is not a defect in the plan's args, it's a property of the deployment
environment. Since this same repo also runs in a cloud/remote-agent mode (`CLAUDE_CODE_REMOTE`,
see `CLAUDE.md`) that's very plausibly containerized the same way, **this must be verified against
klorb's actual cloud/remote deployment target before shipping**, not just on a bare-metal Linux
dev machine — otherwise `BashTool` goes completely dead in exactly the workflow it's most likely
to run in, and the generic "install bubblewrap" fallback message would be actively misleading
(the binary can be correctly installed and still unable to function; see "process outcome" above
for the two distinct failure messages this needs).

We invoke this through the `subprocess` module in Python, using a `threading.Thread` (not
`multiprocessing`) to pump stdout/stderr and poll for completion/timeout/cancellation — mirroring
the existing pattern in `klorb/src/klorb/tui/shell.py`'s `UserShellCommand` (two daemon pump
threads reading stdout/stderr line-by-line, a main-thread poll loop via
`process.wait(timeout=...)`, and a `cancel_event: threading.Event` checked each wake-up, already
proven to coexist with the TUI's render loop). This is sufficient despite the GIL: the blocking
operations here (`Popen.wait()`, pipe reads, `fork`/`exec`) release the GIL in CPython, which is
exactly why the existing shell-command code already works this way today. `multiprocessing` would
add a second interpreter process, cross-process result marshaling, and more complex
signal/cancellation forwarding for no additional concurrency — the actual concurrency bottleneck
(bwrap/bash as an external OS process) is already isolated from the GIL regardless of which
Python primitive launched it.

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
  simple-command candidates (argv0 exact/glob match, optionally argument-prefix patterns — see
  "CommandRules parsing / matching with shfmt" below for the concrete token-pattern
  representation, and "Open questions" for the remaining syntax nuances).
  `SessionConfig` gains a `command_rules` field (on-disk key 'commandRules', following the existing
  dot-delineated lowerCamelCase convention from `docs/specs/process-and-session-config.md`),
  concatenated across config layers the same way `readDirs`/`writeDirs` already are (deny/ask/
  allow lists accumulate restrictions from every layer rather than replacing wholesale).
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

Planned invocation shape (Linux only — macOS support is Future Work, see below):

* See "bubblewrap args to use" above for the exact mount list (whole-tree `/usr` and `/etc`
  binds, conditional merged-`/usr` symlink handling, a read-write home-directory bind with
  denyholes over sensitive subdirectories, and PATH-derived top-up binds for anything left over).
* `--bind <workspace_root> <workspace_root>` read-write, sourced from the *same*
  `SessionConfig.workspace_root`/`writeDirs` the file tools already use — one source of truth for
  "what may this process touch," rather than a second filesystem policy defined independently for
  the sandbox. Any additional `writeDirs.allow` entries get their own `--bind`; everything else is
  simply absent from the mount table (not merely read-only). If the project is marked `trusted`
  (`session_config.workspace.trusted`), `workspace_root` is read-write by default, mirroring the
  same trusted/untrusted branch `resolve_and_evaluate_read()` already implements for reads — this
  is what keeps an everyday `make test > results.log`-style redirect from landing in `"ask"` by
  default for a trusted project, since `evaluate_write()` otherwise normalizes an unmentioned path
  to `"ask"` on both the read and write tables (see
  `docs/adrs/write-verdict-is-stricter-of-read-and-write-tables.md`).
* `--tmpfs /tmp`, `--proc /proc`, `--dev /dev` for a normal-looking but disposable scratch area.
* `--unshare-pid`, `--unshare-ipc`, `--unshare-uts`, `--unshare-net`, `--unshare-cgroup` by
  default; deliberately *not* `--unshare-user` (see "uid/gid" in the args section above — bwrap
  creates a user namespace implicitly as needed regardless, with an identity uid/gid mapping,
  which is what's wanted here). `--share-net`/omitting `--unshare-net` only when a command
  actually needs network access — tying network egress to a future permission resource kind
  (`TODO.md`'s "website access" bullet, `docs/specs/permissions.md`'s other forward reference),
  not granted unconditionally.
* `--die-with-parent` and `--new-session` (the latter needed to prevent `TIOCSTI`-based
  sandbox escapes when no seccomp filter is present).
* `--cap-drop ALL`, and optionally a `--seccomp <fd>` filter blocking syscalls no dev workflow
  legitimately needs (`ptrace`, `mount`, `reboot`, keyring manipulation) — a defense-in-depth
  layer on top of the namespace boundary, not required for a first version. (Note for whoever
  builds the future network-proxy support: Anthropic's `sandbox-runtime` deliberately avoids
  bwrap's native `--seccomp` for its proxied-network case, applying the filter via an external
  helper *after* the proxy sockets are set up, since the filter would otherwise block the
  syscalls needed to create those sockets in the first place — the same ordering problem will
  apply here once network proxying exists.)

### Fallback when `bwrap` can't actually sandbox anything

The original plan was to fail closed here: refuse to run `BashTool` at all if `bwrap` can't be
located or can't create a sandbox. Revised: **fall back to unsandboxed execution instead of
refusing**, because the case that actually breaks this (running inside a container — bwrap
"basically simply does not run in a docker container") is common enough, including inside
klorb's own likely cloud/remote-agent deployment, that a hard refusal would make `BashTool`
unusable there entirely rather than degraded.

**Detection**: don't special-case "am I in Docker" as the gating check — the real question is
"can bwrap actually create a sandbox right now," and Docker is only one of several causes
(others: `kernel.unprivileged_userns_clone=0` on a bare-metal host, other container runtimes,
Ubuntu's AppArmor-based unprivileged-userns restriction, a missing `bwrap` binary). A
file-existence heuristic like `/.dockerenv` would miss all of those and could also false-negative
(present but sandboxing works fine anyway, e.g. a privileged container). So: run a cheap,
self-contained bwrap smoke test once — e.g. `bwrap --ro-bind / / --proc /proc --dev /dev -- true`
— at session start (or lazily on the first `BashTool` call), cache the boolean result for the
life of the process, and gate on *that*, not on any environment fingerprinting. A fast
`/.dockerenv`/`/proc/1/cgroup` check (as well as a check whether bubblewrap is installed)
can still be used purely to make the resulting warning
message more specific ("...this commonly happens inside Docker containers...") — never to make
the go/no-go decision itself.

**When the smoke test fails**, `BashTool` runs the same command *without* `bwrap`:

* Same `bash --rcfile ${HOME}/.bashrc -i -c "<command>"` invocation shape.
* Same environment construction — with one implementation nuance: there's no `--clearenv` to lean
  on anymore, and `subprocess.Popen` inherits the *entire* parent environment by default if not
  given an explicit `env=` argument. So building the equivalent of `--clearenv` + `--setenv`
  requires explicitly constructing the full `env` dict passed to `Popen(..., env=...)` rather than
  omitting `env=` and relying on inheritance — otherwise the least-privilege intent silently
  evaporates in exactly the path where there's no kernel boundary left to fall back on.
* Same timeout/cancellation handling (`threading.Thread` + `process.wait(timeout=...)` polling +
  `cancel_event`) — this was never bwrap-specific.
* Same stdout/stderr tmpfile capture and spill-to-file behavior.
* Signal-death decoding for the target process is actually *simpler* here — one fewer layer
  (`bash -> git`, not `bash -> bwrap -> git`), but the same `128+signum`-via-parent-shell
  convention still applies and still needs decoding the same way.
* `CommandPermissionsTable`/`shfmt`-based argv classification and the `evaluate_write()` check on
  redirection targets are both pure userspace/Python logic with no bwrap dependency — both stay
  fully enforced regardless of sandbox availability.

**What's genuinely lost, and must be stated plainly rather than glossed over**: without the mount
namespace, there is no kernel-level backstop on filesystem access — only on what the shell syntax
itself makes visible. `evaluate_write()` still gates an explicit `> file`/`>> file`/`2> file`/
`tee file` redirect the model wrote, and `CommandRules` still gates argv0/args, but neither can
see what an *approved* command does with its own `open()`/`write()` calls once running — a
`python -c "..."` one-liner, a compiled binary, or any interpreter shfmt can't see inside of can
read or write anything the sandboxed-mode boundary would otherwise have prevented, and there's no
way around that without an OS-level enforcement layer. This is a real, material reduction in the
security guarantee for the duration of the fallback, not a cosmetic one.

**One-time warning**: the first time a `BashTool` command actually runs in fallback mode for a
given session, print a persistent (not transient/toast) entry to the conversation history stating
that sandboxing is unavailable, why (smoke-test failure, tailored by the Docker/cgroup heuristic
above if applicable), and that command/redirect permission checks are still enforced but there is
no OS-level isolation this session. Don't repeat it on every subsequent call. A
`tools.bash.requireSandbox` config knob restoring the original hard-refusal behavior for
security-conscious users who'd rather `BashTool` not run at all than run unsandboxed is a
reasonable future addition, not required for a first version.

**klorb's own test suite**: any of klorb's automated tests that assume real bwrap semantics (mount
isolation, network isolation, etc.) should skip (not xfail/error) when the same smoke-test
function reports "unavailable" — share one `bwrap_available()`-style check between the runtime
fallback logic and test collection (e.g. a `pytest.mark.skipif`/autouse fixture built on it),
rather than hand-rolling a second, potentially-divergent Docker-detection heuristic just for
tests.


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
  * Concrete pattern representation: a rule is a `list[str]` of tokens, matched positionally
    against the parsed argv. Each token is either a literal (matched exactly) or one of two
    wildcards: `*` matches exactly one arbitrary token at that position, and a pattern's *last*
    token being `*` additionally means "and any further tokens, including zero" (covering the
    `foo *`/`git status *`/`git * status *`/`foo bar --baz anyfile.c`-style examples above
    without needing a third wildcard form). A rule with no `*` at all matches only that exact
    argv length (the "forcibly no args" `foo` case). Finer points (case sensitivity, glob-style
    `*` *within* a single token rather than as a whole-token wildcard) are left to implementation,
    since they don't change the deny/ask/allow evaluation model.
  * These can exist in deny/ask/allow lists, applied in that order.
    * Any denylist entry that can match the current command kills it.
      * so a denylist entry for 'git' will kill 'git foo', 'git bar', even if there are
        other allowlist entries for it.
    * Any asklist entry that can match the current command shifts to ask mode
      * ... so asklist entries always preempt allowlist entries

* `shfmt` parse failure should be surfaced to the model as a normal tool error, so it
  can retry with simpler syntax

* Heredoc/pipe-into-interpreter rule: content flowing into a command via a heredoc or a pipe is
  only as safe as what that command *does* with it. A short, explicit allowlist of commands that
  only ever consume stdin as inert data — `cat`, `less`, `git` (e.g. `git commit -F -`,
  interactive rebase todo lists) — may receive heredoc/piped content under normal `CommandRules`
  evaluation. Any other target (`sh`, `bash`, `python`, `perl`, `ruby`, `node`, and anything else
  that treats stdin as code to execute) escalates to `ask` (or `deny`, per configured rules)
  regardless of what an allow-rule for that command's argv0 alone would otherwise say — this is a
  structural override on top of the walker's normal classification, not something a plain
  allowlist entry for e.g. `python` can downgrade. This is the same shape of risk as the
  `curl | sh` worked example below, just arriving via heredoc instead of a pipe.

* Explicit backgrounding (a top-level `&`, distinct from `&&`) is rejected by the walker at parse
  time (escalated to `ask`, configurable to `deny`) rather than reasoned about at runtime — this
  is simpler and matches the "fail closed on anything not confidently classified" rule already
  established. This is defense-in-depth, not the only backstop: even if a sandboxed command
  manages to background/detach something without an explicit top-level `&` (e.g. via its own
  internal `nohup`/`setsid` call), `--unshare-pid` means it's still inside the pid namespace bwrap
  created for this invocation, still parented (however indirectly) to that namespace's default
  reaper, and still torn down when the invocation's `bwrap` process tree ends — there's no
  process-group trick that lets it escape the namespace the way disowning a background job can
  escape a plain, unsandboxed shell. Add a test case that runs something like
  `sleep 30 & disown` inside the sandbox and confirms the process is gone shortly after the tool
  call returns, to validate this end-to-end rather than trusting the namespace-teardown reasoning
  alone.

Five separate decisions made during this plan's research are ADR-worthy and should be written up
as their own ADRs in `docs/adrs/` once implementation actually begins (one file each, following
this repo's answer-bearing-filename convention, not one combined ADR):

1. Shelling out to `shfmt --to-json` over Go bindings or a pure-Python bash parser.
2. Rejecting `trap DEBUG`/`extdebug` as a security boundary (it's cooperative, not adversarial).
3. Bubblewrap sandboxing as mandatory defense-in-depth, not an alternative to classification.
4. `CommandRules`' deny/ask/allow evaluation order mirroring `DirRules`'s (strictest always wins,
   regardless of rule specificity).
5. The env-var strategy in the "env vars" section above — `--clearenv` plus explicit
   `shareEnv`/`setEnv` plus forcing `~/.bashrc` sourcing via `-i --rcfile` despite the resulting
   stderr-stripping requirement — since it's a real trade-off (verified empirically, not
   assumed) between "cumbersome to configure" and "silent no-op for most real `.bashrc` files."

`shfmt-py`'s own package version doesn't map 1:1 to the `shfmt` version it bundles, so
`pyproject.toml` should pin an exact, tested `shfmt-py` version rather than an open-ended
constraint — otherwise a routine `pip install --upgrade` could silently change the bundled
`shfmt` version and trip the AST-shape fixture self-check with no corresponding code change to
blame.

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
* `bwrap` is a linux executable; macOS support means adapting this to Apple's Seatbelt
  (`sandbox-exec`) sandboxing mechanism instead, following the general approach the
  `anthropic-experimental/sandbox-runtime` toolkit takes for its own macOS support. Linux is the
  only supported platform for this plan; macOS is deferred entirely, not an open design question.
* Audit logging (goal #7: command requests, permission decisions and their reasoning, execution
  outcomes) is a real goal but not required for a first version — the deny/ask/allow evaluation
  and the sandboxed execution both need to exist and work correctly first. Structured logging of
  each stage can be added once the rest of this plan is implemented and stable.

## Open questions

* Exact default denylist of homedir subdirectories to mask (`~/.ssh`, `~/.aws`, `~/.gnupg`,
  `~/.config/gcloud`, `~/.netrc`, `~/.docker` are the obvious ones; is there a more complete list
  worth starting from, e.g. browser profile/cookie directories on dev workstations?).
  * ANSWER: The complete denylist to use is already baked into the klorb-defaults.json dir denylist.
    This was already built for the existing filesystem permissions scheme and does not need to be
    revisited.
* Exact schema of bwrap's `--json-status-fd` output, and whether it reports the sandboxed
  command's own exit/signal status directly (which would be more robust than inferring from the
  aggregate `128+signum` bwrap exit code — see "process outcome" above). -- In the future,
  Claude needs to run
  on a WSL2 instance when it can use bubblewrap and work out what this schema is.
* Argv-pattern token syntax details beyond whole-token wildcards (case sensitivity, glob-style
  matching within a single token) — sketched in "CommandRules parsing / matching with shfmt"
  above, not fully specified. Claude needs to make some decisions.

