// © Copyright 2026 Aaron Kimball
import { ERROR_LEVEL_VALUE } from 'shared/logLevels';
import type {
  AttachedImageMeta,
  BashToolMeta,
  HostMessage,
  ImageAttachment,
  PermissionAskMessage,
  QuestionAskMessage,
  ReadFileMeta,
  SessionReplayEntry,
  SessionStatsCounts,
  SessionStatsToolRow,
  TaskListUpdateMessage,
  ToolCallDiff,
  ToolCallLimitAskMessage,
  ToolCallLocation,
  ToolCallStartedMessage,
  ToolCallUpdatedMessage,
} from 'shared/webviewMessages';

/** The single interaction the server may be waiting on an answer for -- a permission ask, a
 * `_klorb/askUserQuestions` question, or a `_klorb/raiseToolCallLimit` ask, tracked in one slot
 * since the server never has more than one blocking ask outstanding at a time
 * (see `applyPendingInteraction`). */
export type PendingInteraction =
  PermissionAskMessage | QuestionAskMessage | ToolCallLimitAskMessage;

/** What kind of content a plain-text history entry holds. `queuedMessage` is a submitted-while-
 * a-turn-was-already-running prompt, rendered in italic "Queued message" styling until its
 * matching `queuedMessageSent` message flips it to `'prompt'` (see `appendQueuedMessage`/
 * `applyQueuedMessageSent`). `serverError` is a `turnError`-like entry for a lost server
 * process, rendered with an additional "Restart Server" action. */
export type HistoryEntryKind =
  | 'prompt'
  | 'response'
  | 'thinking'
  | 'error'
  | 'notice'
  | 'interaction'
  | 'queuedMessage'
  | 'serverError';

/** One plain-text entry in the panel's history scroll. `streaming` marks an entry still
 * receiving chunks; the next chunk of the same kind extends it instead of appending a new
 * entry. */
export interface TextHistoryEntry {
  kind: HistoryEntryKind;
  /** Stable identity for this entry, generated once at construction time -- used as the
   * windowed history list's React key so it survives the trailing entry's own chunk updates. */
  id: string;
  text: string;
  streaming: boolean;
  /** Images attached to a `'prompt'` entry -- a live-submitted entry carries the full
   * `ImageAttachment` (bytes and all, verbatim from the `SubmitPromptMessage` that created
   * it); a `_klorb/sessionReplay`-restored entry carries `AttachedImageMeta` only (no bytes --
   * the server doesn't resend an already-persisted image just to redraw a thumbnail), which
   * `AttachmentThumbnail` renders as a paper-clip placeholder instead of the real image. Never
   * set for any other `kind`. */
  images?: (ImageAttachment | AttachedImageMeta)[];
}

/** A tool-call chip entry: `expanded` defaults to `false` and can be flipped individually
 * (its own chevron). */
export interface ToolCallHistoryEntry {
  kind: 'toolCall';
  /** Stable identity for this entry -- `callId` itself, since it's already a unique, meaningful
   * id rather than an arbitrary one. */
  id: string;
  callId: string;
  status: 'in_progress' | 'completed' | 'failed';
  title: string;
  toolKind: string;
  locations: ToolCallLocation[];
  contentText?: string;
  diff?: ToolCallDiff;
  bashMeta?: BashToolMeta;
  readFileMeta?: ReadFileMeta;
  expanded: boolean;
}

/** A "Show Session Stats" result entry -- rendered as a `SessionStatsCard` (two right-aligned
 * numeric tables plus a separated cost line), mirroring the TUI's own stats notice. See
 * `shared/webviewMessages.ts`'s `SessionStatsMessage` for field semantics. */
export interface SessionStatsHistoryEntry {
  kind: 'sessionStats';
  id: string;
  messageCounts: SessionStatsCounts;
  toolBreakdown: SessionStatsToolRow[];
  tokenUsage: SessionStatsCounts;
  cachePercent: number;
  totalCost: number;
}

/** One entry in the panel's history scroll. */
export type HistoryEntry = TextHistoryEntry | ToolCallHistoryEntry | SessionStatsHistoryEntry;

/** Generates a client-side-only id for a new `HistoryEntry` -- unique enough to key the
 * windowed history list's React elements, never round-tripped to the server. */
export function makeEntryId(): string {
  return crypto.randomUUID();
}

/** Backfills an `id` onto an entry restored from a `vscode.getState()` snapshot saved before
 * `HistoryEntry` grew this field -- returns `entry` unchanged if it already carries one. */
export function ensureEntryId(entry: HistoryEntry): HistoryEntry {
  return typeof entry.id === 'string' && entry.id.length > 0
    ? entry
    : { ...entry, id: makeEntryId() };
}

/** Appends the user's submitted prompt as a finished (non-streaming) entry, with any images
 * attached to it (see `PromptInput`'s attachment tray). */
export function appendPrompt(
  entries: readonly HistoryEntry[],
  text: string,
  images?: ImageAttachment[]
): HistoryEntry[] {
  return [
    ...entries,
    { kind: 'prompt', text, streaming: false, id: makeEntryId(), ...(images ? { images } : {}) },
  ];
}

/** Appends a message the server just confirmed queuing into the running turn
 * (`messageQueued`) -- rendered in italic "Queued message" styling until `applyQueuedMessageSent`
 * flips it to a regular prompt, mirroring the TUI's `_queue_prompt`/`handle_enqueue_message`. */
export function appendQueuedMessage(
  entries: readonly HistoryEntry[],
  text: string
): HistoryEntry[] {
  return [...entries, { kind: 'queuedMessage', text, streaming: false, id: makeEntryId() }];
}

/** Flips the oldest still-`queuedMessage` entry whose text matches `text` to a regular
 * `'prompt'` entry, confirming delivery (`queuedMessageSent`) -- mirrors the TUI's
 * `handle_send_queued_message`, which removes the italic block once its message is drained.
 * Correlation is by order + text (the first matching entry wins), matching the server's own
 * single-queue reality (see `QueuedMessageSentMessage`'s doc comment) -- a no-op if no
 * `queuedMessage` entry matches, e.g. a stale/duplicate notification. */
export function applyQueuedMessageSent(
  entries: readonly HistoryEntry[],
  text: string
): HistoryEntry[] {
  const index = entries.findIndex((entry) => entry.kind === 'queuedMessage' && entry.text === text);
  const entry = index === -1 ? undefined : entries[index];
  if (entry === undefined || entry.kind !== 'queuedMessage') {
    return [...entries];
  }
  const updated: HistoryEntry = { ...entry, kind: 'prompt' };
  return [...entries.slice(0, index), updated, ...entries.slice(index + 1)];
}

/** Mirrors the TUI's `_handle_aborted_response` decision table for a cancelled turn: appends an
 * "(interrupted)" marker onto whichever entry (response or thinking) was still streaming when
 * the turn was cancelled, or a standalone notice if neither was -- e.g. the turn was cancelled
 * between tool-call rounds, before the next round's text had started streaming. */
export function applyInterruptedMarker(entries: readonly HistoryEntry[]): HistoryEntry[] {
  const last = entries[entries.length - 1];
  if (last !== undefined && last.kind === 'response' && last.streaming) {
    const updated: HistoryEntry = {
      ...last,
      text: `${last.text}\n\n*(interrupted)*`,
      streaming: false,
    };
    return [...entries.slice(0, -1), updated];
  }
  if (last !== undefined && last.kind === 'thinking' && last.streaming) {
    const updated: HistoryEntry = {
      ...last,
      text: `${last.text}\n\n(interrupted)`,
      streaming: false,
    };
    return [...entries.slice(0, -1), updated];
  }
  return [
    ...finishStreaming(entries),
    { kind: 'notice', text: '(interrupted)', streaming: false, id: makeEntryId() },
  ];
}

function metaString(klorbMeta: Record<string, unknown>, key: string): string | undefined {
  const value = klorbMeta[key];
  return typeof value === 'string' ? value : undefined;
}

/** Appends a compact permanent record of an answered permission ask -- the TUI's
 * `_record_interaction_history` equivalent (see docs/specs/vscode-plugin.md's approval panel
 * section): the ask's header, description/command, and the option name the user chose. */
export function appendInteraction(
  entries: readonly HistoryEntry[],
  ask: PermissionAskMessage,
  decisionName: string
): HistoryEntry[] {
  const isEscalation =
    typeof ask.klorbMeta.escalation === 'object' && ask.klorbMeta.escalation !== null;
  const headerKind = metaString(ask.klorbMeta, 'headerKind');
  const lines = [
    isEscalation ? 'Privilege escalation' : `Permission requested: ${headerKind ?? 'access'}`,
  ];
  const resourceDescription = metaString(ask.klorbMeta, 'resourceDescription');
  if (resourceDescription !== undefined) {
    lines.push(resourceDescription);
  }
  const commandText =
    metaString(ask.klorbMeta, 'itemCommandText') ?? metaString(ask.klorbMeta, 'commandText');
  if (commandText !== undefined) {
    lines.push(commandText);
  }
  lines.push(`Decision: ${decisionName}`);
  return [
    ...entries,
    { kind: 'interaction', text: lines.join('\n'), streaming: false, id: makeEntryId() },
  ];
}

/** Appends a compact permanent record of an answered `questionAsk` -- the `appendInteraction()`
 * equivalent for a `_klorb/askUserQuestions` question, mirroring the TUI's own
 * `_record_interaction_history` call for `AskUserQuestionsPanel`. */
export function appendQuestionInteraction(
  entries: readonly HistoryEntry[],
  ask: QuestionAskMessage,
  answerText: string
): HistoryEntry[] {
  const lines = [
    `Question ${ask.index + 1} of ${ask.total} · ${ask.header}`,
    ask.question,
    `Answer: ${answerText}`,
  ];
  return [
    ...entries,
    { kind: 'interaction', text: lines.join('\n'), streaming: false, id: makeEntryId() },
  ];
}

/** Appends a compact permanent record of an answered `toolCallLimitAsk` -- the
 * `appendInteraction()` equivalent for a `_klorb/raiseToolCallLimit` ask, recording the
 * server's limit-reached message and the user's decision. */
export function appendToolCallLimitInteraction(
  entries: readonly HistoryEntry[],
  ask: ToolCallLimitAskMessage,
  answerText: string
): HistoryEntry[] {
  const lines = ['Tool call limit reached', ask.message, `Decision: ${answerText}`];
  return [
    ...entries,
    { kind: 'interaction', text: lines.join('\n'), streaming: false, id: makeEntryId() },
  ];
}

function appendChunk(
  entries: readonly HistoryEntry[],
  kind: 'response' | 'thinking',
  text: string
): HistoryEntry[] {
  const last = entries[entries.length - 1];
  if (last !== undefined && last.kind === kind && last.streaming) {
    return [...entries.slice(0, -1), { ...last, text: last.text + text }];
  }
  return [...entries, { kind, text, streaming: true, id: makeEntryId() }];
}

function finishStreaming(entries: readonly HistoryEntry[]): HistoryEntry[] {
  return entries.map((entry) =>
    // `entry` is typed as `HistoryEntry` (always an object) at compile time, but `entries`
    // ultimately traces back to `vscode.getState()` (see `isHistoryEntry()` below) -- a boundary
    // this codebase can't fully police at compile time -- so a non-object slipping through
    // (the `in` operator throws on one) is guarded against defensively here too.
    typeof entry === 'object' && entry !== null && 'streaming' in entry && entry.streaming
      ? { ...entry, streaming: false }
      : entry
  );
}

/** Every `HistoryEntry.kind` value, across all three subtypes -- the set `isHistoryEntry()`
 * checks a candidate's own `kind` against. */
const HISTORY_ENTRY_KINDS: ReadonlySet<string> = new Set([
  'prompt',
  'response',
  'thinking',
  'error',
  'notice',
  'interaction',
  'queuedMessage',
  'serverError',
  'toolCall',
  'sessionStats',
]);

/** Quickly check that `value` at least *looks* like a `HistoryEntry` (a non-null
 * object with a recognized `kind`) -- used to sanitize `vscode.getState()`'s persisted
 * `entries` before trusting them as `initialEntries` (see `main.tsx`).
 */
export function isHistoryEntry(value: unknown): value is HistoryEntry {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { kind?: unknown }).kind === 'string' &&
    HISTORY_ENTRY_KINDS.has((value as { kind: string }).kind)
  );
}

function appendToolCallStarted(
  entries: readonly HistoryEntry[],
  message: ToolCallStartedMessage
): HistoryEntry[] {
  const entry: ToolCallHistoryEntry = {
    kind: 'toolCall',
    id: message.callId,
    callId: message.callId,
    status: 'in_progress',
    title: message.title,
    toolKind: message.kind,
    locations: message.locations,
    bashMeta: message.bashMeta,
    expanded: false,
  };
  return [...entries, entry];
}

/** Mutates the matching `toolCall` entry in place, or appends a new one if `callId` names no
 * entry yet -- the fallback for a call that never got a `toolCallStarted` (e.g. a
 * malformed-arguments call that failed before `on_tool_call_started` could fire). */
function applyToolCallUpdated(
  entries: readonly HistoryEntry[],
  message: ToolCallUpdatedMessage
): HistoryEntry[] {
  const index = entries.findIndex(
    (entry) => entry.kind === 'toolCall' && entry.callId === message.callId
  );
  const status = message.status === 'failed' ? 'failed' : 'completed';
  if (index === -1) {
    const entry: ToolCallHistoryEntry = {
      kind: 'toolCall',
      id: message.callId,
      callId: message.callId,
      status,
      title: message.title ?? 'Tool call',
      toolKind: 'other',
      locations: message.locations ?? [],
      contentText: message.contentText,
      diff: message.diff,
      bashMeta: message.bashMeta,
      readFileMeta: message.readFileMeta,
      expanded: false,
    };
    return [...entries, entry];
  }
  const previous = entries[index] as ToolCallHistoryEntry;
  const updated: ToolCallHistoryEntry = {
    ...previous,
    status,
    title: message.title ?? previous.title,
    locations: message.locations ?? previous.locations,
    contentText: message.contentText ?? previous.contentText,
    diff: message.diff ?? previous.diff,
    bashMeta: message.bashMeta ?? previous.bashMeta,
    readFileMeta: message.readFileMeta ?? previous.readFileMeta,
  };
  return [...entries.slice(0, index), updated, ...entries.slice(index + 1)];
}

/**
 * Applies one host→webview message to the history entry list, returning the new list (the
 * input is never mutated). Streamed chunks extend the trailing streaming entry of the same
 * kind or start a new one — so a response arriving after thinking (or vice versa) starts its
 * own entry, and interleaved phases stay in order.
 */
export function applyHostMessage(entries: HistoryEntry[], message: HostMessage): HistoryEntry[] {
  switch (message.type) {
    case 'turnStarted':
      return [...entries];
    case 'agentChunk':
      return appendChunk(entries, 'response', message.text);
    case 'thoughtChunk':
      return appendChunk(entries, 'thinking', message.text);
    case 'turnEnded': {
      if (message.stopReason === 'end_turn') {
        return finishStreaming(entries);
      }
      if (message.stopReason === 'cancelled') {
        return applyInterruptedMarker(entries);
      }
      return [
        ...finishStreaming(entries),
        {
          kind: 'notice',
          text: `Turn ended: ${message.stopReason}`,
          streaming: false,
          id: makeEntryId(),
        },
      ];
    }
    case 'turnError':
      return [
        ...finishStreaming(entries),
        { kind: 'error', text: message.message, streaming: false, id: makeEntryId() },
      ];
    case 'serverLost':
      return [
        ...finishStreaming(entries),
        { kind: 'serverError', text: message.message, streaming: false, id: makeEntryId() },
      ];
    case 'messageQueued':
      return appendQueuedMessage(entries, message.text);
    case 'queuedMessageSent':
      return applyQueuedMessageSent(entries, message.text);
    case 'notice':
      return [
        ...entries,
        { kind: 'notice', text: message.text, streaming: false, id: makeEntryId() },
      ];
    case 'serverLog':
      return [
        ...entries,
        {
          kind: message.level >= ERROR_LEVEL_VALUE ? 'error' : 'notice',
          text: message.text,
          streaming: false,
          id: makeEntryId(),
        },
      ];
    case 'sessionReset':
      return [];
    case 'toolCallStarted':
      return appendToolCallStarted(entries, message);
    case 'toolCallUpdated':
      return applyToolCallUpdated(entries, message);
    case 'permissionAsk':
    case 'questionAsk':
    case 'toolCallLimitAsk':
      // Tracked separately by `applyPendingInteraction`, not as a history entry -- the
      // ApprovalPanel/QuestionPanel/ToolCallLimitPanel mounts from that state instead, and an
      // `appendInteraction()`/`appendQuestionInteraction()`/`appendToolCallLimitInteraction()`
      // record lands here only once the ask is answered.
      return entries;
    case 'statusUpdate':
      // Tracked separately by `App`'s own `status` state, not as a history entry -- the
      // StatusRow renders from that state instead.
      return entries;
    case 'sessionStats':
      return [
        ...entries,
        {
          kind: 'sessionStats',
          id: makeEntryId(),
          messageCounts: message.messageCounts,
          toolBreakdown: message.toolBreakdown,
          tokenUsage: message.tokenUsage,
          cachePercent: message.cachePercent,
          totalCost: message.totalCost,
        },
      ];
    case 'taskListUpdate':
    case 'toggleTaskPanel':
      // Tracked separately by `App`'s own `taskList`/`taskPanelVisible` state, not as a
      // history entry -- the TaskPanel renders from that state instead.
      return entries;
    case 'subagentTreeUpdate':
    case 'subagentTranscriptUpdate':
    case 'toggleSubagentsPanel':
      // Tracked separately by `App`'s own `subagentNodes`/`subagentTranscript`/
      // `subagentsPanelVisible` state, not as a history entry -- the subagents panel/transcript
      // view render from that state instead (see `webview/features/subagents`).
      return entries;
    case 'sessionReplay':
      return applySessionReplay(message.entries);
    case 'workspaceFiles':
    case 'promptHistory':
    case 'skills':
    case 'imageAttached':
      // Tracked separately by `App`'s own `workspaceFiles`/`promptHistory` state, not as a
      // history entry -- the file finder and prompt input read from that state instead.
      return entries;
  }
}

/** Converts one wire-format `SessionReplayEntry` (`shared/webviewMessages.ts`) into this
 * feature's own `HistoryEntry`, assigning it a stable `id` -- `callId` for a tool call, a fresh
 * one otherwise, since a replay entry doesn't carry one over the wire. */
function sessionReplayEntryToHistoryEntry(entry: SessionReplayEntry): HistoryEntry {
  if (entry.kind === 'toolCall') {
    return { ...entry, id: entry.callId, contentText: entry.contentText ?? undefined };
  }
  return { ...entry, id: makeEntryId() };
}

/** Replaces the history wholesale with a previously saved session's conversation
 * (`sessionReplay`) -- sent once after a successful `session/load`. Applied over whatever
 * cached history the webview's own `sessionState` persistence already restored
 * (stale-while-revalidate), so a resume always ends up showing the server's own record of the
 * conversation rather than a possibly-stale local cache. */
export function applySessionReplay(entries: readonly SessionReplayEntry[]): HistoryEntry[] {
  return entries.map(sessionReplayEntryToHistoryEntry);
}

/** The task panel's data, without the message envelope's `type` discriminant -- see
 * `shared/webviewMessages.ts`'s `TaskListUpdateMessage` for field semantics. */
export type TaskListSnapshot = Omit<TaskListUpdateMessage, 'type'>;

/**
 * Tracks the task panel's snapshot, from the same message stream: `taskListUpdate` replaces it
 * wholesale (the server always sends every task on each update, never a delta -- see
 * `TaskListUpdateMessage`'s own doc comment), `sessionReset` clears it (a fresh session may not
 * get an initial plan snapshot at all -- see docs/specs/klorb-server.md's "Chainlink task-plan
 * updates" section -- so a stale prior session's tasks must not linger), every other message
 * leaves it unchanged.
 */
export function applyTaskListUpdate(
  taskList: TaskListSnapshot | undefined,
  message: HostMessage
): TaskListSnapshot | undefined {
  switch (message.type) {
    case 'taskListUpdate':
      return { summary: message.summary, tasks: message.tasks };
    case 'sessionReset':
      return undefined;
    default:
      return taskList;
  }
}

/**
 * Tracks the interaction (a permission ask or a question ask) currently awaiting an answer,
 * from the same message stream: a `permissionAsk`/`questionAsk` sets it (replacing any prior
 * one, which the server never sends concurrently), `sessionReset` clears it, every other
 * message leaves it unchanged. Kept in the model (not component state) so `vscode.setState`
 * persistence keeps an unanswered interaction visible across a webview hide/show (see
 * docs/specs/vscode-plugin.md's approval panel section) -- resolving a decision/answer clears it
 * via the caller's own state update, not through this reducer.
 */
export function applyPendingInteraction(
  pendingInteraction: PendingInteraction | undefined,
  message: HostMessage
): PendingInteraction | undefined {
  switch (message.type) {
    case 'permissionAsk':
    case 'questionAsk':
    case 'toolCallLimitAsk':
      return message;
    case 'sessionReset':
      return undefined;
    default:
      return pendingInteraction;
  }
}

/** Flips one `toolCall` entry's `expanded` flag (its own chevron), leaving every other entry
 * -- including every other tool call -- untouched. */
export function applyToolCallExpandedToggle(
  entries: readonly HistoryEntry[],
  callId: string
): HistoryEntry[] {
  return entries.map((entry) =>
    entry.kind === 'toolCall' && entry.callId === callId
      ? { ...entry, expanded: !entry.expanded }
      : entry
  );
}

/**
 * Tracks whether a turn is in flight, from the same message stream: `turnStarted` raises the
 * flag; `turnEnded`/`turnError`/`serverLost`/`sessionReset` clear it; other messages leave it
 * unchanged.
 */
export function applyTurnFlag(inFlight: boolean, message: HostMessage): boolean {
  switch (message.type) {
    case 'turnStarted':
      return true;
    case 'turnEnded':
    case 'turnError':
    case 'serverLost':
    case 'sessionReset':
      return false;
    default:
      return inFlight;
  }
}
