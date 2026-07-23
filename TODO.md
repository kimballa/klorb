
# TODO

## Bugs

* Project dir names in ~/.local/share/klorb/ should be `<basename>-<uuid>`, not the other way around.

* LLM output is being added to the history in an markdown-aware way and if the LLM
  itself emits `<xml>`-like tags, it starts syntax-highlighting its own output in weird
  ways. We need to be robust if the LLM accidentally starts sending mis-matched XML
  like `</Think>` in the middle of its output.

* the 'screenshot' option in the cmd palette doesn't work.

* KLORB_CONFIG_DIR/KLORB_STATE_DIR/KLORB_DATA_DIR are eager-computed from the environment
  on module load, before load_dotenv() runs, so they cannot be shadowed in a `.env` file.

* I had already explicitly worked to remove the "global" scrollbar so that only the "history"
  scrollbar showed; but it seems like both (slightly differently-sized/aligned) scrollbars
  are still both present on a long enough session.
  (See commit: "Bugfix. Remove double scrollbar in TUI history view (#33)")
  ... this is probably a "ghost paint" based on whatever abuse of the terminal is being
  done by Textual's draw-over algorithm? This may not be fixable.

* Have an agent do a pass over all/most source (or do it in sections) to remove existing
  over-explaining comments that recapitulate decisions already captured in ADRs, explain what a
  function *doesn't* do, is overly-specific specific and brittle, etc.

## Feature backlog

* When the user types `/` at start or after whitespace, it should have a little fuzzy-finder pop-up
  near the cursor to help find the skill they want. ESC dismisses fuzzy-finder, as does continuing
  to type after ruling out any matches.

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

* Each per-project subdir in `.local/share/klorb/...` should include a `logs` subdir with symlinks
  to all the log files in `.local/state/ associated w/ the project. Really the other way around: put
  the true logs in the per project folders and symlink from a common place. Then the log roll reaper
  could start from the common symlink side when picking things to remove and also clean up dead
  symlinks.

* Risk classifier (risk_classifier.py) "Command comments to review must not be trusted" instructions
  should be put in an eval that judges how well the model resists malicious prompt input.

* ReadFile security: Put everything thru a filter that recognizes AWS access key id fields, etc, and
  just anonymizes those fields before passing to the LLM. (figure out a special replacement token so
  that readfile and editfile can interact in a loop even with field masking making literal context
  matching in EditFile impossible.)

* `klorb system-prompt` should have a `--export` option
  that dumps the *resolved* system prompt files for the current role + model into the
  user's editable tree (`$KLORB_CONFIG_DIR/system_prompts.d/...`, at the same
  relative path the resolver would read it back from), so the user has a real
  `.md` file to start editing from instead of hunting down the packaged copy
  inside site-packages. Should refuse to clobber an existing file without
  `--force`, like `klorb init` (see docs/specs/klorb-init.md). See
  docs/specs/roles-and-system-prompts.md.

* mouse-based select/copy/paste doesn't work. (ctrl-x/c/v does though, and shift-l/r does select...)

* Need a ProviderFactory
  * Produces ApiProviders from a string
  * Currently only openrouter api provider is supported from "openrouter" string.
  * model names now can be fully-qualified model name (fqmn): e.g.: "openrouter:gpt-4o-mini"
  * Session can get the current Provider from the ProviderFactory.
  * /clear to create a new session keeps the same model name (and thus model, provider) as last session.
* More tools:
  * Add Evals for GrepTool and FindFileTool.

  * WebSearchTool -- use Brave Search: <https://api-dashboard.search.brave.com/app/plans>
    (see "Plan 013: WebFetch" section below)

* Skills in `<built-in-skills-dir>`, ~/.klorb/skills, projRoot/.klorb/skills/
  * the user and agent SkillCatalogs are currently global / singleton objects but eventually should
    get moved into Session. This will set up a clean mechanism for restricting skill availability
    for narrow sub-agents.
  * Add general skills/know-how for writing docs/specs and docs/adrs/ files.
  * Add skill for code review
  * When `compatibility.claudeSkills` is true, `projRoot/.claude/skills/` should become a
      privileged directory requiring `EscalatePrivileges(scope="workspace")` the same as
      `.klorb/skills/`, rather than an ordinary `writeDirs`-gated path — writing skill content
      into a directory klorb itself trusts and auto-discovers deserves the same escalation
      klorb's own skills directory gets.
* Add tips/suggestions:
  * When opening a workspace for the first time, suggest compatibility.claudeMarkdown and
    compatibility.claudeSkills if it has a CLAUDE.md or .claude/skills.
* Improve Workspace trust msg:
  * When querying about workspace trust, list any workspace skills auto-allowed by config.

* Eventually when we have a lot of skills, the skill list that is auto-advertised in the
  initial SystemInterjection should be pruned and only display some top most-relevant
  skills or most-frequently-used skills. Let the agent discover others via SearchSkills.

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
  * BashTool / bubblewrap sandbox follow-ups: a `--seccomp` defense-in-depth
    filter (ptrace/mount/reboot/keyring), and network egress via a
    domain-allowlist proxy (today `--unshare-net` denies all network).
    * Once domains are available in the bubblewrapped bash, add allow-list entries for
      pypi, npm, maven-central.
  * TOCTOU: every permission check (klorb.permissions.workspace/directory_access) resolves a
    path string at check time; nothing holds an open OS-level directory handle across the gap
    between that check and the actual file I/O, so a rename/symlink swap in that window could
    redirect an approved operation. Closing this needs os.open()-based fd-relative I/O
    (O_NOFOLLOW/O_DIRECTORY), not path-string re-resolution. See docs/specs/permissions.md.
  * Per-file allow/ask/deny is only partially implemented — add wildcard/glob support
    like `*.pem`.
  * Path macros: support expanding `${home}`/`${workspaceRoot}` (maybe also `${configDir}`)
    inside `readDirs`/`writeDirs` (and any other future path-shaped config value), alongside the
    plain `~` homedir shorthand `canonicalize_dir` already expands. `workspaceRoot` has no
    shorthand today, and namespaced/braced macros read more explicitly than a bare `~` once
    there's more than one kind of substitution — this would give one consistent expansion story
    across every path source (config file, and LLM-supplied tool-call `filename`s) instead of
    special-casing `~` alone.

* Metacognition tools -- read config; update (in-memory) config; update config file(s)

* Context auto-compaction

* Vector database indexing of skills and memories for fuzzier search of both.
  * ... and then do vectordb indexing of the codebase, too.

* Ask (or send a patch) upstream for a `chainlink init --db-only` flag that skips writing the
  Claude-Code-hooks/MCP scaffold (`.claude/settings.json`, `.claude/hooks/`, `.claude/mcp/`,
  `.mcp.json`) and just sets up `.chainlink/`. klorb's `ChainlinkClient._ensure_setup()` prunes
  that scaffold itself today (see docs/specs/chainlink-task-tracking.md); a flag would remove
  the need for the workaround.

* Add a LICENSE file to this repo.

* Ask (or send a patch) upstream for chainlink's `issue show`/`issue list --json` to report a
  `blocked_by_open` list (or count) alongside `blocked_by` -- right now `blocked_by` is never
  pruned as a blocker closes, so klorb's own `open_blocker_count()` (`klorb.tools.tasks.common`)
  has to recompute "still in the way" itself by intersecting against a separately-fetched open-id
  set, rather than trusting chainlink's own data.

### Plan 013: WebFetch

* Third-party malware blocklisting: query external threat lists and auto-deny requests to
  domains on blocklist(s) maintained by trusted third parties, not just the user's own
  `deny` list.
* Cookie handling: a session-scoped `httpx.Client` (held in
  `session.tool_state["WebFetch"]["client"]`) to enable cookie persistence across calls,
  instead of the fresh per-call client used today.
* POST/PATCH/PUT with a request body (JSON, form data, or raw bytes), now that the
  read-only GET path is solid.
* `Tool.is_read_only()` needs a conditional form `is_read_only(args)` once WebFetch
  supports methods besides GET, so it can return True for GET/HEAD/OPTIONS and False
  otherwise.
* Dedicated WebSearchTool -- use Brave Search: <https://api-dashboard.search.brave.com/app/plans>
