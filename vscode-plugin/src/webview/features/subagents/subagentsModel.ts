// © Copyright 2026 Aaron Kimball
import type {
  HostMessage,
  SubagentNodeInfo,
  SubagentTranscriptUpdateMessage,
} from 'shared/webviewMessages';
import { applySessionReplay, type HistoryEntry } from 'webview/features/history';

/** The subagents panel's transcript snapshot, without the message envelope's `type`
 * discriminant -- mirrors `webview/App.tsx`'s own `StatusSnapshot`/`TaskListSnapshot`
 * convention for a message-derived, non-history-entry piece of panel state. */
export type SubagentTranscriptSnapshot = Omit<SubagentTranscriptUpdateMessage, 'type'>;

/**
 * Tracks the subagent tree's node list, from the same message stream: `subagentTreeUpdate`
 * replaces it wholesale (the poller always sends the complete tree, never a delta -- see
 * `SubagentTreeUpdateMessage`'s own doc comment), `sessionReset` clears it (a fresh/loaded
 * session's tree is unrelated to whatever the previous one had), every other message leaves it
 * unchanged.
 */
export function applySubagentTreeUpdate(
  nodes: SubagentNodeInfo[],
  message: HostMessage
): SubagentNodeInfo[] {
  switch (message.type) {
    case 'subagentTreeUpdate':
      return message.nodes;
    case 'sessionReset':
      return [];
    default:
      return nodes;
  }
}

/**
 * Tracks the selected subagent's transcript snapshot: `subagentTranscriptUpdate` replaces it
 * wholesale, `sessionReset` clears it, every other message leaves it unchanged. A stale update
 * for a subagent that's no longer selected can still arrive (the poller drops most of these
 * itself, see `SubagentPoller._pollTranscript`'s in-flight check, but a fresh request racing a
 * selection change isn't fully closeable from the host side alone) -- the caller is expected to
 * only read this snapshot while `sessionId` still matches the current selection.
 */
export function applySubagentTranscriptUpdate(
  transcript: SubagentTranscriptSnapshot | undefined,
  message: HostMessage
): SubagentTranscriptSnapshot | undefined {
  switch (message.type) {
    case 'subagentTranscriptUpdate':
      return {
        sessionId: message.sessionId,
        entries: message.entries,
        state: message.state,
        aborted: message.aborted,
        queuedMessages: message.queuedMessages,
      };
    case 'sessionReset':
      return undefined;
    default:
      return transcript;
  }
}

/** Converts `transcript`'s wire-format entries into `HistoryEntry[]` for reuse through
 * `HistoryView` (see docs/specs/vscode-plugin.md's "Subagents panel" section for why the
 * transcript view reuses the root history renderer wholesale rather than a second one), then
 * overrides each `toolCall` entry's `expanded` flag from `expandedCallIds` -- polling refetches
 * the whole transcript on every tick (see `SubagentPoller`), and the wire's own `expanded` is
 * always `false` (`klorb.server.update_mapping._replay_tool_call_entry` never persists it), so
 * without this override a user's own expand/collapse toggle would silently revert on the next
 * poll. `expandedCallIds` is tracked independently of the entries themselves for exactly this
 * reason. Appends one italic `'queuedMessage'` entry per `transcript.queuedMessages` -- the
 * poll's own stand-in for the root session's `messageQueued`/`queuedMessageSent` notifications
 * (see `SubagentTranscriptUpdateMessage`'s doc comment): it just re-renders every poll until the
 * message is actually folded into a turn, at which point the server stops reporting it and the
 * resulting turn shows up in `entries` instead. */
export function subagentTranscriptEntries(
  transcript: SubagentTranscriptSnapshot | undefined,
  expandedCallIds: ReadonlySet<string>
): HistoryEntry[] {
  if (transcript === undefined) {
    return [];
  }
  const entries = applySessionReplay(transcript.entries).map((entry) =>
    entry.kind === 'toolCall' ? { ...entry, expanded: expandedCallIds.has(entry.callId) } : entry
  );
  const queuedEntries: HistoryEntry[] = transcript.queuedMessages.map((text) => ({
    kind: 'queuedMessage',
    text,
    streaming: false,
  }));
  return [...entries, ...queuedEntries];
}

/** The tree's own root entry (`parentId: null`) -- every other node's own `parentId` chain
 * eventually points here. `undefined` before the first `subagentTreeUpdate` arrives. */
export function findRootNode(nodes: readonly SubagentNodeInfo[]): SubagentNodeInfo | undefined {
  return nodes.find((node) => node.parentId === null);
}

/** One row's leading marker: `'!'` if it's the session an outstanding ask belongs to (and the
 * panel's blink phase is currently on) -- wins over every other state, since it's the most
 * actionable -- else `'*'` while its turn is actively running, else `undefined` (idle). Mirrors
 * `klorb.tui.widgets.subagents_panel.SubagentsPanel._render_row_label`'s exact precedence: one
 * marker slot, not one per condition. */
export function rowMarker(
  node: SubagentNodeInfo,
  attentionSessionId: string | null | undefined,
  blinkOn: boolean
): '!' | '*' | undefined {
  if (blinkOn && attentionSessionId !== null && attentionSessionId !== undefined) {
    if (node.id === attentionSessionId) {
      return '!';
    }
  }
  if (node.state === 'running') {
    return '*';
  }
  return undefined;
}
