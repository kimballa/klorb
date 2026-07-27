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

/** The conversation was reset (a fresh session replaced the old one); clear the history. */
export interface SessionResetMessage {
  type: 'sessionReset';
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

/** A tool call was just started (ACP `tool_call` update): `kind` and `status` are left as
 * plain strings (mirroring `TurnEndedMessage.stopReason`) rather than a literal union, so an
 * ACP `ToolKind`/`ToolCallStatus` value this client doesn't recognize yet still round-trips
 * instead of failing to parse. */
export interface ToolCallStartedMessage {
  type: 'toolCallStarted';
  callId: string;
  title: string;
  kind: string;
  locations: ToolCallLocation[];
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
 * `itemTotal`/`grantPatterns`/`riskLevel`, or for an escalation ask `escalation`. */
export interface PermissionAskMessage {
  type: 'permissionAsk';
  requestId: number;
  title: string;
  options: PermissionAskOption[];
  klorbMeta: Record<string, unknown>;
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
 * extension-method registry). */
export interface QuestionAskMessage {
  type: 'questionAsk';
  requestId: number;
  header: string;
  question: string;
  options: QuestionAskOption[];
  index: number;
  total: number;
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

/** Every message the extension host may post to the webview. */
export type HostMessage =
  | TurnStartedMessage
  | AgentChunkMessage
  | ThoughtChunkMessage
  | TurnEndedMessage
  | TurnErrorMessage
  | SessionResetMessage
  | ToolCallStartedMessage
  | ToolCallUpdatedMessage
  | PermissionAskMessage
  | QuestionAskMessage
  | StatusUpdateMessage
  | SessionStatsMessage
  | TaskListUpdateMessage
  | ToggleTaskPanelMessage;

/** The user submitted a prompt from the input box. */
export interface SubmitPromptMessage {
  type: 'submitPrompt';
  text: string;
}

/** The user asked to cancel the in-flight turn (Stop button or Escape). */
export interface CancelTurnMessage {
  type: 'cancelTurn';
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
  | CancelTurnMessage
  | OpenLocationMessage
  | OpenDiffMessage
  | PermissionDecisionMessage
  | QuestionAnswerMessage
  | PickModelMessage
  | PickThinkingMessage
  | CyclePermissionModeMessage
  | SetPermissionModeMessage
  | ShowSessionStatsMessage
  | NewSessionMessage
  | ReloadSkillsMessage
  | WebviewErrorMessage;

/** Message `type` values that carry a required string field, keyed by the field's name. */
interface FieldSpec {
  field: 'text' | 'message' | 'stopReason';
  types: readonly string[];
}

const HOST_FIELD_SPECS: readonly FieldSpec[] = [
  { field: 'text', types: ['agentChunk', 'thoughtChunk'] },
  { field: 'stopReason', types: ['turnEnded'] },
  { field: 'message', types: ['turnError'] },
];

const HOST_BARE_TYPES: readonly string[] = ['turnStarted', 'sessionReset', 'toggleTaskPanel'];

const WEBVIEW_FIELD_SPECS: readonly FieldSpec[] = [{ field: 'text', types: ['submitPrompt'] }];

const WEBVIEW_BARE_TYPES: readonly string[] = [
  'cancelTurn',
  'pickModel',
  'pickThinking',
  'cyclePermissionMode',
  'setPermissionMode',
  'showSessionStats',
  'newSession',
  'reloadSkills',
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
    record.klorbMeta !== null
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
    typeof record.total === 'number'
  ) {
    return record as unknown as QuestionAskMessage;
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
    (record.workspaceTrusted !== undefined && typeof record.workspaceTrusted !== 'boolean')
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
    case 'statusUpdate':
      return parseStatusUpdate(record);
    case 'sessionStats':
      return parseSessionStats(record);
    case 'taskListUpdate':
      return parseTaskListUpdate(record);
    default:
      return undefined;
  }
}

/** Narrows an untyped `postMessage` payload to a `WebviewMessage`, or `undefined` if it isn't
 * one. */
export function parseWebviewMessage(data: unknown): WebviewMessage | undefined {
  const simple = parseMessage(data, WEBVIEW_FIELD_SPECS, WEBVIEW_BARE_TYPES);
  if (simple !== undefined) {
    return simple as unknown as WebviewMessage;
  }
  if (typeof data !== 'object' || data === null) {
    return undefined;
  }
  const record = data as Record<string, unknown>;
  switch (record.type) {
    case 'openLocation':
      return parseOpenLocation(record);
    case 'openDiff':
      return parseOpenDiff(record);
    case 'permissionDecision':
      return parsePermissionDecision(record);
    case 'questionAnswer':
      return parseQuestionAnswer(record);
    case 'webviewError':
      return parseWebviewError(record);
    default:
      return undefined;
  }
}
