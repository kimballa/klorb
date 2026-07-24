// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import {
  appendPrompt,
  applyHostMessage,
  applyTurnFlag,
  type HistoryEntry,
} from '../src/webview/historyModel';

describe('appendPrompt', () => {
  it('appends a finished prompt entry', () => {
    const entries = appendPrompt([], 'do the thing');
    expect(entries).toEqual([{ kind: 'prompt', text: 'do the thing', streaming: false }]);
  });
});

describe('applyHostMessage', () => {
  it('creates a streaming response entry on the first chunk and extends it on later ones', () => {
    let entries: HistoryEntry[] = [];
    entries = applyHostMessage(entries, { type: 'agentChunk', text: 'Hello' });
    expect(entries).toEqual([{ kind: 'response', text: 'Hello', streaming: true }]);
    entries = applyHostMessage(entries, { type: 'agentChunk', text: ' world' });
    expect(entries).toEqual([{ kind: 'response', text: 'Hello world', streaming: true }]);
  });

  it('keeps thinking and response chunks in separate entries', () => {
    let entries: HistoryEntry[] = [];
    entries = applyHostMessage(entries, { type: 'thoughtChunk', text: 'pondering' });
    entries = applyHostMessage(entries, { type: 'agentChunk', text: 'Answer' });
    entries = applyHostMessage(entries, { type: 'thoughtChunk', text: 'more pondering' });
    expect(entries).toEqual([
      { kind: 'thinking', text: 'pondering', streaming: true },
      { kind: 'response', text: 'Answer', streaming: true },
      { kind: 'thinking', text: 'more pondering', streaming: true },
    ]);
  });

  it('does not extend a prompt entry with response chunks', () => {
    let entries = appendPrompt([], 'question');
    entries = applyHostMessage(entries, { type: 'agentChunk', text: 'answer' });
    expect(entries).toHaveLength(2);
    expect(entries[1]).toEqual({ kind: 'response', text: 'answer', streaming: true });
  });

  it('finalizes streaming flags on turnEnded', () => {
    let entries: HistoryEntry[] = [];
    entries = applyHostMessage(entries, { type: 'agentChunk', text: 'partial' });
    entries = applyHostMessage(entries, { type: 'turnEnded', stopReason: 'end_turn' });
    expect(entries).toEqual([{ kind: 'response', text: 'partial', streaming: false }]);
  });

  it('appends a notice for a non-end_turn stop reason', () => {
    let entries: HistoryEntry[] = [];
    entries = applyHostMessage(entries, { type: 'agentChunk', text: 'partial' });
    entries = applyHostMessage(entries, { type: 'turnEnded', stopReason: 'cancelled' });
    expect(entries).toEqual([
      { kind: 'response', text: 'partial', streaming: false },
      { kind: 'notice', text: 'Turn ended: cancelled', streaming: false },
    ]);
  });

  it('starts a fresh response entry after a finalized one', () => {
    let entries: HistoryEntry[] = [];
    entries = applyHostMessage(entries, { type: 'agentChunk', text: 'first turn' });
    entries = applyHostMessage(entries, { type: 'turnEnded', stopReason: 'end_turn' });
    entries = applyHostMessage(entries, { type: 'agentChunk', text: 'second turn' });
    expect(entries).toEqual([
      { kind: 'response', text: 'first turn', streaming: false },
      { kind: 'response', text: 'second turn', streaming: true },
    ]);
  });

  it('appends an error entry and finalizes streaming on turnError', () => {
    let entries: HistoryEntry[] = [];
    entries = applyHostMessage(entries, { type: 'agentChunk', text: 'partial' });
    entries = applyHostMessage(entries, { type: 'turnError', message: 'server exploded' });
    expect(entries).toEqual([
      { kind: 'response', text: 'partial', streaming: false },
      { kind: 'error', text: 'server exploded', streaming: false },
    ]);
  });

  it('clears everything on sessionReset', () => {
    let entries = appendPrompt([], 'question');
    entries = applyHostMessage(entries, { type: 'agentChunk', text: 'answer' });
    entries = applyHostMessage(entries, { type: 'sessionReset' });
    expect(entries).toEqual([]);
  });

  it('leaves entries unchanged on turnStarted', () => {
    const entries = appendPrompt([], 'question');
    expect(applyHostMessage(entries, { type: 'turnStarted' })).toEqual(entries);
  });
});

describe('applyTurnFlag', () => {
  it('raises on turnStarted and clears on turnEnded/turnError/sessionReset', () => {
    expect(applyTurnFlag(false, { type: 'turnStarted' })).toBe(true);
    expect(applyTurnFlag(true, { type: 'turnEnded', stopReason: 'end_turn' })).toBe(false);
    expect(applyTurnFlag(true, { type: 'turnError', message: 'x' })).toBe(false);
    expect(applyTurnFlag(true, { type: 'sessionReset' })).toBe(false);
  });

  it('leaves the flag alone for streamed chunks', () => {
    expect(applyTurnFlag(true, { type: 'agentChunk', text: 'x' })).toBe(true);
    expect(applyTurnFlag(false, { type: 'thoughtChunk', text: 'x' })).toBe(false);
  });
});
