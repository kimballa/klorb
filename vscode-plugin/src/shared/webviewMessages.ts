// © Copyright 2026 Aaron Kimball

/**
 * The typed message protocol between the extension host and the webview, shared by both
 * tsconfigs (the host's tsconfig.json and the webview's tsconfig.webview.json). The webview
 * never speaks ACP: the host translates ACP session updates into `HostMessage`s and webview
 * user intent (`WebviewMessage`s) into ACP requests. Later increments extend these two
 * unions rather than inventing ad hoc message shapes.
 */

/** A new prompt turn began (the user's prompt was accepted and sent to the server). */
export interface TurnStartedMessage {
  type: 'turnStarted';
}

/** A streamed piece of the agent's response text for the current turn. */
export interface AgentChunkMessage {
  type: 'agentChunk';
  text: string;
}

/** A streamed piece of the agent's thinking text for the current turn. */
export interface ThoughtChunkMessage {
  type: 'thoughtChunk';
  text: string;
}

/** The current turn finished; `stopReason` is the ACP stop reason string (e.g. "end_turn",
 * "cancelled"). */
export interface TurnEndedMessage {
  type: 'turnEnded';
  stopReason: string;
}

/** The current turn (or an attempt to start one) failed with an error. */
export interface TurnErrorMessage {
  type: 'turnError';
  message: string;
}

/** The current turn failed because the `klorb server` child process itself was lost (crashed,
 * or was killed/restarted out from under the in-flight turn) -- distinct from an ordinary
 * `TurnErrorMessage` so the history can offer a "Restart Server" action alongside the error
 * text instead of just reporting the failure. */
export interface ServerLostMessage {
  type: 'serverLost';
  message: string;
}

/** A message the user submitted while a turn was already in flight was queued into the running
 * turn (`_klorb/enqueueMessage`), not rejected -- render it in italic "Queued message" styling
 * until the matching `QueuedMessageSentMessage` arrives. */
export interface MessageQueuedMessage {
  type: 'messageQueued';
  text: string;
}

/** A previously-queued message (see `MessageQueuedMessage`) was actually delivered -- either
 * folded into a tool-response envelope mid-turn, or redelivered as the next turn once the
 * current one ended (see docs/specs/klorb-server.md's `_klorb/enqueueMessage` section).
 * Correlates with its `MessageQueuedMessage` by order + text (the single-queue reality: the
 * server never has more than one message queued ahead of another with a stable id to match
 * against). */
export interface QueuedMessageSentMessage {
  type: 'queuedMessageSent';
  text: string;
}

/** The conversation was reset (a fresh session replaced the old one); clear the history. */
export interface SessionResetMessage {
  type: 'sessionReset';
}

/** A hook handler's debugging note (`HookOutput.log`, `_klorb/notice`) -- neutral text to
 * surface in the history scroll, distinct from a `HookOutput.message` (which the server folds
 * back into the conversation itself, never reaching the webview as its own message). */
export interface NoticeMessage {
  type: 'notice';
  text: string;
}

/** A sufficiently urgent record parsed from `klorb server`'s JSON-formatted stderr, surfaced in
 * the history scroll; `level` is its Python `logging` module numeric level value. */
export interface ServerLogMessage {
  type: 'serverLog';
  text: string;
  level: number;
}

/** One file location a tool call names, e.g. the file a read/edit call touched. */
export interface ToolCallLocation {
  path: string;
  line?: number;
}

/** One rendered line of a tool-call diff hunk -- mirrors `klorb.tools.util.diff_lines.DiffLine`
 * (see docs/specs/klorb-server.md's tool-call update mapping section), with its `old_lineno`/
 * `new_lineno` fields renamed to camelCase at this host/webview boundary. */
export interface DiffHunkLine {
  kind: 'context' | 'add' | 'del';
  oldLineno: number | null;
  newLineno: number | null;
  text: string;
}

/** A contiguous run of `DiffHunkLine`s, mirroring `klorb.tools.util.diff_lines.DiffHunk`. */
export interface DiffHunk {
  lines: DiffHunkLine[];
}

/** A tool call's diff content, flattened from an ACP `diff` content block: `oldText`/`newText`
 * are always present (ACP's own convention, `oldText: null` for a brand-new file); `hunks` is
 * present only when the server additionally attached `_meta.klorb.diffHunks` (klorb's own
 * server always does), and is preferred for rendering a colored gutter view when present. */
export interface ToolCallDiff {
  path: string;
  oldText: string | null;
  newText: string;
  hunks?: DiffHunk[];
}

/** Structured metadata for a Bash tool call, carried in `_meta.klorb.bash` on ACP
 * `tool_call`/`tool_call_update` updates so the webview can render the call's intent,
 * command, and result fields independently instead of parsing a flat title string. */
export interface BashToolMeta {
  /** The shell command the agent asked to run. */
  command: string;
  /** The agent's description of why it's running this command (omit when absent). */
  intent?: string;
  /** Whether the command succeeded (omit when not yet finished). */
  success?: boolean;
  /** The process exit code (omit when not yet finished, or when the process timed out). */
  exitStatus?: number;
  /** Wall-clock runtime in seconds (omit when not yet finished). */
  runtime?: number;
  /** Captured stdout (omit when not yet finished, or when output was spilled to a file). */
  stdout?: string;
  /** Captured stderr (omit when not yet finished, or when output was spilled to a file). */
  stderr?: string;
}

/** Structured metadata for a ReadFile tool call, carried in `_meta.klorb.readFile` on ACP
 * `tool_call_update` updates so the webview can render line-numbered content instead of
 * parsing the JSON `detail_view()` text block. */
export interface ReadFileMeta {
  /** The file that was read. */
  filename: string;
  /** Line-numbered content (each line prefixed with `N|`), truncated to 8 lines. */
  content: string;
}

/** A tool call was just started (ACP `tool_call` update): `kind` and `status` are left as
 * plain strings (mirroring `TurnEndedMessage.stopReason`) rather than a literal union, so an
 * ACP `ToolKind`/`ToolCallStatus` value this client doesn't recognize yet still round-trips
 * instead of failing to parse. `toolName` is the server's own `_meta.klorb.toolName` (e.g.
 * `"CreateSubagent"`), omitted for a non-klorb ACP agent -- lets a listener distinguish tools
 * that share one `kind` bucket (see `update_mapping.TOOL_KIND_MAP`'s `"other"` entries). */
export interface ToolCallStartedMessage {
  type: 'toolCallStarted';
  callId: string;
  title: string;
  kind: string;
  locations: ToolCallLocation[];
  bashMeta?: BashToolMeta;
  toolName?: string;
}

/** A tool call finished or otherwise changed (ACP `tool_call_update`); mutates the matching
 * history entry in place. */
export interface ToolCallUpdatedMessage {
  type: 'toolCallUpdated';
  callId: string;
  status: string;
  title?: string;
  contentText?: string;
  diff?: ToolCallDiff;
  locations?: ToolCallLocation[];
  bashMeta?: BashToolMeta;
  readFileMeta?: ReadFileMeta;
}

/** One selectable option in a permission-ask option grid, flattened from an ACP
 * `PermissionOption`: `scope` is the option's own `_meta.klorb.scope` token (see
 * docs/specs/klorb-server.md's permission-ask options table), omitted when the server didn't
 * attach one (e.g. a peer ACP agent that isn't klorb). */
export interface PermissionAskOption {
  id: string;
  name: string;
  kind: string;
  scope?: string;
}

/** A permission ask (or an `EscalatePrivileges` ask, distinguished by `klorbMeta.escalation`)
 * the server is waiting on an answer for -- mounts the `ApprovalPanel` in the interaction area
 * until a matching `permissionDecision` resolves it. `klorbMeta` is the request's `_meta.klorb`
 * payload verbatim (see docs/specs/klorb-server.md's "Permission asks and escalation" section):
 * `resourceDescription`, and for a bash ask `commandText`/`itemCommandText`/`itemIndex`/
 * `itemTotal`/`grantPatterns`/`riskLevel`, or for an escalation ask `escalation`.
 * `originSessionId`, present only when this ask was raised by a subagent's turn rather than the
 * root session's own (`klorb.session.events.PermissionAskContext.origin_session_id`, forwarded
 * through `klorb.agents.policy.build_subagent_turn_handlers`), names which session in the
 * subagents panel this ask belongs to -- the panel only renders it once that session is
 * selected, mirroring the TUI's `SubagentsPanelMixin._await_session_selected` gate. */
export interface PermissionAskMessage {
  type: 'permissionAsk';
  requestId: number;
  title: string;
  options: PermissionAskOption[];
  klorbMeta: Record<string, unknown>;
  originSessionId?: string;
}

/** One selectable option in a `questionAsk`'s option list, flattened from a `_klorb/
 * askUserQuestions` request's own `options` entry. */
export interface QuestionAskOption {
  label: string;
  description?: string;
}

/** A `_klorb/askUserQuestions` ext request the server is waiting on an answer for -- mounts the
 * `QuestionPanel` in the interaction area until a matching `questionAnswer` resolves it. One
 * question of a multi-question `AskUserQuestions` batch, asked serially (`index`/`total` name
 * this question's position, e.g. "Question 2 of 3" -- see docs/specs/klorb-server.md's
 * extension-method registry). `originSessionId` mirrors `PermissionAskMessage.originSessionId`
 * -- see that field's own doc comment. */
export interface QuestionAskMessage {
  type: 'questionAsk';
  requestId: number;
  header: string;
  question: string;
  options: QuestionAskOption[];
  index: number;
  total: number;
  originSessionId?: string;
}

/** A `_klorb/raiseToolCallLimit` ext request the server is waiting on an answer for -- mounts
 * the `ToolCallLimitPanel` in the interaction area until a matching
 * `toolCallLimitDecision` resolves it. The server doubles the tool-call cap on `approved`
 * and stops the turn on denial. */
export interface ToolCallLimitAskMessage {
  type: 'toolCallLimitAsk';
  requestId: number;
  message: string;
}

/** A thinking-effort level, mirroring `klorb.session.constants.ThinkingEffort`. */
export type ThinkingEffort = 'low' | 'medium' | 'high';

/** A coalesced snapshot of the session's control-plane state -- the status row's data source.
 * Every field is independently optional: the host posts this whenever any one piece changes
 * (a `current_mode_update`, a `session_info_update`, a `_klorb/usage` notification, or a
 * `_klorb/getSessionConfig`/`_klorb/setSessionConfig` round trip), always with the *complete*
 * currently-known state -- not a delta -- so the webview can simply replace its local status
 * with each message rather than merging partial updates (see
 * `host/features/sessionControls/sessionControls.ts`). A field stays absent until the host
 * has learned it at least once (e.g. `model` before the first `_klorb/getSessionConfig`
 * reply); `maxTokens`/`sessionTitle` additionally use `null` for a real "no limit"/"no title"
 * value, distinct from "not yet known". */
export interface StatusUpdateMessage {
  type: 'statusUpdate';
  model?: string;
  thinkingEnabled?: boolean;
  thinkingEffort?: ThinkingEffort;
  permissionMode?: string;
  usedTokens?: number;
  maxTokens?: number | null;
  outputTokens?: number;
  sessionTitle?: string | null;
  workspaceTrusted?: boolean;
  /** Whether the connected server advertised `_klorb/enqueueMessage` at `initialize()` -- known
   * once per connection (set alongside the rest of `session/new`'s state, see
   * `SessionControls.applySessionInfo`), not per-session. Determines whether the prompt input
   * stays enabled during a turn (mid-turn submit queues into the running turn) or falls back to
   * 002's disabled-while-in-flight behavior. */
  enqueueMessageCapable?: boolean;
  /** Whether the currently active model supports image input -- gates the prompt input's
   * attach-image affordance (drag/paste/file-picker). Known once per `_klorb/getSessionConfig`
   * round trip, same as `model` -- see docs/specs/vision-image-input.md. */
  activeModelVision?: boolean;
  /** Whether the connected server advertised `agentCapabilities._meta.klorb.subagents` at
   * `initialize()` -- known once per connection, not per-session (mirrors
   * `enqueueMessageCapable`). Gates whether the subagents panel polls/renders at all, so an
   * older server that predates `_klorb/subagentTree` doesn't leave the panel permanently empty
   * with no explanation. */
  subagentsCapable?: boolean;
}

/** An ordered label -> numeric-value map, rendered as a right-aligned two-column table row per
 * entry (see `SessionStatsMessage`). */
export type SessionStatsCounts = Record<string, number>;

/** One tool's success/failure row in a `SessionStatsMessage`'s "Per-tool breakdown". */
export interface SessionStatsToolRow {
  name: string;
  succeeded: number;
  failed: number;
}

/** A rendered `_klorb/sessionStats` result (`klorb.showSessionStats`), appended to the history
 * as a `SessionStatsCard` -- mirrors `klorb.session_statistics.SessionStatistics.
 * format_report()`'s TUI layout (see docs/specs/vscode-plugin.md's status row and session
 * controls section) as two right-aligned numeric tables plus a separated cost line, rather
 * than the TUI's own preformatted monospace text. `cachePercent` is the "Cached tokens" row's
 * own extra percentage annotation, not a `tokenUsage` entry of its own; `totalCost` renders in
 * its own visually-separated block below the token table. */
export interface SessionStatsMessage {
  type: 'sessionStats';
  messageCounts: SessionStatsCounts;
  toolBreakdown: SessionStatsToolRow[];
  tokenUsage: SessionStatsCounts;
  cachePercent: number;
  totalCost: number;
}

/** One task in a `taskListUpdate`, flattened from an ACP `PlanEntry`: `priority`/`status` are
 * left as plain strings (mirroring `ToolCallStartedMessage`'s `kind`) so a value this client
 * doesn't recognize yet still round-trips instead of failing to parse. `issueId` is present only
 * when the server attached its own `_meta.klorb` detail (klorb's own server always does; a
 * stock ACP agent's plain `PlanEntry` carries no id at all -- see docs/specs/klorb-server.md's
 * "Chainlink task-plan updates" section); `blocked`/`isCurrentTask`/`closed` fall back to a
 * best-effort guess derived from `status` alone when that detail is absent. */
export interface TaskInfo {
  issueId?: number;
  text: string;
  priority: string;
  status: string;
  blocked: boolean;
  isCurrentTask: boolean;
  closed: boolean;
}

/** The task list's coalesced counts, from a `plan` update's own update-level `_meta.klorb` (or
 * derived from `tasks` when that detail is absent -- see `TaskInfo`). `currentTaskId` is `null`
 * when no task is in progress, or when the server didn't say which one is. */
export interface TaskListSummary {
  openCount: number;
  closedCount: number;
  blockedCount: number;
  currentTaskId: number | null;
}

/** The session's chainlink-backed task list, replaced wholesale on every ACP `plan` update (see
 * docs/specs/klorb-server.md's "Chainlink task-plan updates" section) -- the task panel's data
 * source, mirroring `StatusUpdateMessage`'s own "always the complete snapshot" convention: the
 * server sends every task on each update, never a delta. */
export interface TaskListUpdateMessage {
  type: 'taskListUpdate';
  summary: TaskListSummary;
  tasks: TaskInfo[];
}

/** The user ran **Klorb: Toggle Task Panel** (or clicked its own header pin control): flip
 * whether the task panel is shown at all, independent of its own expanded/collapsed disclosure
 * state (which the webview keeps un-persisted, native `<details>` state). */
export interface ToggleTaskPanelMessage {
  type: 'toggleTaskPanel';
}

/** One session in the live tree rooted at the root session -- mirrors one entry of
 * `klorb.server.subagent_updates.build_subagent_tree_snapshot`'s `"nodes"` list, and (for
 * `role`/`state`/`address`) `klorb.tui.widgets.subagents_panel.SubagentRowData`. The root
 * session's own entry (`parentId: null`) is included alongside every subagent's, so a single
 * `SubagentTreeUpdateMessage` is enough to render the whole selectable tree, root row included
 * -- see docs/specs/vscode-plugin.md's "Subagents panel" section. `model`/`thinkingEnabled`/
 * `thinkingEffort`/`usedTokens`/`maxTokens`/`outputTokens` are this session's own
 * `SessionConfig`/token tally, letting the header/status row follow whichever session is
 * selected without a second round trip -- the VSCode analogue of the TUI's `ReplApp.
 * format_title`/`StatusBarMixin._update_status_bar` reading `_selected_session` directly. */
export interface SubagentNodeInfo {
  id: string;
  parentId: string | null;
  address: string;
  title: string | null;
  role: string;
  state: 'running' | 'finished' | null;
  aborted: boolean;
  model: string;
  thinkingEnabled: boolean;
  thinkingEffort: ThinkingEffort;
  usedTokens: number;
  maxTokens: number | null;
  outputTokens: number;
}

/** A fresh snapshot of the whole subagent tree (`_klorb/subagentTree`), replacing the panel's
 * node list wholesale -- polled by the host while the subagents panel is visible, mirroring
 * `TaskListUpdateMessage`'s own "always the complete snapshot, never a delta" convention. */
export interface SubagentTreeUpdateMessage {
  type: 'subagentTreeUpdate';
  nodes: SubagentNodeInfo[];
}

/** A fresh transcript snapshot for the currently selected subagent (`_klorb/subagentTranscript`),
 * polled by the host while that subagent stays selected -- `entries` is the same
 * `SessionReplayEntry[]` shape a `sessionReplay` carries, so the webview can render it through
 * the same `HistoryView`. `state`/`aborted` drive the trailing status line's four states (still
 * working / task complete / interrupted / sending interrupt…, the last being purely local UI
 * state around a `cancelSubagent` click -- see docs/specs/vscode-plugin.md). `queuedMessages` is
 * every message accepted (via `_klorb/subagentPrompt`) while this subagent's turn was already
 * running but not yet folded into a turn of its own -- there is no push notification for a
 * subagent's queued message the way `MessageQueuedMessage`/`QueuedMessageSentMessage` cover the
 * root session, so the poll itself is what surfaces it. */
export interface SubagentTranscriptUpdateMessage {
  type: 'subagentTranscriptUpdate';
  sessionId: string;
  entries: SessionReplayEntry[];
  state: 'running' | 'finished';
  aborted: boolean;
  queuedMessages: string[];
}

/** The user ran **Klorb: Toggle Subagents Panel** (or clicked its own header pin control),
 * mirroring `ToggleTaskPanelMessage`. */
export interface ToggleSubagentsPanelMessage {
  type: 'toggleSubagentsPanel';
}

/** The webview's subagents panel became visible or hidden -- the host starts/stops polling
 * `_klorb/subagentTree` accordingly (no point polling a tree nothing is drawing). Sent once per
 * actual visibility change (`toggleSubagentsPanel`'s own JSX-mount effect), not per render. */
export interface SetSubagentsPanelVisibleMessage {
  type: 'setSubagentsPanelVisible';
  visible: boolean;
}

/** The user selected a row in the subagents panel: `sessionId: null` selects the root session
 * (restoring normal interactivity), a real id selects that subagent. The host starts polling
 * `_klorb/subagentTranscript` for a non-null selection (stopping any previous selection's poll)
 * and stops entirely for `null` -- see docs/specs/vscode-plugin.md's "Subagents panel" section. */
export interface SelectSubagentMessage {
  type: 'selectSubagent';
  sessionId: string | null;
}

/** The user clicked Stop while a subagent (not the root) was selected: cancel that specific
 * subagent's turn (`_klorb/subagentCancel`), leaving the root session's own turn (if any)
 * untouched -- mirrors the TUI's per-subagent Escape/Ctrl+C handling
 * (`KeyActionsMixin._interrupt_running_activity`/`SubagentsPanelMixin.
 * _note_subagent_interrupt_requested`). */
export interface CancelSubagentMessage {
  type: 'cancelSubagent';
  sessionId: string;
}

/** One text entry replayed from a previously saved session (see `SessionReplayMessage`) --
 * matches `webview/features/history/historyModel.ts`'s own `TextHistoryEntry` shape
 * field-for-field, but is defined independently here (not imported from that webview-only
 * feature) since `shared/` must stay importable by both the host and webview tsconfigs, and a
 * feature's internals aren't exported outside its own barrel -- the same reasoning
 * `ToolCallStartedMessage`/`ToolCallHistoryEntry` are already two distinct-but-similar types
 * for. Always `streaming: false`: a replay entry is already complete, never a live chunk.
 * `images`, present only for a `kind: 'prompt'` entry that had attachments, carries metadata
 * only (`AttachedImageMeta`, no bytes) -- see its own doc comment. */
export interface SessionReplayTextEntry {
  kind: 'prompt' | 'response' | 'thinking';
  text: string;
  streaming: false;
  images?: AttachedImageMeta[];
}

/** One tool-call entry replayed from a previously saved session -- mirrors
 * `ToolCallHistoryEntry`'s shape, restricted to the two terminal statuses a restored call can
 * have (a replayed call was, by definition, not still running when it was saved). */
export interface SessionReplayToolCallEntry {
  kind: 'toolCall';
  callId: string;
  status: 'completed' | 'failed';
  title: string;
  toolKind: string;
  toolName?: string;
  locations: ToolCallLocation[];
  contentText?: string | null;
  bashMeta?: BashToolMeta;
  readFileMeta?: ReadFileMeta;
  expanded: boolean;
}

export type SessionReplayEntry = SessionReplayTextEntry | SessionReplayToolCallEntry;

/** Replays a previously saved session's conversation into the history scroll -- sent once after
 * a successful `session/load` (`_klorb/sessionReplay`), both an explicit "Session history" pick
 * and a resume-latest-on-activation load. The webview applies this over whatever cached history
 * its own `sessionState` persistence already restored (stale-while-revalidate) -- see
 * docs/specs/session-persistence.md. */
export interface SessionReplayMessage {
  type: 'sessionReplay';
  entries: SessionReplayEntry[];
}

/** One saved session, as shown in the "Session history" quickpick -- mirrors
 * `klorb.workspace.session_store.RecentSession`'s `session_id`/`title` fields (`subdir` is a
 * server-internal implementation detail the client never needs). */
export interface RecentSessionSummary {
  id: string;
  title: string | null;
}

/** The workspace's enumerated files, pushed once when the view resolves (mirrors
 * `SessionControls.postSnapshot()`'s "re-push current state on view resolve" convention) --
 * POSIX-style paths relative to the workspace root, already filtered by `files.exclude` and
 * every `.gitignore` in the workspace (see `host/features/fileSearch`). Backs the prompt input's
 * `@`-mention file finder (`webview/features/fileFinder`); empty when no workspace folder is
 * open. */
export interface WorkspaceFilesMessage {
  type: 'workspaceFiles';
  files: string[];
}

/** The workspace's past prompt texts for up/down-arrow recall in the prompt input. Pushed once
 * when the view resolves (mirrors `WorkspaceFilesMessage`'s own repost-on-resolve pattern) and
 * re-pushed after each new prompt is recorded. Empty when no prompts have been submitted yet. */
export interface PromptHistoryMessage {
  type: 'promptHistory';
  entries: string[];
}

/** One discoverable skill, flattened from the klorb server's `_klorb/listSkills` result. */
export interface SkillEntry {
  namespace: string;
  name: string;
  description: string;
}

/** The session's discoverable skill catalog, pushed once when the view resolves and re-pushed
 * after a `_klorb/reloadSkills` completes. Backs the prompt input's `/`-mention skill finder
 * (`webview/features/skillFinder`). Empty when no skills are discoverable. */
export interface SkillsMessage {
  type: 'skills';
  entries: SkillEntry[];
}

/** An image the user picked via the status row's "Attach Image…" menu item (see
 * `AttachImageFileMessage`) is ready to add to the prompt input's pending attachment tray --
 * the same shape a drag-drop/paste attachment reaches internally. */
export interface ImageAttachedMessage {
  type: 'imageAttached';
  image: ImageAttachment;
}

/** Every message the extension host may post to the webview. */
export type HostMessage =
  | TurnStartedMessage
  | AgentChunkMessage
  | ThoughtChunkMessage
  | TurnEndedMessage
  | TurnErrorMessage
  | ServerLostMessage
  | MessageQueuedMessage
  | QueuedMessageSentMessage
  | NoticeMessage
  | ServerLogMessage
  | SessionResetMessage
  | ToolCallStartedMessage
  | ToolCallUpdatedMessage
  | PermissionAskMessage
  | QuestionAskMessage
  | ToolCallLimitAskMessage
  | StatusUpdateMessage
  | SessionStatsMessage
  | TaskListUpdateMessage
  | ToggleTaskPanelMessage
  | SubagentTreeUpdateMessage
  | SubagentTranscriptUpdateMessage
  | ToggleSubagentsPanelMessage
  | SessionReplayMessage
  | WorkspaceFilesMessage
  | PromptHistoryMessage
  | SkillsMessage
  | ImageAttachedMessage;

/** An attached image's caption-worthy metadata, without its bytes -- what a restored/replayed
 * prompt entry (`SessionReplayTextEntry.images`) carries, since the server doesn't resend an
 * already-persisted image's bytes just to redraw a thumbnail (see docs/specs/session-
 * persistence.md's "Image-fragment storage" section). `name`, when known (a drag-drop/file-pick;
 * absent for a clipboard paste), captions as itself with any leading directory parts stripped,
 * or as "(clipboard)" when absent -- see `AttachmentThumbnail`. `width`/`height`, when known,
 * caption as `<width>x<height>`. `AttachmentThumbnail` renders a plain paper-clip placeholder
 * icon in place of a thumbnail whenever bytes aren't present, i.e. exactly an
 * `AttachedImageMeta` that isn't also a full `ImageAttachment`. */
export interface AttachedImageMeta {
  name?: string;
  width?: number;
  height?: number;
}

/** One image the user attached to a prompt (drag-drop, clipboard paste, or the status row's file
 * picker) -- raw bytes travel webview -> host -> ACP as-is; the resize/transcode pipeline runs
 * server-side (see docs/specs/vision-image-input.md). `name`, when known, becomes the image's
 * `filename='...'` header on the server side. */
export interface ImageAttachment extends AttachedImageMeta {
  mimeType: string;
  dataBase64: string;
}

/** The user submitted a prompt from the input box. When `subagentId` is present, this targets
 * that subagent directly via `_klorb/subagentPrompt` -- regardless of its own busy state --
 * instead of the root session (see docs/specs/vscode-plugin.md's "Subagents panel" section). */
export interface SubmitPromptMessage {
  type: 'submitPrompt';
  text: string;
  subagentId?: string;
  images?: ImageAttachment[];
}

/** The user submitted a prompt while a turn was already in flight, and the connected server
 * advertises `_klorb/enqueueMessage`: queue it into the running turn instead of rejecting it
 * (see `SubmitPromptMessage` for the not-in-flight case). */
export interface EnqueueMessageMessage {
  type: 'enqueueMessage';
  text: string;
  images?: ImageAttachment[];
}

/** The user asked to cancel the in-flight turn (Stop button or Escape). */
export interface CancelTurnMessage {
  type: 'cancelTurn';
}

/** The user clicked a `ServerLostMessage` entry's "Restart Server" action -- same as running
 * **Klorb: Restart Server** from the command palette. */
export interface RestartServerMessage {
  type: 'restartServer';
}

/** The user clicked a tool-call location link: open that file (at `line`, if given) in the
 * editor. */
export interface OpenLocationMessage {
  type: 'openLocation';
  path: string;
  line?: number;
}

/** The user clicked a tool call's "Open diff" action. */
export interface OpenDiffMessage {
  type: 'openDiff';
  callId: string;
  path: string;
}

/** The user's decision on a `permissionAsk`, echoed back to the host: `optionId` selects one
 * of the ask's own options (redirecting to free text via `otherText` mirrors the TUI's "Other"
 * row -- see docs/specs/klorb-server.md's decision-mapping section), or `cancelled` for
 * Escape/no answer (deny-once). A proper discriminated union (rather than one interface with
 * optional fields) so a consumer can narrow on `'cancelled' in message` without a runtime
 * guard of its own. */
export type PermissionDecisionMessage =
  | { type: 'permissionDecision'; requestId: number; cancelled: true }
  | { type: 'permissionDecision'; requestId: number; optionId: string; otherText?: string };

/** The user's answer to a `questionAsk`, echoed back to the host: `selectedOptionIndex` picks
 * one of the ask's own `options`, `otherText` is a free-text answer, or `cancelled` for Escape
 * (which stops the whole question batch server-side, unlike a permission ask's per-item deny --
 * see docs/specs/klorb-server.md). A discriminated union for the same reason
 * `PermissionDecisionMessage` is one. */
export type QuestionAnswerMessage =
  | { type: 'questionAnswer'; requestId: number; cancelled: true }
  | { type: 'questionAnswer'; requestId: number; selectedOptionIndex: number }
  | { type: 'questionAnswer'; requestId: number; otherText: string };

/** The user's decision on a `toolCallLimitAsk`, echoed back to the host: `approved` lets the
 * server double the tool-call cap and continue, `cancelled` stops the turn (deny-once). */
export type ToolCallLimitDecisionMessage =
  | { type: 'toolCallLimitDecision'; requestId: number; approved: true }
  | { type: 'toolCallLimitDecision'; requestId: number; cancelled: true };

/** The user clicked the status row's model chip: show the model picker. */
export interface PickModelMessage {
  type: 'pickModel';
}

/** The user clicked the status row's thinking chip: show the Off/Low/Medium/High picker. */
export interface PickThinkingMessage {
  type: 'pickThinking';
}

/** The user clicked the status row's permission badge: cycle ask -> auto -> deny -> ask. */
export interface CyclePermissionModeMessage {
  type: 'cyclePermissionMode';
}

/** The user picked "Set Permission Mode" from the status row's menu: show the Ask/Auto/Deny
 * QuickPick, same as the **Klorb: Set Permission Mode** command -- jumps straight to the
 * picked mode, unlike `CyclePermissionModeMessage`, which only advances one step. */
export interface SetPermissionModeMessage {
  type: 'setPermissionMode';
}

/** The user picked "Session Stats" from the status row's menu: show the current session's
 * stats, same as the **Klorb: Show Session Stats** command. */
export interface ShowSessionStatsMessage {
  type: 'showSessionStats';
}

/** The user picked "New Session" from the status row's menu: start a fresh session, same as
 * the **Klorb: New Session** command. */
export interface NewSessionMessage {
  type: 'newSession';
}

/** The user picked "Reload Skills" from the status row's menu: reload skills, same as the
 * **Klorb: Reload Skills** command. */
export interface ReloadSkillsMessage {
  type: 'reloadSkills';
}

/** The user picked "Attach Image…" from the status row's menu: open a native file picker
 * (`vscode.window.showOpenDialog`, filtered to image files) and, if one was chosen, add it to
 * the prompt input's pending attachment tray via a follow-up `ImageAttachedMessage` -- the same
 * end state a drag-drop onto the input reaches, just triggered from the menu instead. */
export interface AttachImageFileMessage {
  type: 'attachImageFile';
}

/** The user committed an edit to the session title (double-click-to-edit in `PanelHeader`).
 * `title: null` clears it back to unnamed -- same as an empty/whitespace-only input, which the
 * webview already normalizes to `null` before posting. */
export interface RenameSessionMessage {
  type: 'renameSession';
  title: string | null;
}

/** The user clicked the stopwatch ("Session history") icon: fetch this workspace's saved
 * sessions (`session/list`) and show them in a native `showQuickPick`; picking one loads it
 * (`session/load`), replacing the live session -- all handled host-side (`klorb.browseSessions`)
 * in response to this one trigger, the same way `NewSessionMessage` drives `klorb.newSession`. */
export interface ListRecentSessionsMessage {
  type: 'listRecentSessions';
}

/** The webview has mounted and its message listener is active -- the host should push the
 * current prompt history. Sent once on webview mount as a pull mechanism to complement the
 * host's deferred push in `resolveWebviewView`. */
export interface RequestPromptHistoryMessage {
  type: 'requestPromptHistory';
}

/** The webview's top-level `ErrorBoundary` (`src/webview/components/ErrorBoundary.tsx`) caught
 * an uncaught render error -- the webview's own JS console (VS Code's "Developer: Open Webview
 * Developer Tools") always has the full detail first, but a webview crash is otherwise invisible
 * anywhere the "Klorb" output channel is concerned, so `ErrorBoundary` reports it here too, and
 * `KlorbSessionViewProvider` logs it to that channel. */
export interface WebviewErrorMessage {
  type: 'webviewError';
  message: string;
  stack?: string;
}

/** Every message the webview may post to the extension host. */
export type WebviewMessage =
  | SubmitPromptMessage
  | EnqueueMessageMessage
  | CancelTurnMessage
  | RestartServerMessage
  | OpenLocationMessage
  | OpenDiffMessage
  | PermissionDecisionMessage
  | QuestionAnswerMessage
  | ToolCallLimitDecisionMessage
  | PickModelMessage
  | PickThinkingMessage
  | CyclePermissionModeMessage
  | SetPermissionModeMessage
  | ShowSessionStatsMessage
  | NewSessionMessage
  | ReloadSkillsMessage
  | RenameSessionMessage
  | ListRecentSessionsMessage
  | AttachImageFileMessage
  | SetSubagentsPanelVisibleMessage
  | SelectSubagentMessage
  | CancelSubagentMessage
  | RequestPromptHistoryMessage
  | WebviewErrorMessage;

/** Message `type` values that carry a required string field, keyed by the field's name. */
interface FieldSpec {
  field: 'text' | 'message' | 'stopReason';
  types: readonly string[];
}

const HOST_FIELD_SPECS: readonly FieldSpec[] = [
  {
    field: 'text',
    types: ['agentChunk', 'thoughtChunk', 'messageQueued', 'queuedMessageSent', 'notice'],
  },
  { field: 'stopReason', types: ['turnEnded'] },
  { field: 'message', types: ['turnError', 'serverLost'] },
];

const HOST_BARE_TYPES: readonly string[] = [
  'turnStarted',
  'sessionReset',
  'toggleTaskPanel',
  'toggleSubagentsPanel',
];

const WEBVIEW_BARE_TYPES: readonly string[] = [
  'cancelTurn',
  'restartServer',
  'pickModel',
  'pickThinking',
  'cyclePermissionMode',
  'setPermissionMode',
  'showSessionStats',
  'newSession',
  'reloadSkills',
  'listRecentSessions',
  'attachImageFile',
  'requestPromptHistory',
];

function parseMessage(
  data: unknown,
  fieldSpecs: readonly FieldSpec[],
  bareTypes: readonly string[]
): Record<string, unknown> | undefined {
  if (typeof data !== 'object' || data === null) {
    return undefined;
  }
  const record = data as Record<string, unknown>;
  const type = record.type;
  if (typeof type !== 'string') {
    return undefined;
  }
  if (bareTypes.includes(type)) {
    return record;
  }
  const spec = fieldSpecs.find((candidate) => candidate.types.includes(type));
  if (spec === undefined || typeof record[spec.field] !== 'string') {
    return undefined;
  }
  return record;
}

function isToolCallLocation(value: unknown): value is ToolCallLocation {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  return typeof v.path === 'string' && (v.line === undefined || typeof v.line === 'number');
}

function isToolCallLocationArray(value: unknown): value is ToolCallLocation[] {
  return Array.isArray(value) && value.every(isToolCallLocation);
}

function isDiffHunkLine(value: unknown): value is DiffHunkLine {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    (v.kind === 'context' || v.kind === 'add' || v.kind === 'del') &&
    (v.oldLineno === null || typeof v.oldLineno === 'number') &&
    (v.newLineno === null || typeof v.newLineno === 'number') &&
    typeof v.text === 'string'
  );
}

function isDiffHunk(value: unknown): value is DiffHunk {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const lines = (value as Record<string, unknown>).lines;
  return Array.isArray(lines) && lines.every(isDiffHunkLine);
}

function isToolCallDiff(value: unknown): value is ToolCallDiff {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  if (typeof v.path !== 'string' || typeof v.newText !== 'string') {
    return false;
  }
  if (v.oldText !== null && typeof v.oldText !== 'string') {
    return false;
  }
  return v.hunks === undefined || (Array.isArray(v.hunks) && v.hunks.every(isDiffHunk));
}

function parseToolCallStarted(record: Record<string, unknown>): ToolCallStartedMessage | undefined {
  if (
    typeof record.callId === 'string' &&
    typeof record.title === 'string' &&
    typeof record.kind === 'string' &&
    isToolCallLocationArray(record.locations)
  ) {
    return record as unknown as ToolCallStartedMessage;
  }
  return undefined;
}

function parseToolCallUpdated(record: Record<string, unknown>): ToolCallUpdatedMessage | undefined {
  if (
    typeof record.callId !== 'string' ||
    typeof record.status !== 'string' ||
    (record.title !== undefined && typeof record.title !== 'string') ||
    (record.contentText !== undefined && typeof record.contentText !== 'string') ||
    (record.diff !== undefined && !isToolCallDiff(record.diff)) ||
    (record.locations !== undefined && !isToolCallLocationArray(record.locations))
  ) {
    return undefined;
  }
  return record as unknown as ToolCallUpdatedMessage;
}

function isPermissionAskOption(value: unknown): value is PermissionAskOption {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    typeof v.id === 'string' &&
    typeof v.name === 'string' &&
    typeof v.kind === 'string' &&
    (v.scope === undefined || typeof v.scope === 'string')
  );
}

function parsePermissionAsk(record: Record<string, unknown>): PermissionAskMessage | undefined {
  if (
    typeof record.requestId === 'number' &&
    typeof record.title === 'string' &&
    Array.isArray(record.options) &&
    record.options.every(isPermissionAskOption) &&
    typeof record.klorbMeta === 'object' &&
    record.klorbMeta !== null &&
    (record.originSessionId === undefined || typeof record.originSessionId === 'string')
  ) {
    return record as unknown as PermissionAskMessage;
  }
  return undefined;
}

function isQuestionAskOption(value: unknown): value is QuestionAskOption {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    typeof v.label === 'string' &&
    (v.description === undefined || typeof v.description === 'string')
  );
}

function parseQuestionAsk(record: Record<string, unknown>): QuestionAskMessage | undefined {
  if (
    typeof record.requestId === 'number' &&
    typeof record.header === 'string' &&
    typeof record.question === 'string' &&
    Array.isArray(record.options) &&
    record.options.every(isQuestionAskOption) &&
    typeof record.index === 'number' &&
    typeof record.total === 'number' &&
    (record.originSessionId === undefined || typeof record.originSessionId === 'string')
  ) {
    return record as unknown as QuestionAskMessage;
  }
  return undefined;
}

function parseToolCallLimitAsk(
  record: Record<string, unknown>
): ToolCallLimitAskMessage | undefined {
  if (typeof record.requestId === 'number' && typeof record.message === 'string') {
    return { type: 'toolCallLimitAsk', requestId: record.requestId, message: record.message };
  }
  return undefined;
}

function parseQuestionAnswer(record: Record<string, unknown>): QuestionAnswerMessage | undefined {
  if (typeof record.requestId !== 'number') {
    return undefined;
  }
  if (record.cancelled === true) {
    return { type: 'questionAnswer', requestId: record.requestId, cancelled: true };
  }
  if (typeof record.selectedOptionIndex === 'number') {
    return {
      type: 'questionAnswer',
      requestId: record.requestId,
      selectedOptionIndex: record.selectedOptionIndex,
    };
  }
  if (typeof record.otherText === 'string') {
    return { type: 'questionAnswer', requestId: record.requestId, otherText: record.otherText };
  }
  return undefined;
}

function parsePermissionDecision(
  record: Record<string, unknown>
): PermissionDecisionMessage | undefined {
  if (typeof record.requestId !== 'number') {
    return undefined;
  }
  if (record.cancelled === true) {
    return { type: 'permissionDecision', requestId: record.requestId, cancelled: true };
  }
  if (
    typeof record.optionId === 'string' &&
    (record.otherText === undefined || typeof record.otherText === 'string')
  ) {
    return {
      type: 'permissionDecision',
      requestId: record.requestId,
      optionId: record.optionId,
      ...(typeof record.otherText === 'string' ? { otherText: record.otherText } : {}),
    };
  }
  return undefined;
}

function isThinkingEffort(value: unknown): value is ThinkingEffort {
  return value === 'low' || value === 'medium' || value === 'high';
}

function parseStatusUpdate(record: Record<string, unknown>): StatusUpdateMessage | undefined {
  if (
    (record.model !== undefined && typeof record.model !== 'string') ||
    (record.thinkingEnabled !== undefined && typeof record.thinkingEnabled !== 'boolean') ||
    (record.thinkingEffort !== undefined && !isThinkingEffort(record.thinkingEffort)) ||
    (record.permissionMode !== undefined && typeof record.permissionMode !== 'string') ||
    (record.usedTokens !== undefined && typeof record.usedTokens !== 'number') ||
    (record.maxTokens !== undefined &&
      record.maxTokens !== null &&
      typeof record.maxTokens !== 'number') ||
    (record.outputTokens !== undefined && typeof record.outputTokens !== 'number') ||
    (record.sessionTitle !== undefined &&
      record.sessionTitle !== null &&
      typeof record.sessionTitle !== 'string') ||
    (record.workspaceTrusted !== undefined && typeof record.workspaceTrusted !== 'boolean') ||
    (record.enqueueMessageCapable !== undefined &&
      typeof record.enqueueMessageCapable !== 'boolean') ||
    (record.activeModelVision !== undefined && typeof record.activeModelVision !== 'boolean') ||
    (record.subagentsCapable !== undefined && typeof record.subagentsCapable !== 'boolean')
  ) {
    return undefined;
  }
  return record as unknown as StatusUpdateMessage;
}

function isSessionStatsCounts(value: unknown): value is SessionStatsCounts {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  return Object.values(value as Record<string, unknown>).every((v) => typeof v === 'number');
}

function isSessionStatsToolRow(value: unknown): value is SessionStatsToolRow {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    typeof v.name === 'string' && typeof v.succeeded === 'number' && typeof v.failed === 'number'
  );
}

function parseSessionStats(record: Record<string, unknown>): SessionStatsMessage | undefined {
  if (
    !isSessionStatsCounts(record.messageCounts) ||
    !Array.isArray(record.toolBreakdown) ||
    !record.toolBreakdown.every(isSessionStatsToolRow) ||
    !isSessionStatsCounts(record.tokenUsage) ||
    typeof record.cachePercent !== 'number' ||
    typeof record.totalCost !== 'number'
  ) {
    return undefined;
  }
  return record as unknown as SessionStatsMessage;
}

function isTaskInfo(value: unknown): value is TaskInfo {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    (v.issueId === undefined || typeof v.issueId === 'number') &&
    typeof v.text === 'string' &&
    typeof v.priority === 'string' &&
    typeof v.status === 'string' &&
    typeof v.blocked === 'boolean' &&
    typeof v.isCurrentTask === 'boolean' &&
    typeof v.closed === 'boolean'
  );
}

function isTaskListSummary(value: unknown): value is TaskListSummary {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    typeof v.openCount === 'number' &&
    typeof v.closedCount === 'number' &&
    typeof v.blockedCount === 'number' &&
    (v.currentTaskId === null || typeof v.currentTaskId === 'number')
  );
}

function parseTaskListUpdate(record: Record<string, unknown>): TaskListUpdateMessage | undefined {
  if (
    !isTaskListSummary(record.summary) ||
    !Array.isArray(record.tasks) ||
    !record.tasks.every(isTaskInfo)
  ) {
    return undefined;
  }
  return record as unknown as TaskListUpdateMessage;
}

function isSubagentState(value: unknown): value is 'running' | 'finished' | null {
  return value === 'running' || value === 'finished' || value === null;
}

function isSubagentNodeInfo(value: unknown): value is SubagentNodeInfo {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    typeof v.id === 'string' &&
    (v.parentId === null || typeof v.parentId === 'string') &&
    typeof v.address === 'string' &&
    (v.title === null || typeof v.title === 'string') &&
    typeof v.role === 'string' &&
    isSubagentState(v.state) &&
    typeof v.aborted === 'boolean' &&
    typeof v.model === 'string' &&
    typeof v.thinkingEnabled === 'boolean' &&
    isThinkingEffort(v.thinkingEffort) &&
    typeof v.usedTokens === 'number' &&
    (v.maxTokens === null || typeof v.maxTokens === 'number') &&
    typeof v.outputTokens === 'number'
  );
}

function parseSubagentTreeUpdate(
  record: Record<string, unknown>
): SubagentTreeUpdateMessage | undefined {
  if (!Array.isArray(record.nodes) || !record.nodes.every(isSubagentNodeInfo)) {
    return undefined;
  }
  return { type: 'subagentTreeUpdate', nodes: record.nodes };
}

function parseSubagentTranscriptUpdate(
  record: Record<string, unknown>
): SubagentTranscriptUpdateMessage | undefined {
  if (
    typeof record.sessionId !== 'string' ||
    !Array.isArray(record.entries) ||
    !record.entries.every(isSessionReplayEntry) ||
    (record.state !== 'running' && record.state !== 'finished') ||
    typeof record.aborted !== 'boolean' ||
    !Array.isArray(record.queuedMessages) ||
    !record.queuedMessages.every((text) => typeof text === 'string')
  ) {
    return undefined;
  }
  return {
    type: 'subagentTranscriptUpdate',
    sessionId: record.sessionId,
    entries: record.entries,
    state: record.state,
    aborted: record.aborted,
    queuedMessages: record.queuedMessages,
  };
}

/** Validates a raw `_klorb/subagentTree` ext-method result (`{nodes: [...]}`, no message
 * envelope) into `SubagentNodeInfo[]`, or `undefined` if malformed -- shared by
 * `host/features/subagents/subagentPoller.ts`, which polls this ext method directly rather than
 * receiving it as a `HostMessage`. */
export function parseSubagentTreeResult(value: unknown): SubagentNodeInfo[] | undefined {
  if (typeof value !== 'object' || value === null) {
    return undefined;
  }
  const nodes = (value as Record<string, unknown>).nodes;
  return Array.isArray(nodes) && nodes.every(isSubagentNodeInfo) ? nodes : undefined;
}

/** Validates a raw `_klorb/subagentTranscript` ext-method result (`{entries, state, aborted,
 * queuedMessages}`, no message envelope, no `sessionId` -- the poller already knows which
 * subagent it asked about), or `undefined` if malformed -- see `parseSubagentTreeResult`'s own
 * doc comment. */
export function parseSubagentTranscriptResult(value: unknown):
  | {
      entries: SessionReplayEntry[];
      state: 'running' | 'finished';
      aborted: boolean;
      queuedMessages: string[];
    }
  | undefined {
  if (typeof value !== 'object' || value === null) {
    return undefined;
  }
  const v = value as Record<string, unknown>;
  if (
    !Array.isArray(v.entries) ||
    !v.entries.every(isSessionReplayEntry) ||
    (v.state !== 'running' && v.state !== 'finished') ||
    typeof v.aborted !== 'boolean' ||
    !Array.isArray(v.queuedMessages) ||
    !v.queuedMessages.every((text) => typeof text === 'string')
  ) {
    return undefined;
  }
  return {
    entries: v.entries,
    state: v.state,
    aborted: v.aborted,
    queuedMessages: v.queuedMessages,
  };
}

function parseSetSubagentsPanelVisible(
  record: Record<string, unknown>
): SetSubagentsPanelVisibleMessage | undefined {
  if (typeof record.visible !== 'boolean') {
    return undefined;
  }
  return { type: 'setSubagentsPanelVisible', visible: record.visible };
}

function parseSelectSubagent(record: Record<string, unknown>): SelectSubagentMessage | undefined {
  if (record.sessionId !== null && typeof record.sessionId !== 'string') {
    return undefined;
  }
  return { type: 'selectSubagent', sessionId: record.sessionId };
}

function parseCancelSubagent(record: Record<string, unknown>): CancelSubagentMessage | undefined {
  if (typeof record.sessionId !== 'string') {
    return undefined;
  }
  return { type: 'cancelSubagent', sessionId: record.sessionId };
}

function parseRenameSession(record: Record<string, unknown>): RenameSessionMessage | undefined {
  if (record.title !== null && typeof record.title !== 'string') {
    return undefined;
  }
  return { type: 'renameSession', title: record.title };
}

function isAttachedImageMeta(value: unknown): value is AttachedImageMeta {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    (v.name === undefined || typeof v.name === 'string') &&
    (v.width === undefined || typeof v.width === 'number') &&
    (v.height === undefined || typeof v.height === 'number')
  );
}

function isSessionReplayEntry(value: unknown): value is SessionReplayEntry {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  if (v.kind === 'prompt' || v.kind === 'response' || v.kind === 'thinking') {
    return (
      typeof v.text === 'string' &&
      v.streaming === false &&
      (v.images === undefined || (Array.isArray(v.images) && v.images.every(isAttachedImageMeta)))
    );
  }
  if (v.kind === 'toolCall') {
    return (
      typeof v.callId === 'string' &&
      (v.status === 'completed' || v.status === 'failed') &&
      typeof v.title === 'string' &&
      typeof v.toolKind === 'string' &&
      Array.isArray(v.locations) &&
      (v.contentText === undefined ||
        v.contentText === null ||
        typeof v.contentText === 'string') &&
      typeof v.expanded === 'boolean'
    );
  }
  return false;
}

function parseSessionReplay(record: Record<string, unknown>): SessionReplayMessage | undefined {
  if (!Array.isArray(record.entries) || !record.entries.every(isSessionReplayEntry)) {
    return undefined;
  }
  return record as unknown as SessionReplayMessage;
}

function parseWorkspaceFiles(record: Record<string, unknown>): WorkspaceFilesMessage | undefined {
  if (!Array.isArray(record.files) || !record.files.every((file) => typeof file === 'string')) {
    return undefined;
  }
  return { type: 'workspaceFiles', files: record.files };
}

function parsePromptHistory(record: Record<string, unknown>): PromptHistoryMessage | undefined {
  if (
    !Array.isArray(record.entries) ||
    !record.entries.every((entry) => typeof entry === 'string')
  ) {
    return undefined;
  }
  return { type: 'promptHistory', entries: record.entries };
}

function isSkillEntry(value: unknown): value is SkillEntry {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    typeof v.namespace === 'string' &&
    typeof v.name === 'string' &&
    typeof v.description === 'string'
  );
}

function parseSkills(record: Record<string, unknown>): SkillsMessage | undefined {
  if (!Array.isArray(record.entries) || !record.entries.every(isSkillEntry)) {
    return undefined;
  }
  return { type: 'skills', entries: record.entries };
}

function parseImageAttached(record: Record<string, unknown>): ImageAttachedMessage | undefined {
  if (!isImageAttachment(record.image)) {
    return undefined;
  }
  return { type: 'imageAttached', image: record.image };
}

function parseOpenLocation(record: Record<string, unknown>): OpenLocationMessage | undefined {
  if (
    typeof record.path === 'string' &&
    (record.line === undefined || typeof record.line === 'number')
  ) {
    return record as unknown as OpenLocationMessage;
  }
  return undefined;
}

function parseOpenDiff(record: Record<string, unknown>): OpenDiffMessage | undefined {
  if (typeof record.callId === 'string' && typeof record.path === 'string') {
    return record as unknown as OpenDiffMessage;
  }
  return undefined;
}

function parseToolCallLimitDecision(
  record: Record<string, unknown>
): ToolCallLimitDecisionMessage | undefined {
  if (typeof record.requestId !== 'number') {
    return undefined;
  }
  if (record.approved === true) {
    return { type: 'toolCallLimitDecision', requestId: record.requestId, approved: true };
  }
  if (record.cancelled === true) {
    return { type: 'toolCallLimitDecision', requestId: record.requestId, cancelled: true };
  }
  return undefined;
}

function isImageAttachment(value: unknown): value is ImageAttachment {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    typeof v.mimeType === 'string' &&
    typeof v.dataBase64 === 'string' &&
    (v.name === undefined || typeof v.name === 'string') &&
    (v.width === undefined || typeof v.width === 'number') &&
    (v.height === undefined || typeof v.height === 'number')
  );
}

/** Shared validation for `SubmitPromptMessage`/`EnqueueMessageMessage`'s `text`/`images`
 * fields -- both carry the same shape, differing only in `type`. */
function parseTextWithImages(
  record: Record<string, unknown>
): { text: string; images?: ImageAttachment[] } | undefined {
  if (typeof record.text !== 'string') {
    return undefined;
  }
  if (record.images === undefined) {
    return { text: record.text };
  }
  if (!Array.isArray(record.images) || !record.images.every(isImageAttachment)) {
    return undefined;
  }
  return { text: record.text, images: record.images };
}

function parseServerLog(record: Record<string, unknown>): ServerLogMessage | undefined {
  if (typeof record.text === 'string' && typeof record.level === 'number') {
    return record as unknown as ServerLogMessage;
  }
  return undefined;
}

function parseWebviewError(record: Record<string, unknown>): WebviewErrorMessage | undefined {
  if (
    typeof record.message !== 'string' ||
    (record.stack !== undefined && typeof record.stack !== 'string')
  ) {
    return undefined;
  }
  return record as unknown as WebviewErrorMessage;
}

/** Narrows an untyped `postMessage` payload to a `HostMessage`, or `undefined` if it isn't one.
 * `toolCallStarted`/`toolCallUpdated` carry nested arrays/objects the `FieldSpec` mechanism
 * above can't express, so they're validated by dedicated guards instead. */
export function parseHostMessage(data: unknown): HostMessage | undefined {
  const simple = parseMessage(data, HOST_FIELD_SPECS, HOST_BARE_TYPES);
  if (simple !== undefined) {
    return simple as unknown as HostMessage;
  }
  if (typeof data !== 'object' || data === null) {
    return undefined;
  }
  const record = data as Record<string, unknown>;
  switch (record.type) {
    case 'toolCallStarted':
      return parseToolCallStarted(record);
    case 'toolCallUpdated':
      return parseToolCallUpdated(record);
    case 'permissionAsk':
      return parsePermissionAsk(record);
    case 'questionAsk':
      return parseQuestionAsk(record);
    case 'toolCallLimitAsk':
      return parseToolCallLimitAsk(record);
    case 'serverLog':
      return parseServerLog(record);
    case 'statusUpdate':
      return parseStatusUpdate(record);
    case 'sessionStats':
      return parseSessionStats(record);
    case 'taskListUpdate':
      return parseTaskListUpdate(record);
    case 'subagentTreeUpdate':
      return parseSubagentTreeUpdate(record);
    case 'subagentTranscriptUpdate':
      return parseSubagentTranscriptUpdate(record);
    case 'sessionReplay':
      return parseSessionReplay(record);
    case 'workspaceFiles':
      return parseWorkspaceFiles(record);
    case 'promptHistory':
      return parsePromptHistory(record);
    case 'skills':
      return parseSkills(record);
    case 'imageAttached':
      return parseImageAttached(record);
    default:
      return undefined;
  }
}

/** Narrows an untyped `postMessage` payload to a `WebviewMessage`, or `undefined` if it isn't
 * one. */
export function parseWebviewMessage(data: unknown): WebviewMessage | undefined {
  const simple = parseMessage(data, [], WEBVIEW_BARE_TYPES);
  if (simple !== undefined) {
    return simple as unknown as WebviewMessage;
  }
  if (typeof data !== 'object' || data === null) {
    return undefined;
  }
  const record = data as Record<string, unknown>;
  switch (record.type) {
    case 'submitPrompt': {
      const parsed = parseTextWithImages(record);
      if (parsed === undefined) {
        return undefined;
      }
      if (record.subagentId !== undefined && typeof record.subagentId !== 'string') {
        return undefined;
      }
      return {
        type: 'submitPrompt',
        ...parsed,
        ...(record.subagentId !== undefined ? { subagentId: record.subagentId as string } : {}),
      };
    }
    case 'enqueueMessage': {
      const parsed = parseTextWithImages(record);
      return parsed === undefined ? undefined : { type: 'enqueueMessage', ...parsed };
    }
    case 'openLocation':
      return parseOpenLocation(record);
    case 'openDiff':
      return parseOpenDiff(record);
    case 'permissionDecision':
      return parsePermissionDecision(record);
    case 'questionAnswer':
      return parseQuestionAnswer(record);
    case 'toolCallLimitDecision':
      return parseToolCallLimitDecision(record);
    case 'setSubagentsPanelVisible':
      return parseSetSubagentsPanelVisible(record);
    case 'selectSubagent':
      return parseSelectSubagent(record);
    case 'cancelSubagent':
      return parseCancelSubagent(record);
    case 'renameSession':
      return parseRenameSession(record);
    case 'webviewError':
      return parseWebviewError(record);
    default:
      return undefined;
  }
}
