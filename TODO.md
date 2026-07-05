

# Bugs:

* KLORB_CONFIG_DIR/KLORB_STATE_DIR/KLORB_DATA_DIR are eager-computed from the environment
  on module load, before load_dotenv() runs, so they cannot be shadowed in a `.env` file.

* In tool limit increase modal there are yes and no buttons. You can [tab] between them
  but you should also be able to use L/R arrow keys to get there.
  * (This should be the case for all the yes/no selection modals we have.)

* Some of the config for this repo must actually be settings in my User-specific vscode config
  on my home PC. Do a sweep thru the settings that should be pulled into the repo, as well
  as extensions that should be listed as Workspace Recommendations.

# Feature backlog

* If you change the visual theme, the theme color should get baked into the ProcessConfig /
  homedir-level settings file.

* When you select a model, it should update the workspace's `sessionDefaults > model` config file 
  setting so it remembers next time.

* When you select a theme, the theme selection modal should put a `(*)` as a suffix next to
  the currently-selected theme name.
  * When you change to a new theme, a confirmation msg should show up in the main scroll "Changed
    current theme to `foo bar theme name`."

* If it's the agent's turn the "send a message" textbox prompt should be "queue a message..." 
  and you should be allowed to type before it's actually your turn to send.
  * The next logical thing to do is to implement "interrupting" in the conversation so you
    can interject midway thru what it's saying. 

* PLAN-003 (READY): project bootstrapping and trust
  * when you start klorb it attempts to identify the workspace root.
  * if it can't find one, then the cwd is the workspace.
  * ask the user if they want to open the cwd as a project.
    * if no, do nothing. we only get default permissions of stuff.
    * if yes, create cwd/.klorb/ and write a minimal config json in there
      * by default allow file read access to the workspace root.
      * Also burn in the currently-active model name to the default session config in the file.
  * ask the user if they trust the dir and want to allow writes
    * if yes then allow file write/create access to the workspace root.
    * if no then put the workspace root down as 'ask' for writes.
    * If no then clamp down reads to inside workspace root too, not just trusting its config file.
  * ... this trust question actually needs to happen the first time a given
    workspace is opened even if it already contained a config file (e.g., you download it from
    the internet and unzip a tarball with a /.klorb/ in it). so trust needs to be tracked in
    ~/.share/klorb/ or ~/.config/klorb/ or something external to the workspace itself.
    ("Trust" is its own whole thing that will need a dedicated plan, basically.)

* Add a command (CLI and/or command palette) that dumps the *resolved* system prompt for the
  current role + model into the user's editable tree
  (`$KLORB_CONFIG_DIR/system_prompts.d/...`, at the same relative path the resolver would
  read it back from), so the user has a real `.md` file to start editing from instead of
  hunting down the packaged copy inside site-packages. Should refuse to clobber an existing
  file without `--force`, like `klorb init` (see docs/specs/klorb-init.md). See
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

* send_one_shot should actually run a whole session (non-interactively) until it gets a 'finished'
  state rather than just sending a single msg turn for a single response.
* Need a ProviderFactory
    * Produces ApiProviders from a string
    * Currently only openrouter api provider is supported from "openrouter" string.
    * model names now can be fully-qualified model name (fqmn): e.g.: "openrouter:gpt-4o-mini"
    * Session can get the current Provider from the ProviderFactory.
    * /clear to create a new session keeps the same model name (and thus model, provider) as last session.
* More tools:
    * AskUserQuestionsTool
    * Add Evals for GrepTool and FindFileTool.

    * WebSearchTool -- use Brave Search: https://api-dashboard.search.brave.com/app/plans
    * WebFetchTool

* Skills in <built-in-skills-dir>, ~/.klorb/skills, projRoot/.klorb/skills/
    * Add general skills/know-how for writing docs/specs and docs/adrs/ files.
    * If `compatibility.claudeSkills` is true, include projRoot/.claude/skills/
* Integrate with chainlink for todo tracking
    * TodoList tool
    * TodoWrite tool
* Memories in ~/.klorb/memory, projRoot/.klorb/memory/
  * UpdateMemory tool
  * Remember tool
* "Team scratchpad" is a file where all agents on the team can read and write
  * ephemeral; survives for the length of a session.
  * ReadScratchpad tool -- reads range from the file.
  * EditScratchpad tool -- updates file
  * SearchScratchpad tool -- grep it.


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
  * what bash commands can it run (or not)
  * what web sites can it access? (... what kind of prompt injection could happen here?)
  * Use bubblewrap via https://github.com/anthropic-experimental/sandbox-runtime ?
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

* BashTool
* Metacognition tools -- read config; update (in-memory) config; update config file(s)

* Context auto-compaction
