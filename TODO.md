
# TODO

## Agent / Harness

### Bugs

* the 'screenshot' option in the cmd palette doesn't work.

* If the user approves a bashDomain mid-session, a persistent bash shell doesn't seem to pick it up.
  (Do we need to kill the persistent bash session so the next command loads it fresh?)

### Feature backlog

* Rework various tool calls to be less json-like.
  * edit_file_core & related: move most content to k/v pair lines, then fmt `post_edit_content` and `diff`
    as plain text blocks with line number prefixes.
  * create_file_core, likewise, plaintext "diff" block.
  * CreateFile and ReplaceAll: add "no verification ReadFile needed" language to response.
    * ... and same for EditFile.
  * Append to the FindFile description:
    *"The search is recursive within `dirname`. To find all files named `summary.txt` at any depth under `reports/`, use `pattern='summary.txt', dirname='reports'` — no `**` path syntax is needed."*
  * bash tool: print in format:

    ```
    command: (str)
    success: bool
    [failure_reason: str]
    exit_status: 0
    [stdout_file: <str>]
    [stderr_file: <str>]
    runtime: <num>
    terminal_alive: bool
    terminal_cwd: bool
    [sandbox_rebuilt: bool if true]
    [sandbox_notice: str]
    [blocked_domains: list of str]

    stdout
    ========
    <stdout text follows. empty just means print nothing here>

    stderr
    ========
    <stderr text follows. empty just means print nothing here>
    ```

    key order there is chosen literally.
    if stdout_file is given, omit the `stdout` plaintext block. same for stderr.
  * GrepTool should likewise be a bunch of k/v stuff at the top then it should have
    mostly plaintext blocks, per matched file:

    ```
    matching_file_1.py
     2|bla
    *3|the match
     4|context again
     12|foo
    *13|match two
     14|bar

    matching_file_2.py
    *1|the match in the 2nd file
     2|and so on...
    ```

* We can then go on to rewriting history for tighter context.
  * tool calls contain a `tool_args` field. This was produced by the agent. we apply it
    directly to the tool. We also then recapitulate it into the messages array (in a role=agent
    stop_reason=tool_calls msg) forever.
  * Tool gets `#update_args(tool_args: dict, tool_response: Any, err_info: (those 4 err fields)) -> dict`
    which by default just returns tool_args unchanged.
    But we call this with all the tool output (the response from the tool itself, none if
    that all got handled by exception) and a struct w/ the other error info we would otherwise
    format into fields of the response, etc. And we return a new tool_args that may just be
    exactly the original tool_args but may be more compact.
    * For most error situations, we return the input tool_args as-is. But on success, sometimes
      we don't need to keep it all.
    * For e.g. CreateFile(), the output will have all the new file content. So, omit the
      content block entirely from the tool_call as-shown back to the agent for the rest of
      the conversation.
    * Same for the various EditFileCore-based tools. We already get the new content and
      a diff, out. So, drop the input from the re-send.
    * Same for other CreateFileCore tools.
    * All ReadFileCore tools have sufficient context in the output that, for successful read,
      the actual ReadFoo tool call can just lose all its tool_args.
    * Again, if there's an error, just leave the input verbatim.
    * We actually do serialize this transmutation for session.json, etc. We save this
      on a reflected_tool_args field; post-tool-use, how do we reflect the agent's tool
      args back to it?

* use inotify to invalidate agent file reads?
  * We can use inotify to know when a file was edited outside an EditFile command. That can be used
    to inform the agent that it needs to re-ReadFile before it makes further edits there if we want
    to either do a systeminterjection, or remove stale ReadFile tool results from context, or even
    rewrite the context history so the next time it is magically updated...

* `BashTool` stderr/stdout should have the `SecretDetector` applied to it.

* If the agent reads a file with anything `ReadFileCore`- or `Grep`-oriented and the `SecretDetector`
  masks out a secret, put the file on a list of sensitive files. This file list should be fed to
  the BashTool command classifier and if it appears that a bash command could give the agent unmasked access
  to the file contents or otherwise exfiltrate the credentials, it should be marked as 9/10+ risk
  (credential extraction attempt).

* New Subagent roles:
  * pair_programmer -- a subagent that works closely alongside the main Operator while doing a large task.
    The two agents can pass messages/responses back and forth for a conversation about the design, and once
    they both agree on the design, they can move forward with the work
    * the pair programmer should use an inotify / FileChanged Event hook to get updates on all the files
      being edited by the primary Operator. It should do reviews in real time as the edits are happening,
      to provide feedback as it goes. The Pairer should feel at libery to explore other files near the
      site of the change to give better feedback. It should be doing code reviews as it goes.
    * We need the ability to enable the FileChanged event handler only for a specific subagent, perhaps
      as part of a particular skill that it activates.
    * If the main Operator isn't expecting further communication with the subagent, subagent message
      output in response to FileChanged isn't going to make it back to the Operator. It needs a
      `MessageAgent()` tool that will force the message into its context.
  * project_manager -- keep track of fine-grained tasks and ensure that they are all
    completed by other agents (or keep the Operator parent agent honest about progress). When given
    a medium-grain task, break it down into additional fine-grained tasks and ensure they're
    registered with chainlink.
  * Add a /make-plan-tasks skill that explains how to recursively break down a plan into steps and link
    them together with the chainlink-based Todo* tools.
  * Add a skill for Operator to manage a Planner, one or more Implementers, and Reviewer(s) to
    deliver a complete feature.
  * ... The goal of this is to eventually support a "software factory" model where the system is
    autonomously continuously pulling new tasks out from a queue (e.g. a directory filled with new
    feature spec documents or bug report documents) and executing them one after the next.

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

* (low priority): add a ProviderFactory, to support connections other than openrouter.ai?
  * Produces ApiProviders from a string
  * Currently only openrouter api provider is supported from "openrouter" string.
  * model names now can be fully-qualified model name (fqmn): e.g.: "openrouter:gpt-4o-mini"
  * Session can get the current Provider from the ProviderFactory.
  * /clear to create a new session keeps the same model name (and thus model, provider) as last session.

* More tools:
  * Add Evals for GrepTool, SemanticSearchTool, and FindFileTool.
  * WebSearchTool -- use Brave Search: <https://api-dashboard.search.brave.com/app/plans>
    (see "Plan 013: WebFetch" section below)
  * BroadcastMessage -- send a msg to the whole agent team
  * Improve ability to use MessageSubagent for peer to peer 1:1 messaging while agent loops are ongoing?
  * SearchTools currently only does case-insensitive literal match over tool
    name/description/parameter schema docs. Add semantic index search too (requires a
    json- or tool-specific chunker).

* Improvements to Skills:
  * Add general skills/know-how for writing docs/specs and docs/adrs/ files.
  * Eventually when we have a lot of skills, the skill list that is auto-advertised in the
    initial SystemInterjection should be pruned and only display some top most-relevant
    skills or most-frequently-used skills. Let the agent discover others via SearchSkills.

* Permissions
  * BashTool / bubblewrap sandbox follow-ups: a `--seccomp` defense-in-depth
    filter (ptrace/mount/reboot/keyring).
  * A wildcard `readFiles`/`writeFiles` rule (e.g. `*.pem`) is enforced by `FileAccessTable`
    against the agent's own file tools, but `bwrap` sandboxing currently skips wildcard rules.
    (We currently use this as a hack to deny ReadFile access to .git-credentials while still
    permitting `git` to see the file...)
  * TOCTOU: every permission check (klorb.permissions.workspace/directory_access) resolves a
    path string at check time; nothing holds an open OS-level directory handle across the gap
    between that check and the actual file I/O, so a rename/symlink swap in that window could
    redirect an approved operation. Closing this needs os.open()-based fd-relative I/O
    (O_NOFOLLOW/O_DIRECTORY), not path-string re-resolution. See docs/specs/permissions.md.

* Metacognition tools -- read config; update (in-memory) config; update config file(s)

* Context auto-compaction

* Integrate with `chainlink init --db-only`, once merged. Then we don't need to include the code
  to remove all the extraneous stuff it adds. (see docs/specs/chainlink-task-tracking.md)
* Integrate with `chainlink`'s `blocked_by_open` field, once merged. Then we don't have to look
  up every task in the `blocked_by` list to calculate a true blocker list / `open_blocker_count()`.
* Integrate with multiple `--label` filters in `chainlink issue list` when merged.

* Add more system interjections:
  * If the agent does *not* have a plan, after a while, redirect it to write down some
    objectives for itself via TodoWrite and use TodoNext to start focusing on task-oriented work.
  * Start adding system interjections mentioning how many turns the agent has taken, or how
    many tool calls (vs total tool call budget / limit) it has performed. Parents should be able to
    set (more limited) tool use budgets for child sessions.

* [harness/CLI feature] Add a `--export` option to the `klorb system-prompt` CLI command that
  dumps the *resolved* system prompt files for the current role + model into the user's
  editable tree (`$KLORB_CONFIG_DIR/system_prompts.d/...`, at the same relative path the
  resolver would read it back from), so the user gets a real `.md` file to start editing
  instead of hunting down the packaged copy inside site-packages. Refuse to clobber an
  existing file without `--force`, mirroring `klorb init`'s clobber-protection (see
  docs/specs/klorb-init.md). See docs/specs/roles-and-system-prompts.md for how the resolver
  picks files per role/model.

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

## VSCode plugin

### Feature backlog

* A tool call's `system_interjections` never reach the history view today, live or replayed
  (`ToolCallEvent` carries no such field, and `update_mapping.py`'s replay builder discards
  `system_interjections` when decoding a saved `tool_response`). Surface them the same way
  `parseSystemInterjections.ts`/`HistoryView.tsx` already render a `role="user"` prompt's
  `<SystemInterjection>` blocks: thread a structured field through the live ACP update and
  `_replay_tool_call_entry`/`build_session_replay`, and add a webview rendering path fed that
  structured data instead of text-parsed. See docs/adrs/00207-render-tool-response-wire-text-at-send-time-not-storage.md.

* VSCode should show a custom icon for the plugin in the 'installed plugins' list.

* The task panel div has style `.task-panel-list` which specifies `max-height: 40vh;`.
  How tall is that, exactly? I feel like it should be no more than 5 or 6 rows high.

## Remaining / future work from `plan` epics

*Some `plan` documents were only partially implemented; others explicitly mentioned follow-up work*
*imagined during the plan but out of scope for the plan itself. These follow-up action items are*
*documented in the subsections below.*

### Plan 013: WebFetch

* Third-party malware blocklisting: query external threat lists and auto-deny requests to
  domains on blocklist(s) maintained by trusted third parties, not just the user's own
  `deny` list. Now that hooks exist (docs/specs/hooks-and-events.md), implement as an
  `onToolUse`/`onToolResult` consumer instead of bespoke `WebFetch` code — see
  "Plan 022: Hooks and Events" below.
* Cookie handling: a session-scoped `httpx.Client` (held in
  `session.tool_state["WebFetch"]["client"]`) to enable cookie persistence across calls,
  instead of the fresh per-call client used today.
* POST/PATCH/PUT with a request body (JSON, form data, or raw bytes), now that the
  read-only GET path is solid.
* `Tool.is_read_only()` needs a conditional form `is_read_only(args)` once WebFetch
  supports methods besides GET, so it can return True for GET/HEAD/OPTIONS and False
  otherwise.
  * Use AgentCapabilities to control which http methods a subagent can use.
* Dedicated WebSearchTool -- use Brave Search: <https://api-dashboard.search.brave.com/app/plans>

### Plan 017: Multiple sessions per workspace

* Surface a "delete this saved session" action (TUI palette command and/or VS Code quickpick
  item) rather than relying solely on `MAX_RECENT_SESSIONS` pruning to reclaim space.
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
* Kimi's token formula remains unpublished -- still estimating via the generic Anthropic-tiles
  fallback until Moonshot AI publishes one.
* Client-side (webview canvas) pre-downscaling before the postMessage hop, if raw-image transit
  over stdio/postMessage proves to be a practical bottleneck in real use -- the resize pipeline
  is server-authoritative today.

### Plan 021: Subagents

* Adding a `VisionAssistant` agent role.
  * This is marked as 'phase 6' of the plan. This phase was left incomplete.
  * Ideally, we have a way to delegate to a subagent with vision support when we stumble
    upon a screenshot or other image file and the parent does not have vision support, or
    does not want to load the image into its own context.
  * We don't have a means of giving a blind model an image file in a way it can then tramsit it
    "as an image_url attachment" into a MessageFragment for the subagent to use; adding this is
    a blocking feature before this agent role can be produced.
  * Once available, allow both Operator and Explorer to use VisionAssistant subagents.

### Plan 022: Hooks and Events

* `onRequestPermission` hook: deferred entirely. A real design needs to reconcile
  `HookOutput.permission` (a bare `Verdict`) against the richer `PermissionDecision`
  (`action`+`scope`, `klorb/src/klorb/session/events.py`) a human/UI answer produces.
* A genuine persistent daemon mode, so `Timer` events can run 24/7 (or catch up if
  downtime skipped the moment)
* Hot-reloading hook/event config edited mid-process, without a full restart.
* Surfacing hook activity in the UI — a TUI/VSCode view of which hooks fired, what they returned,
  and whether they errored, rather than only `logger.debug()`/`warning()` output.
  * Easier MVP might be to surface stderr from the hook script via logger.warning().
* `HookOutput.interrupt` is only respected alongside `reset_session`
  (`Session._prepare_reset_session`); a bare `interrupt` with no `reset_session` is still a no-op.

### Plan 023: TUI history virtualization

* Empirically tune `DEFAULT_CHUNK_SIZE_MESSAGES`, the collapse-side hysteresis margin
  (`VirtualizedHistoryContainer.refresh_visibility`'s `margin=self._container.size.height`), and
  `ESTIMATED_LINES_PER_SEEDED_MESSAGE` against a real long session, rather than the values
  Phase 1/2 shipped with.
* Re-check TODO.md's "gets unusable and eventually crashes" entry above once this has run in a
  real long session: if virtualization alone resolves the degradation, drop it from that entry;
  if instability persists, it corroborates a separate non-DOM cause.

### Plan 025: Skill-granted hooks/events

* Generalize `is_heritable` beyond `HookConfig`/`EventConfig` to every grant kind
  (`command_rules`, `skill_rules`, `read_dirs`/`write_dirs`/`read_files`/`write_files`), so a
  subagent's creation can filter those the same way it now filters hooks/events, instead of a
  subagent always inheriting every one of its parent's bash/skill/directory grants unconditionally.

## Meta / dev environment
