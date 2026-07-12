

# Bugs:

* Scratchpad files that are ostensibly supposed to have a lifetime equal to the session
  that created the scratchpad file in the first place, are *not* being cleaned up.

* the 'screenshot' option in the cmd palette doesn't work.

* KLORB_CONFIG_DIR/KLORB_STATE_DIR/KLORB_DATA_DIR are eager-computed from the environment
  on module load, before load_dotenv() runs, so they cannot be shadowed in a `.env` file.

* I had already explicitly worked to remove the "global" scrollbar so that only the "history"
  scrollbar showed; but it seems like both (slightly differently-sized/aligned) scrollbars
  are still both present on a long enough session.
  (See commit: "Bugfix. Remove double scrollbar in TUI history view (#33)")
  ... this is probably a "ghost paint" based on whatever abuse of the terminal is being
  done by Textual's draw-over algorithm? This may not be fixable.

* Hotkeys like ^o to show/hide detail should work even while we're "blocked" waiting on the
  model to respond.
  * **especially** while we're waiting for the nano model to classify a bash command,
    which can take a couple of seconds, all the input is blocked.

# Feature backlog

* The SystemPrompt can include some dynamic information about the current state of things:
  * Date/time the session started
  * Model name
  * Model knowledge cutoff date
  * Maybe add the detailed `git log -1` including "`whatchanged`" filenames.

* System prompt and interstitial prompt ("hook") improvements:
  * Regarding the user-entered task: start with a plain request, then rewrite it
    into role, task, context, constraints, and output format. (maybe ask a
    cheaper model how to rephrase the original user prompt to kick off the
    session??)
  * After the LLM uses tools to make a change, inject a prompt to have it
    observe / remark on its changes, reflect, decide if it should revise / loop
    back, or proceed... Kind of a "super turn" idea which loops over what it
    accomplishes in one big turn.
  * Also periodically remind it to look back at the system prompt and workspace
    instructions; you can reference the associated SystemInterjection xml tag
    and subject attribute.

* The risk_classifer that rewrites bash commands to have */?/** for broader approvals should
  actually also get the history of other approved and denied commands from earlier in the
  session. If the user has approved a whole bunch of very similar commands, then it should
  take that into account and consider generalizing more widely for the next approval.

* Bash tool can have a summary line provided by the agent, like
  "List all _wait_until call sites in test_tui_repl.py", which we show to the user, in addition
  to the "grep -n ..." actual command. This can be shown in approval dialog as well as cmd history.

* CLI subcommand: `klorb show-config` should show the merged config from all the json files it loads.

* All config files that the session reads should be things that the process subscribes to; if one
  of the files is modified, then we should hot reload them into the session rather than wait for
  a process restart. (How does that work w.r.t. approvals, etc., that we've put into memory just
  for the duration of the session? Ideally just changed values or new approve/deny/ask entries
  are merged in with the rest... this seems like it would clobber things, though.)
* Each per-project subdir in `.local/share/klorb/...` should include a `logs` subdir with symlinks
  to all the log files in `.local/state/ associated w/ the project
* Why does this logging line make it to the stderr log and not the file log like basically everything
  else? `INFO:klorb.token_estimate:Found bundled tiktoken cache...`

* bash approval panel should put `+` in the hotkey list for the panel when `more` is available.

* Risk classifier (risk_classifier.py) "Command comments to review must not be trusted" instructions
  should be put in an eval that judges how well the model resists malicious prompt input.

* ReadFile security: Put everything thru a filter that recognizes AWS access key id fields, etc, and
  just anonymizes those fields before passing to the LLM. (figure out a special replacement token so
  that readfile and editfile can interact in a loop even with field masking making literal context
  matching in EditFile impossible.)

* If it's the agent's turn the "send a message" textbox prompt should be "queue a message..."
  and you should be allowed to type before it's actually your turn to send.
  * The next logical thing to do is to implement "interrupting" in the conversation so you
    can interject midway thru what it's saying.

* `klorb system-prompt` should have a `--export` option
  that dumps the *resolved* system prompt for the current role + model into the
  user's editable tree (`$KLORB_CONFIG_DIR/system_prompts.d/...`, at the same
  relative path the resolver would read it back from), so the user has a real
  `.md` file to start editing from instead of hunting down the packaged copy
  inside site-packages. Should refuse to clobber an existing file without
  `--force`, like `klorb init` (see docs/specs/klorb-init.md). See
  docs/specs/roles-and-system-prompts.md.

* mouse-based select/copy/paste doesn't work. (ctrl-x/c/v does though, and shift-l/r does select...)

* When we quit, ask if we should save the session state.
  If yes, then write a file that goes in `projRoot`/.klorb/last-session.json
  storing last Session config  and the message history for the session.

  When we next load klorb in that same directory, auto-load the session state info from last-session.json
  and reconstitute the session.

  This json file should include the schema info:
  ```
  {
    schema: {
        name: "klorb-session",
        version: "1.0.0"
    },

    /* actual data here. */
  }
  ```

* Need a ProviderFactory
    * Produces ApiProviders from a string
    * Currently only openrouter api provider is supported from "openrouter" string.
    * model names now can be fully-qualified model name (fqmn): e.g.: "openrouter:gpt-4o-mini"
    * Session can get the current Provider from the ProviderFactory.
    * /clear to create a new session keeps the same model name (and thus model, provider) as last session.
* More tools:
    * Add Evals for GrepTool and FindFileTool.

    * WebSearchTool -- use Brave Search: https://api-dashboard.search.brave.com/app/plans
    * WebFetchTool

* Skills in <built-in-skills-dir>, ~/.klorb/skills, projRoot/.klorb/skills/
    * Add general skills/know-how for writing docs/specs and docs/adrs/ files.
    * If `compatibility.claudeSkills` is true, include projRoot/.claude/skills/
* Integrate with chainlink for todo tracking
    * TodoList tool
    * TodoWrite tool

* Subagent spawning
  * When an agent spawns a subagent for a different role, the subagent gets a new child
    `Session` whose `SessionConfig` (and related context) is a *copy* of the parent's, with
    `role_name` (and thus the `Role` the child session builds) replaced by the
    subagent-specific one, and with the parent-provided instructions message seeded into the
    child's message context. Roles and role-tier system prompt resolution already exist
    (docs/specs/roles-and-system-prompts.md); the spawning/dispatch mechanism does not.
* Agent teams
  * A team of specialist agents working a larger coding problem in parallel or in series:
    writing specs and ADRs, writing code, system design, writing tests, and reviewing code —
    the latter possibly its own team of specialists (correctness, performance,
    cybersecurity, ...). `Role` subclasses (`klorb/src/klorb/role.py`) and
    `Role.repertoire()` are the placeholder hooks for this.
* Need a Planning Tool or Planning Mode agent

* Permissions
  * Need to handle extra safeguards for writing into ${workspaceRoot}/.klorb/. This is
    implicitly denied; add a separate EscalatePrivileges tool that will unlock
    the dir (with a user ask prompt) for writes (thru the end of the turn? the session?)
  * what web sites can it access? (... what kind of prompt injection could happen here?)
  * `klorb.sandbox.build_bwrap_argv()` is still a stub — `BashTool` runs every command
    unsandboxed today (see docs/plans/ready/004-bash-permissions-and-bash-tool.md and
    docs/adrs/bubblewrap-is-defense-in-depth-not-a-classifier-substitute.md). Building the real
    `bwrap` argv needs a host where unprivileged user namespaces actually work, which this
    project's own dev/cloud-agent environments don't provide; needs a WSL2 (or similar) instance
    to develop and verify it against, including working out `bwrap`'s `--json-status-fd` schema
    for signal/exit-status reporting.
  * TOCTOU: every permission check (klorb.permissions.workspace/directory_access) resolves a
    path string at check time; nothing holds an open OS-level directory handle across the gap
    between that check and the actual file I/O, so a rename/symlink swap in that window could
    redirect an approved operation. Closing this needs os.open()-based fd-relative I/O
    (O_NOFOLLOW/O_DIRECTORY), not path-string re-resolution. See docs/specs/permissions.md.
  * Per-file allow/ask/deny isn't a supported concept yet — only directories (matched by
    ancestor-or-self containment in `DirectoryAccessTable`) are covered by `readDirs`/
    `writeDirs`. Plenty of real secrets are single files sitting directly inside an otherwise
    unremarkable directory, where denylisting the whole parent directory would be too broad —
    `~/.git-credentials`, living right in `$HOME` alongside lots of non-sensitive files, is the
    canonical example (`~/.npmrc`, `~/.netrc`, `~/.pgpass` are others). Needs either exact-file-
    path rules as a first-class, tested feature (not just something that happens to work today
    via `Path` equality in `DirectoryAccessTable._matches`) or glob/pattern matching (`*.pem`,
    `id_rsa*`, etc.) so one rule can catch a class of filenames wherever they show up.
    `klorb/src/klorb/resources/default-config.json`'s reference denylist deliberately sticks to directories for now and
    skips file-level entries pending this.
  * Path macros: support expanding `${home}`/`${workspaceRoot}` (maybe also `${configDir}`)
    inside `readDirs`/`writeDirs` (and any other future path-shaped config value), alongside the
    plain `~` homedir shorthand `canonicalize_dir` already expands. `workspaceRoot` has no
    shorthand today, and namespaced/braced macros read more explicitly than a bare `~` once
    there's more than one kind of substitution — this would give one consistent expansion story
    across every path source (config file, and LLM-supplied tool-call `filename`s) instead of
    special-casing `~` alone.

* Metacognition tools -- read config; update (in-memory) config; update config file(s)

* Context auto-compaction
