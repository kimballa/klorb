// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import type { PermissionAskMessage } from 'shared/webviewMessages';
import {
  appendInteraction,
  appendPrompt,
  applyExpandAllToolCalls,
  applyHostMessage,
  applyPendingAsk,
  applyToolCallExpandedToggle,
  applyTurnFlag,
  type HistoryEntry,
  type ToolCallHistoryEntry,
} from 'webview/features/history';

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

describe('applyHostMessage tool calls', () => {
  it('appends an in-progress tool call entry on toolCallStarted', () => {
    let entries: HistoryEntry[] = [];
    entries = applyHostMessage(entries, {
      type: 'toolCallStarted',
      callId: 'call-1',
      title: 'Read foo.py',
      kind: 'read',
      locations: [{ path: '/tmp/foo.py' }],
    });
    expect(entries).toEqual([
      {
        kind: 'toolCall',
        callId: 'call-1',
        status: 'in_progress',
        title: 'Read foo.py',
        toolKind: 'read',
        locations: [{ path: '/tmp/foo.py' }],
        expanded: false,
      },
    ]);
  });

  it('seeds a new tool call entry expanded when the global mode is on', () => {
    let entries: HistoryEntry[] = [];
    entries = applyHostMessage(
      entries,
      {
        type: 'toolCallStarted',
        callId: 'call-1',
        title: 'Read foo.py',
        kind: 'read',
        locations: [],
      },
      true
    );
    expect((entries[0] as ToolCallHistoryEntry).expanded).toBe(true);
  });

  it('mutates the matching entry in place on toolCallUpdated, preserving order', () => {
    let entries: HistoryEntry[] = [];
    entries = appendPrompt(entries, 'edit the file');
    entries = applyHostMessage(entries, {
      type: 'toolCallStarted',
      callId: 'call-1',
      title: 'Edit foo.py',
      kind: 'edit',
      locations: [{ path: '/tmp/foo.py' }],
    });
    entries = applyHostMessage(entries, { type: 'agentChunk', text: 'working on it' });
    entries = applyHostMessage(entries, {
      type: 'toolCallUpdated',
      callId: 'call-1',
      status: 'completed',
      contentText: 'edited 1 line',
    });

    expect(entries).toHaveLength(3);
    expect(entries[1]).toEqual({
      kind: 'toolCall',
      callId: 'call-1',
      status: 'completed',
      title: 'Edit foo.py',
      toolKind: 'edit',
      locations: [{ path: '/tmp/foo.py' }],
      contentText: 'edited 1 line',
      expanded: false,
    });
  });

  it('appends a toolCallUpdated for an unknown callId (malformed-arguments fallback)', () => {
    let entries: HistoryEntry[] = [];
    entries = applyHostMessage(entries, {
      type: 'toolCallUpdated',
      callId: 'call-orphan',
      status: 'failed',
      contentText: 'bad JSON arguments',
    });
    expect(entries).toEqual([
      {
        kind: 'toolCall',
        callId: 'call-orphan',
        status: 'failed',
        title: 'Tool call',
        toolKind: 'other',
        locations: [],
        contentText: 'bad JSON arguments',
        diff: undefined,
        expanded: false,
      },
    ]);
  });

  it('interleaves tool calls with streamed chunks, preserving arrival order', () => {
    let entries: HistoryEntry[] = [];
    entries = applyHostMessage(entries, { type: 'agentChunk', text: 'before' });
    entries = applyHostMessage(entries, {
      type: 'toolCallStarted',
      callId: 'call-1',
      title: 'Grep',
      kind: 'search',
      locations: [],
    });
    entries = applyHostMessage(entries, { type: 'agentChunk', text: 'after' });
    expect(entries.map((entry) => entry.kind)).toEqual(['response', 'toolCall', 'response']);
  });
});

describe('applyExpandAllToolCalls', () => {
  it('flips every tool call entry to the given mode, leaving other entries untouched', () => {
    let entries: HistoryEntry[] = [];
    entries = appendPrompt(entries, 'question');
    entries = applyHostMessage(entries, {
      type: 'toolCallStarted',
      callId: 'call-1',
      title: 'Read',
      kind: 'read',
      locations: [],
    });
    entries = applyHostMessage(entries, {
      type: 'toolCallStarted',
      callId: 'call-2',
      title: 'Grep',
      kind: 'search',
      locations: [],
    });

    entries = applyExpandAllToolCalls(entries, true);

    expect(entries[0]!.kind).toBe('prompt');
    expect((entries[1] as ToolCallHistoryEntry).expanded).toBe(true);
    expect((entries[2] as ToolCallHistoryEntry).expanded).toBe(true);
  });
});

describe('applyToolCallExpandedToggle', () => {
  it('flips only the named entry', () => {
    let entries: HistoryEntry[] = [];
    entries = applyHostMessage(entries, {
      type: 'toolCallStarted',
      callId: 'call-1',
      title: 'Read',
      kind: 'read',
      locations: [],
    });
    entries = applyHostMessage(entries, {
      type: 'toolCallStarted',
      callId: 'call-2',
      title: 'Grep',
      kind: 'search',
      locations: [],
    });

    entries = applyToolCallExpandedToggle(entries, 'call-2');

    expect((entries[0] as ToolCallHistoryEntry).expanded).toBe(false);
    expect((entries[1] as ToolCallHistoryEntry).expanded).toBe(true);
  });
});

describe('appendInteraction', () => {
  const ask: PermissionAskMessage = {
    type: 'permissionAsk',
    requestId: 1,
    title: 'Run: ls',
    options: [{ id: 'allow:once', name: 'Allow once', kind: 'allow_once' }],
    klorbMeta: {
      resourceDescription: 'run shell command: ls',
      itemCommandText: 'ls',
      headerKind: 'Run command',
    },
  };

  it('appends a compact record with the header, description, command, and decision', () => {
    const entries = appendInteraction([], ask, 'Allow once');
    expect(entries).toEqual([
      {
        kind: 'interaction',
        text: 'Permission requested: Run command\nrun shell command: ls\nls\nDecision: Allow once',
        streaming: false,
      },
    ]);
  });

  it('uses the "Privilege escalation" header when klorbMeta.escalation is set', () => {
    const escalationAsk: PermissionAskMessage = {
      ...ask,
      klorbMeta: { escalation: { description: 'Escalate to homedir scope' } },
    };
    const entries = appendInteraction([], escalationAsk, 'Deny');
    expect(entries).toEqual([
      {
        kind: 'interaction',
        text: 'Privilege escalation\nDecision: Deny',
        streaming: false,
      },
    ]);
  });
});

describe('applyPendingAsk', () => {
  const ask: PermissionAskMessage = {
    type: 'permissionAsk',
    requestId: 1,
    title: 'Run: ls',
    options: [],
    klorbMeta: {},
  };

  it('sets the pending ask on permissionAsk and clears it on sessionReset', () => {
    expect(applyPendingAsk(undefined, ask)).toEqual(ask);
    expect(applyPendingAsk(ask, { type: 'sessionReset' })).toBeUndefined();
  });

  it('leaves the pending ask alone for unrelated messages', () => {
    expect(applyPendingAsk(ask, { type: 'agentChunk', text: 'x' })).toEqual(ask);
    expect(applyPendingAsk(undefined, { type: 'turnStarted' })).toBeUndefined();
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
