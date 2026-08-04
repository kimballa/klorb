
# TODO

## Agent / Harness

### Bugs

* a recent system plan with a phase for vscode plugin UI dev painted it as a straightforward translation of TUI integration. But it completely overlooked the need to build ACP server / protocol integration for the feature. The TUI bypasses this comms requirement. Add a vscode-tech-planning skill to ensure we don't miss that step in the future.

* the 'screenshot' option in the cmd palette doesn't work.

* KLORB_CONFIG_DIR/KLORB_STATE_DIR/KLORB_DATA_DIR are eager-computed from the environment
  on module load, before load_dotenv() runs, so they cannot be shadowed in a `.env` file.

* (All python) Have an agent do a pass over all/most source (or do it in sections) to remove existing
  over-explaining comments that recapitulate decisions already captured in ADRs, explain what a
  function *doesn't* do, is overly-specific specific and brittle, etc.

### Feature backlog

* The user should be allowed to send messages to subagents in the usual direct-send and enqueued ways. The subagent does not differentiate between input from its parent agent vs the actual user. When it creates a subagent or uses MessageSubagent to resume a conversation, the parent agent sets a parent_interested flag when it is expecting a response from the subagent. When the subagent ends its turn, it is conveyed to the parent only if the parent was expecting a msg (at which point the flag is cleared). If the user just separately msgs a stalled subagent and it replies, that doesn't roll up to the parent agent. WaitForSubagents does not watch any subagents whose "parent_interested" flag is False. With this flag implemented, the prompt input can be enabled in the TUI and vscode plugin; but it should send a msg to the active (sun)agent session, not just the root session. And the draft msg is part of the Session-specific UI state. Choose a different subagent from the list and it should reset to empty (or the last draft started there).

* Raise default tool call limit from 50 to 100.

* Subagent roles:
  * Planner -- develop planning documentation for how to implement a new feature.
  * Reviewer -- perform adversarial code review on a completed feature.
  * TaskMaster / ProjectManager -- keep track of fine-grained tasks and ensure that they are all
    completed by other agents (or keep the Operator parent agent honest about progress). When given
    a medium-grain task, break it down into additional fine-grained tasks and ensure they're
    registered with chainlink.
  * ... The goal of this is to eventually support a "software factory" model where the system is
    autonomously continuously pulling new tasks out from a queue (e.g. a directory filled with new
    feature spec documents or bug report documents) and executing them one after the next.

* WaitForSubagent blocks indefinitely. If the user adds a new msg in the meantime, it just gets queued
  rather than interrupting the situation. There's probably a new kind of "user-interruptible" nature
  to this tool to let it get an insta-response from the tool_response ("no agent feedback yet, but
  here's something new from the user: ...")

* We currently disable the user chat to subagents. Should that be the case? Or should we let the user
  interject? (What's the harm?)

* now that we have the python tui fzf for files, we have a python mechanism for maintaining a list of
  all files in the repo. The FindFiles tool should take advantage of that for much faster
  performance than actually hitting the filesystem directly. (The "server side" tool should maintain
  its own list, not share with the tui. It should also include gitignored files... as well as anything
  that readDirs and readFiles deny access to. It should build this list on startup then subscribe to
  watchdog events, like the other fzf file index. It should also rebuild this file any time readDirs
  or readFiles permissions are updated. It should definitely do this work on a bg thread.)

* Sometimes the agent just thinks and thinks and keeps reading things and doesn't really make decisions
  or start testing anything. Maybe we should notice this condition (only using read-only tools for N
  tool calls in a row) and force it to deliver a report to the user that enumerates some concrete
  next steps, or something like that.

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

* Improvements to Skills:
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
  * Eventually when we have a lot of skills, the skill list that is auto-advertised in the
    initial SystemInterjection should be pruned and only display some top most-relevant
    skills or most-frequently-used skills. Let the agent discover others via SearchSkills.

* Agent teams
  * A team of specialist agents working a larger coding problem in parallel or in series:
    writing specs and ADRs, writing code, system design, writing tests, and reviewing code —
    the latter possibly its own team of specialists (correctness, performance,
    cybersecurity, ...). `Role` subclasses (`klorb/src/klorb/role.py`) and
    `Role.repertoire()` are the placeholder hooks for this.
* Need a Planning Tool or Planning Mode agent

* Permissions
  * BashTool / bubblewrap sandbox follow-ups: a `--seccomp` defense-in-depth
    filter (ptrace/mount/reboot/keyring).
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

* Integrate with `chainlink init --db-only`, once merged. Then we don't need to include the code
  to remove all the extraneous stuff it adds.
  (see docs/specs/chainlink-task-tracking.md)
* Integrate with `chainlink`'s `blocked_by_open` field, once merged. Then we don't have to look
  up every task in the `blocked_by` list to calculate a true blocker list / `open_blocker_count()`.

* Add more system interjections:
  * If the agent does *not* have a plan, after a while, start harrassing it to write down some
    objectives for itself via TodoWrite and use TodoNext to start focusing on task-oriented work.
  * Start adding system interjections mentioning how many turns the agent has taken, or how
    many tool calls (vs total tool call budget / limit) it has performed.

## TUI

### Bugs

* LLM output is being added to the history in an markdown-aware way and if the LLM
  itself emits `<xml>`-like tags, it starts syntax-highlighting its own output in weird
  ways. We need to be robust if the LLM accidentally starts sending mis-matched XML
  like `</Think>` in the middle of its output.

* I had already explicitly worked to remove the "global" scrollbar so that only the "history"
  scrollbar showed; but it seems like both (slightly differently-sized/aligned) scrollbars
  are still both present on a long enough session.
  (See commit: "Bugfix. Remove double scrollbar in TUI history view (#33)")
  ... this is probably a "ghost paint" based on whatever abuse of the terminal is being
  done by Textual's draw-over algorithm? This may not be fixable.

* mouse-based select/copy/paste doesn't work. (ctrl-x/c/v does though, and shift-l/r does select...)

### Feature backlog

* The homedir settings track whether the task panel is open or closed (`ui.taskSidebar.visible`)
  but now it can be replaced by the agents panel. We should track which of these is actually
  open/visible. Let's remove that particular field and have `ui.sidebar` be `"tasks"`, `"agents"`,
  or `null`.

* When the user types `/` at start or after whitespace, it should have a little fuzzy-finder pop-up
  near the cursor to help find the skill they want. ESC dismisses fuzzy-finder, as does continuing
  to type after ruling out any matches.

* Add tips/suggestions:
  * When opening a workspace for the first time, suggest compatibility.claudeMarkdown and
    compatibility.claudeSkills if it has a CLAUDE.md or .claude/skills.
  * This can actually be an onWorkspaceTrust hook, executed within the Session.
    * ... we need to define a whole bunch of hookable moments, here.
  * This can then send a msg / AskUserQuestion to the user, in either TUI or VSCode.
* Improve Workspace trust msg:
  * When querying about workspace trust, list any workspace skills auto-allowed by config.

* Merge `ThinkingCommandProvider`'s "Enable/Disable thinking" and "Set thinking effort"
  command-palette entries into one Off/Low/Medium/High choice, mirroring the VS Code plugin's
  merged thinking chip/`klorb.setThinking` QuickPick (see
  docs/adrs/merge-thinking-enabled-and-effort-into-one-picker.md).

## VSCode plugin

### Bugs

### Feature backlog

* When the user types `/` it should pop up a fuzzy-finder panel to hone in on the skill the user
  wants to invoke.

* Add a `klorb.logLevel` argument to the VSCode plugin config that accepts
  DEBUG/INFO/WARNING/ERROR/CRITICAL and if not empty, sets `$KLORB_LOG_LEVEL` in the environment
  used to spawn the `klorb server` process.

* "Per-tool breakdown" stats within Session Statistics should be rendered as an aligned grid
  or `<table>`, not just a bunch of text.

## Remaining / future work from `plan` epics

Some `plan` documents were only partially implemented; others explicitly mentioned follow-up work
imagined during the plan but out of scope for the plan itself. These follow-up action items are
documented in the subsections below.

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

### Plan 017: Multiple sessions per workspace

* Surface a "delete this saved session" action (TUI palette command and/or VS Code quickpick
  item) rather than relying solely on `MAX_RECENT_SESSIONS` pruning to reclaim space.
* Show a relative recency timestamp ("2 hours ago") in the Load Session picker (shown when
  you click the "Session History" button) -- today's design deliberately has no timestamp field on
  `RecentSession`, only list order; adding one is backwards-compatible.
  * Add a lastModifiedTimestamp field to Session, stored in the session.json, and also stored in
    the RecentSession model. The field is serialized as an ISO-8601 datetime in both places.
    If this timestamp is provided, then the quickpick shows a relative age for the entry,
    like `<session_title> (2 hours ago)`. The relative age should be approximate: `just now` if
    it's less than 2 minutes old. `<n> minutes ago` within the last hour, `<n> hours ago` if
    it's within the last 24 hours, `<n> day(s) ago`, `last week`, `last month`, then a
    month-and-year format for older: `April, 2025`.
* `docs/specs/klorb-server.md`'s `fork_session`/`resume_session` stubs are unaffected by this
  plan; a future plan could build genuine session forking on top of this same `sessions/`
  directory layout.

### Plan 018: Bash network egress

* Live, mid-connection interactive ask (`ProxyAskBroker` + a new concurrent-ask transport for
  both the TUI and ACP), gated on measuring how often the static pre-flight scanner actually
  misses a domain a live ask would have caught (`blocked_domains` on the `Bash` response is the
  data source for that measurement).
* Port-scoped `DomainSpec` matching (`localhost:3000`), so a loopback/LAN grant for one workflow
  doesn't implicitly cover every other locally-bound service.
* Single-port SOCKS/HTTP-CONNECT protocol sniffing instead of two fixed listener ports, if
  managing two turns out to be operationally annoying.
* Non-HTTP(S) protocols as a first-class, pre-flight-recognized command shape (`ssh`/`scp`/
  `rsync` and friends) -- today these simply have no egress path at all.
* Third-party domain-reputation/malware-blocklist integration (same deferral as `WebFetch`'s own,
  see "Plan 013: WebFetch" above).
* TLS-terminating inspection, if a future need for content-level (not just domain-level) request
  filtering emerges.
* `HTTP_PROXY`/`HTTPS_PROXY` only understands `CONNECT`, not forward-proxy request/response
  relaying -- a plain-`http://` target (as opposed to `https://`) gets no reply at all instead of
  a domain-gated refusal. Narrow in practice (registry/API traffic is HTTPS-only today), but
  worth closing if it ever bites a real workflow. See `klorb.sandbox.network`'s module docstring.
* `bashDomains.allow`'s packaged defaults cover `pip`/`uv`/`npm` (PyPI, npm registry) but not
  Maven Central (`cargo`/`go`'s registries are also unlisted) -- add `repo.maven.apache.org` (and
  reconsider `crates.io`/`proxy.golang.org`) once real usage shows they're worth defaulting to
  rather than an ordinary first-use `ask`.

### Plan 020: Vision / image input

* TUI/CLI image attachment (`--image path.png`, or a `>attach <workspace-file>` palette
  command) -- no drag-drop/paste-image surface to build the UI on top of in a raw terminal; the
  resize pipeline and `MessageFragment`/ACP plumbing are already UI-agnostic and reusable once a
  TUI-side entry point exists.
* Remote image URLs (`{"image_url": {"url": "https://..."}}` instead of always inlining base64),
  gated through WebFetch's existing `DomainAccessTable` for permission screening -- smaller wire
  payload, at the cost of a new network trust boundary. Images are always inlined today.
* Remote file upload / preflighting -- sending image files to a dedicated file upload endpoint
  of the provider and using a reference to the uploaded file, instead of resending base64 every
  turn.
* Image retention/pruning policy -- an analogue to the existing per-model `drop_reasoning`
  toggle, e.g. dropping or summarizing image fragments from history after N turns, to cap the
  ongoing resend-token cost of an image attached early in a long session (klorb's session
  history has no context-pruning mechanism at all yet).
* Re-verify `gpt-5-nano`/`mimo-v2.5`'s exact resolution/token-formula numbers against
  OpenRouter's live `/models` response as that API's data evolves -- sourced from each vendor's
  own docs today (OpenAI's `images-vision` guide; MiMo-V2.5's own `preprocessor_config.json`),
  not guessed, but the live API is the tie-breaker if OpenRouter's routing ever imposes
  different effective limits than the base model's own spec.
* Kimi's token formula remains genuinely unpublished -- still riding the generic Anthropic-tiles
  fallback until Moonshot AI publishes one.
* Client-side (webview canvas) pre-downscaling before the postMessage hop, if raw-image transit
  over stdio/postMessage proves to be a practical bottleneck in real use -- the resize pipeline
  is server-authoritative today.

### Plan 021: Subagents

* Subagent `assigned_task_id`: add a field to `CreateSubagent` that lets the parent start the
  subagent off with a specific todo item. This requires reworking the todo tools' current
  greedy auto-assignment behavior (which shoves new tasks onto an agent as soon as it closes
  its last task) so that a subagent isn't automatically chewing through the entire todo list
  when it may not be equipped to do so.
  * Right now, TodoUpdate automatically claims the next free task whenever it closes the
    current task. There is no way to bound the set of tasks considered for this process.
    Therefore, we cannot reveal task mgmt tools to subagents, because a subagent intended
    to perform a specific subtask of the project would finish the subtask and be driven
    to take up tasks intended for other agents, which it may be incapable of doing.
  * To release task tracking to subagents, we need to add and respect per-subagent label
    filters and the ability for the parent agent to mark tasks with a filter as a means of
    delegating the task to the subagent.

* Adding a `VisionAssistant` agent role.
  * This is marked as 'phase 6' of the plan. This phase was left incomplete.
  * Ideally, we have a way to delegate to a subagent with vision support when we stumble
    upon a screenshot or other image file and the parent does not have vision support, or
    does not want to load the image into its own context.
  * We don't have a means of giving a blind model an image file in a way it can then tramsit it
    "as an image_url attachment" into a MessageFragment for the subagent to use; adding this is
    a blocking feature before this agent role can be produced.
  * Once available, allow both Operator and Explorer to use VisionAssistant subagents.
