// © Copyright 2026 Aaron Kimball
import type { HostMessage } from '../shared/webviewMessages';

/** What kind of content a history entry holds. */
export type HistoryEntryKind = 'prompt' | 'response' | 'thinking' | 'error' | 'notice';

/** One entry in the panel's history scroll. `streaming` marks an entry still receiving
 * chunks; the next chunk of the same kind extends it instead of appending a new entry. */
export interface HistoryEntry {
  kind: HistoryEntryKind;
  text: string;
  streaming: boolean;
}

/** Appends the user's submitted prompt as a finished (non-streaming) entry. */
export function appendPrompt(entries: readonly HistoryEntry[], text: string): HistoryEntry[] {
  return [...entries, { kind: 'prompt', text, streaming: false }];
}

function appendChunk(
  entries: readonly HistoryEntry[],
  kind: 'response' | 'thinking',
  text: string,
): HistoryEntry[] {
  const last = entries[entries.length - 1];
  if (last !== undefined && last.kind === kind && last.streaming) {
    return [...entries.slice(0, -1), { ...last, text: last.text + text }];
  }
  return [...entries, { kind, text, streaming: true }];
}

function finishStreaming(entries: readonly HistoryEntry[]): HistoryEntry[] {
  return entries.map((entry) => (entry.streaming ? { ...entry, streaming: false } : entry));
}

/**
 * Applies one host→webview message to the history entry list, returning the new list (the
 * input is never mutated). Streamed chunks extend the trailing streaming entry of the same
 * kind or start a new one — so a response arriving after thinking (or vice versa) starts its
 * own entry, and interleaved phases stay in order.
 */
export function applyHostMessage(
  entries: readonly HistoryEntry[],
  message: HostMessage,
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
      return [...finishStreaming(entries), { kind: 'error', text: message.message, streaming: false }];
    case 'sessionReset':
      return [];
  }
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
