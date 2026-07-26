// © Copyright 2026 Aaron Kimball
import type {
  HostMessage,
  PermissionAskMessage,
  ToolCallDiff,
  ToolCallLocation,
  ToolCallStartedMessage,
  ToolCallUpdatedMessage,
} from 'shared/webviewMessages';

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

/** One entry in the panel's history scroll. */
export type HistoryEntry = TextHistoryEntry | ToolCallHistoryEntry;

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
    entry.kind !== 'toolCall' && entry.streaming ? { ...entry, streaming: false } : entry
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
      // Tracked separately by `applyPendingAsk`, not as a history entry -- the ApprovalPanel
      // mounts from that state instead, and an `appendInteraction()` record lands here only
      // once the ask is answered.
      return [...entries];
  }
}

/**
 * Tracks the permission ask currently awaiting an answer, from the same message stream: a
 * `permissionAsk` sets it (replacing any prior one, which the server never sends concurrently),
 * `sessionReset` clears it, every other message leaves it unchanged. Kept in the model (not
 * component state) so `vscode.setState` persistence keeps an unanswered ask visible across a
 * webview hide/show (see docs/specs/vscode-plugin.md's approval panel section) -- resolving a
 * decision clears it via the caller's own state update, not through this reducer.
 */
export function applyPendingAsk(
  pendingAsk: PermissionAskMessage | undefined,
  message: HostMessage
): PermissionAskMessage | undefined {
  switch (message.type) {
    case 'permissionAsk':
      return message;
    case 'sessionReset':
      return undefined;
    default:
      return pendingAsk;
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
