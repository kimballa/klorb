// © Copyright 2026 Aaron Kimball
import type {
  HostMessage,
  PermissionAskMessage,
  QuestionAskMessage,
  SessionStatsCounts,
  SessionStatsToolRow,
  TaskListUpdateMessage,
  ToolCallDiff,
  ToolCallLocation,
  ToolCallStartedMessage,
  ToolCallUpdatedMessage,
} from 'shared/webviewMessages';

/** The single interaction the server may be waiting on an answer for -- a permission ask or a
 * `_klorb/askUserQuestions` question, tracked in one slot since the server never has more than
 * one blocking ask outstanding at a time (see `applyPendingInteraction`). */
export type PendingInteraction = PermissionAskMessage | QuestionAskMessage;

/** What kind of content a plain-text history entry holds. */
export type HistoryEntryKind =
  'prompt' | 'response' | 'thinking' | 'error' | 'notice' | 'interaction';

/** One plain-text entry in the panel's history scroll. `streaming` marks an entry still
 * receiving chunks; the next chunk of the same kind extends it instead of appending a new
 * entry. */
export interface TextHistoryEntry {
  kind: HistoryEntryKind;
  text: string;
  streaming: boolean;
}

/** A tool-call chip entry: `expanded` starts at whatever the global "expand all tool calls"
 * mode was when the call started, and can then also be flipped individually (its own chevron)
 * or in bulk (the global toggle re-applies to every tool-call entry at once) -- mirrors the
 * TUI's Ctrl+O, which flips every `ToolCallStatic` in the history at once (see
 * `klorb.tui.mixins.key_actions.action_toggle_tool_call_detail`). */
export interface ToolCallHistoryEntry {
  kind: 'toolCall';
  callId: string;
  status: 'in_progress' | 'completed' | 'failed';
  title: string;
  toolKind: string;
  locations: ToolCallLocation[];
  contentText?: string;
  diff?: ToolCallDiff;
  expanded: boolean;
}

/** A "Show Session Stats" result entry -- rendered as a `SessionStatsCard` (two right-aligned
 * numeric tables plus a separated cost line), mirroring the TUI's own stats notice. See
 * `shared/webviewMessages.ts`'s `SessionStatsMessage` for field semantics. */
export interface SessionStatsHistoryEntry {
  kind: 'sessionStats';
  messageCounts: SessionStatsCounts;
  toolBreakdown: SessionStatsToolRow[];
  tokenUsage: SessionStatsCounts;
  cachePercent: number;
  totalCost: number;
}

/** One entry in the panel's history scroll. */
export type HistoryEntry = TextHistoryEntry | ToolCallHistoryEntry | SessionStatsHistoryEntry;

/** Appends the user's submitted prompt as a finished (non-streaming) entry. */
export function appendPrompt(entries: readonly HistoryEntry[], text: string): HistoryEntry[] {
  return [...entries, { kind: 'prompt', text, streaming: false }];
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
  return [...entries, { kind: 'interaction', text: lines.join('\n'), streaming: false }];
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
  return [...entries, { kind: 'interaction', text: lines.join('\n'), streaming: false }];
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
  return [...entries, { kind, text, streaming: true }];
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
  message: ToolCallStartedMessage,
  expandAllToolCalls: boolean
): HistoryEntry[] {
  const entry: ToolCallHistoryEntry = {
    kind: 'toolCall',
    callId: message.callId,
    status: 'in_progress',
    title: message.title,
    toolKind: message.kind,
    locations: message.locations,
    expanded: expandAllToolCalls,
  };
  return [...entries, entry];
}

/** Mutates the matching `toolCall` entry in place, or appends a new one if `callId` names no
 * entry yet -- the fallback for a call that never got a `toolCallStarted` (e.g. a
 * malformed-arguments call that failed before `on_tool_call_started` could fire). */
function applyToolCallUpdated(
  entries: readonly HistoryEntry[],
  message: ToolCallUpdatedMessage,
  expandAllToolCalls: boolean
): HistoryEntry[] {
  const index = entries.findIndex(
    (entry) => entry.kind === 'toolCall' && entry.callId === message.callId
  );
  const status = message.status === 'failed' ? 'failed' : 'completed';
  if (index === -1) {
    const entry: ToolCallHistoryEntry = {
      kind: 'toolCall',
      callId: message.callId,
      status,
      title: message.title ?? 'Tool call',
      toolKind: 'other',
      locations: message.locations ?? [],
      contentText: message.contentText,
      diff: message.diff,
      expanded: expandAllToolCalls,
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
  };
  return [...entries.slice(0, index), updated, ...entries.slice(index + 1)];
}

/**
 * Applies one host→webview message to the history entry list, returning the new list (the
 * input is never mutated). Streamed chunks extend the trailing streaming entry of the same
 * kind or start a new one — so a response arriving after thinking (or vice versa) starts its
 * own entry, and interleaved phases stay in order. `expandAllToolCalls` seeds a newly-started
 * tool-call entry's initial expanded state (see `ToolCallHistoryEntry`); it's ignored by every
 * other message.
 */
export function applyHostMessage(
  entries: readonly HistoryEntry[],
  message: HostMessage,
  expandAllToolCalls = false
): HistoryEntry[] {
  switch (message.type) {
    case 'turnStarted':
      return [...entries];
    case 'agentChunk':
      return appendChunk(entries, 'response', message.text);
    case 'thoughtChunk':
      return appendChunk(entries, 'thinking', message.text);
    case 'turnEnded': {
      const finished = finishStreaming(entries);
      if (message.stopReason === 'end_turn') {
        return finished;
      }
      return [
        ...finished,
        { kind: 'notice', text: `Turn ended: ${message.stopReason}`, streaming: false },
      ];
    }
    case 'turnError':
      return [
        ...finishStreaming(entries),
        { kind: 'error', text: message.message, streaming: false },
      ];
    case 'sessionReset':
      return [];
    case 'toolCallStarted':
      return appendToolCallStarted(entries, message, expandAllToolCalls);
    case 'toolCallUpdated':
      return applyToolCallUpdated(entries, message, expandAllToolCalls);
    case 'permissionAsk':
    case 'questionAsk':
      // Tracked separately by `applyPendingInteraction`, not as a history entry -- the
      // ApprovalPanel/QuestionPanel mounts from that state instead, and an `appendInteraction()`/
      // `appendQuestionInteraction()` record lands here only once the ask is answered.
      return [...entries];
    case 'statusUpdate':
      // Tracked separately by `App`'s own `status` state, not as a history entry -- the
      // StatusRow renders from that state instead.
      return [...entries];
    case 'sessionStats':
      return [
        ...entries,
        {
          kind: 'sessionStats',
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
      return [...entries];
  }
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
      return message;
    case 'sessionReset':
      return undefined;
    default:
      return pendingInteraction;
  }
}

/** Flips every `toolCall` entry's `expanded` flag to `expand` at once -- the global "expand
 * all tool calls" toggle, mirroring the TUI's Ctrl+O (see `ToolCallHistoryEntry`). */
export function applyExpandAllToolCalls(
  entries: readonly HistoryEntry[],
  expand: boolean
): HistoryEntry[] {
  return entries.map((entry) =>
    entry.kind === 'toolCall' ? { ...entry, expanded: expand } : entry
  );
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
 * flag; `turnEnded`/`turnError`/`sessionReset` clear it; other messages leave it unchanged.
 */
export function applyTurnFlag(inFlight: boolean, message: HostMessage): boolean {
  switch (message.type) {
    case 'turnStarted':
      return true;
    case 'turnEnded':
    case 'turnError':
    case 'sessionReset':
      return false;
    default:
      return inFlight;
  }
}
