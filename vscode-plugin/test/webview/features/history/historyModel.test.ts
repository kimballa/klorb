// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import { ERROR_LEVEL_VALUE, WARNING_LEVEL_VALUE } from 'shared/logLevels';
import type { PermissionAskMessage, QuestionAskMessage } from 'shared/webviewMessages';
import {
  appendInteraction,
  appendPrompt,
  appendQuestionInteraction,
  appendQueuedMessage,
  applyHostMessage,
  applyInterruptedMarker,
  applyPendingInteraction,
  applyQueuedMessageSent,
  applySessionReplay,
  applyTaskListUpdate,
  applyToolCallExpandedToggle,
  applyTurnFlag,
  isHistoryEntry,
  isScrollPinnedToBottom,
  type HistoryEntry,
  type ToolCallHistoryEntry,
} from 'webview/features/history';

describe('appendPrompt', () => {
  it('appends a finished prompt entry', () => {
    const entries = appendPrompt([], 'do the thing');
    expect(entries).toEqual([{ kind: 'prompt', text: 'do the thing', streaming: false }]);
  });

  it('carries attached images onto the prompt entry', () => {
    const images = [{ mimeType: 'image/png', dataBase64: 'abcd', name: 'shot.png' }];
    const entries = appendPrompt([], 'what is this?', images);
    expect(entries).toEqual([{ kind: 'prompt', text: 'what is this?', streaming: false, images }]);
  });

  it('omits the images field entirely when none were attached', () => {
    const entries = appendPrompt([], 'do the thing', undefined);
    expect(entries[0]).not.toHaveProperty('images');
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

  it('does not throw on turnEnded when a malformed non-object entry is present', () => {
    // A defensive regression test: `vscode.getState()`'s persisted `entries` isn't runtime-
    // validated the way host<->webview messages are, and a stale/incompatible shape from an old
    // build has been observed to leave a bare string in the array. `finishStreaming()`'s
    // `'streaming' in entry` check would throw a TypeError on a non-object without its own
    // `typeof`/`null` guard.
    const entries = ['not a real entry'] as unknown as HistoryEntry[];
    expect(() =>
      applyHostMessage(entries, { type: 'turnEnded', stopReason: 'end_turn' })
    ).not.toThrow();
    expect(applyHostMessage(entries, { type: 'turnEnded', stopReason: 'end_turn' })).toEqual([
      'not a real entry',
    ]);
  });

  it('appends a notice for a non-end_turn, non-cancelled stop reason', () => {
    let entries: HistoryEntry[] = [];
    entries = applyHostMessage(entries, { type: 'agentChunk', text: 'partial' });
    entries = applyHostMessage(entries, { type: 'turnEnded', stopReason: 'refusal' });
    expect(entries).toEqual([
      { kind: 'response', text: 'partial', streaming: false },
      { kind: 'notice', text: 'Turn ended: refusal', streaming: false },
    ]);
  });

  it('marks a still-streaming response entry (interrupted) for a cancelled stop reason', () => {
    let entries: HistoryEntry[] = [];
    entries = applyHostMessage(entries, { type: 'agentChunk', text: 'partial' });
    entries = applyHostMessage(entries, { type: 'turnEnded', stopReason: 'cancelled' });
    expect(entries).toEqual([
      { kind: 'response', text: 'partial\n\n*(interrupted)*', streaming: false },
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

  it('leaves a sessionStats entry untouched when a later turn ends', () => {
    const withStats = applyHostMessage([], {
      type: 'sessionStats',
      messageCounts: {},
      toolBreakdown: [],
      tokenUsage: {},
      cachePercent: 0,
      totalCost: 0,
    });
    const entries = applyHostMessage(withStats, { type: 'turnEnded', stopReason: 'end_turn' });
    expect(entries).toEqual(withStats);
  });

  it('appends a sessionStats entry on sessionStats', () => {
    const entries = applyHostMessage([], {
      type: 'sessionStats',
      messageCounts: { 'User messages': 1 },
      toolBreakdown: [{ name: 'Bash', succeeded: 1, failed: 0 }],
      tokenUsage: { 'Input tokens': 100 },
      cachePercent: 25,
      totalCost: 0.01,
    });
    expect(entries).toEqual([
      {
        kind: 'sessionStats',
        messageCounts: { 'User messages': 1 },
        toolBreakdown: [{ name: 'Bash', succeeded: 1, failed: 0 }],
        tokenUsage: { 'Input tokens': 100 },
        cachePercent: 25,
        totalCost: 0.01,
      },
    ]);
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

describe('appendQuestionInteraction', () => {
  const ask: QuestionAskMessage = {
    type: 'questionAsk',
    requestId: 1,
    header: 'Format',
    question: 'Which format?',
    options: [{ label: 'JSON' }],
    index: 0,
    total: 2,
  };

  it('appends a compact record with the header/count, question, and answer', () => {
    const entries = appendQuestionInteraction([], ask, 'JSON');
    expect(entries).toEqual([
      {
        kind: 'interaction',
        text: 'Question 1 of 2 · Format\nWhich format?\nAnswer: JSON',
        streaming: false,
      },
    ]);
  });
});

describe('applyPendingInteraction', () => {
  const ask: PermissionAskMessage = {
    type: 'permissionAsk',
    requestId: 1,
    title: 'Run: ls',
    options: [],
    klorbMeta: {},
  };
  const questionAsk: QuestionAskMessage = {
    type: 'questionAsk',
    requestId: 2,
    header: 'Format',
    question: 'Which format?',
    options: [],
    index: 0,
    total: 1,
  };

  it('sets the pending interaction on permissionAsk/questionAsk and clears it on sessionReset', () => {
    expect(applyPendingInteraction(undefined, ask)).toEqual(ask);
    expect(applyPendingInteraction(ask, { type: 'sessionReset' })).toBeUndefined();
    expect(applyPendingInteraction(undefined, questionAsk)).toEqual(questionAsk);
    expect(applyPendingInteraction(questionAsk, { type: 'sessionReset' })).toBeUndefined();
  });

  it('leaves the pending interaction alone for unrelated messages', () => {
    expect(applyPendingInteraction(ask, { type: 'agentChunk', text: 'x' })).toEqual(ask);
    expect(applyPendingInteraction(undefined, { type: 'turnStarted' })).toBeUndefined();
  });
});

describe('applyTaskListUpdate', () => {
  const task = {
    issueId: 12,
    text: '#12 Fix the bug',
    priority: 'high',
    status: 'in_progress',
    blocked: false,
    isCurrentTask: true,
    closed: false,
  };
  const snapshot = {
    summary: { openCount: 1, closedCount: 0, blockedCount: 0, currentTaskId: 12 },
    tasks: [task],
  };

  it('replaces the snapshot wholesale on taskListUpdate', () => {
    const first = applyTaskListUpdate(undefined, { type: 'taskListUpdate', ...snapshot });
    expect(first).toEqual(snapshot);

    const replacement = {
      summary: { openCount: 0, closedCount: 1, blockedCount: 0, currentTaskId: null },
      tasks: [{ ...task, status: 'completed', isCurrentTask: false, closed: true }],
    };
    const second = applyTaskListUpdate(first, { type: 'taskListUpdate', ...replacement });
    expect(second).toEqual(replacement);
  });

  it('clears the snapshot on sessionReset', () => {
    const withSnapshot = applyTaskListUpdate(undefined, { type: 'taskListUpdate', ...snapshot });
    expect(applyTaskListUpdate(withSnapshot, { type: 'sessionReset' })).toBeUndefined();
  });

  it('leaves the snapshot alone for unrelated messages', () => {
    const withSnapshot = applyTaskListUpdate(undefined, { type: 'taskListUpdate', ...snapshot });
    expect(applyTaskListUpdate(withSnapshot, { type: 'agentChunk', text: 'x' })).toEqual(snapshot);
    expect(applyTaskListUpdate(undefined, { type: 'turnStarted' })).toBeUndefined();
  });
});

describe('applyTurnFlag', () => {
  it('raises on turnStarted and clears on turnEnded/turnError/serverLost/sessionReset', () => {
    expect(applyTurnFlag(false, { type: 'turnStarted' })).toBe(true);
    expect(applyTurnFlag(true, { type: 'turnEnded', stopReason: 'end_turn' })).toBe(false);
    expect(applyTurnFlag(true, { type: 'turnError', message: 'x' })).toBe(false);
    expect(applyTurnFlag(true, { type: 'serverLost', message: 'x' })).toBe(false);
    expect(applyTurnFlag(true, { type: 'sessionReset' })).toBe(false);
  });

  it('leaves the flag alone for streamed chunks', () => {
    expect(applyTurnFlag(true, { type: 'agentChunk', text: 'x' })).toBe(true);
    expect(applyTurnFlag(false, { type: 'thoughtChunk', text: 'x' })).toBe(false);
  });
});

describe('queued messages', () => {
  it('appendQueuedMessage appends a queuedMessage entry', () => {
    const entries = appendQueuedMessage([], 'also check the tests');
    expect(entries).toEqual([
      { kind: 'queuedMessage', text: 'also check the tests', streaming: false },
    ]);
  });

  it('applyQueuedMessageSent flips the oldest matching queuedMessage entry to a prompt', () => {
    let entries: HistoryEntry[] = [
      { kind: 'response', text: 'earlier reply', streaming: false },
      { kind: 'queuedMessage', text: 'first queued', streaming: false },
      { kind: 'queuedMessage', text: 'second queued', streaming: false },
    ];
    entries = applyQueuedMessageSent(entries, 'first queued');
    expect(entries).toEqual([
      { kind: 'response', text: 'earlier reply', streaming: false },
      { kind: 'prompt', text: 'first queued', streaming: false },
      { kind: 'queuedMessage', text: 'second queued', streaming: false },
    ]);
  });

  it('applyQueuedMessageSent is a no-op when nothing matches (stale/duplicate notification)', () => {
    const entries: HistoryEntry[] = [
      { kind: 'queuedMessage', text: 'first queued', streaming: false },
    ];
    expect(applyQueuedMessageSent(entries, 'never queued')).toEqual(entries);
  });

  it('applyHostMessage dispatches messageQueued/queuedMessageSent through the same reducers', () => {
    let entries: HistoryEntry[] = [];
    entries = applyHostMessage(entries, { type: 'messageQueued', text: 'hi' });
    expect(entries).toEqual([{ kind: 'queuedMessage', text: 'hi', streaming: false }]);
    entries = applyHostMessage(entries, { type: 'queuedMessageSent', text: 'hi' });
    expect(entries).toEqual([{ kind: 'prompt', text: 'hi', streaming: false }]);
  });

  it('applyHostMessage appends a notice entry for a hook log notification', () => {
    const entries = applyHostMessage([], { type: 'notice', text: 'hook fired' });
    expect(entries).toEqual([{ kind: 'notice', text: 'hook fired', streaming: false }]);
  });

  it('applyHostMessage appends a notice entry for a below-error-level serverLog record', () => {
    const entries = applyHostMessage([], {
      type: 'serverLog',
      text: 'careful',
      level: WARNING_LEVEL_VALUE,
    });
    expect(entries).toEqual([{ kind: 'notice', text: 'careful', streaming: false }]);
  });

  it('applyHostMessage appends an error entry for an ERROR+ serverLog record', () => {
    const entries = applyHostMessage([], {
      type: 'serverLog',
      text: 'boom',
      level: ERROR_LEVEL_VALUE,
    });
    expect(entries).toEqual([{ kind: 'error', text: 'boom', streaming: false }]);
  });
});

describe('applyInterruptedMarker', () => {
  it('appends the marker to a still-streaming response entry and stops it streaming', () => {
    const entries: HistoryEntry[] = [{ kind: 'response', text: 'partial', streaming: true }];
    expect(applyInterruptedMarker(entries)).toEqual([
      { kind: 'response', text: 'partial\n\n*(interrupted)*', streaming: false },
    ]);
  });

  it('appends the marker to a still-streaming thinking entry and stops it streaming', () => {
    const entries: HistoryEntry[] = [{ kind: 'thinking', text: 'pondering', streaming: true }];
    expect(applyInterruptedMarker(entries)).toEqual([
      { kind: 'thinking', text: 'pondering\n\n(interrupted)', streaming: false },
    ]);
  });

  it('appends a standalone notice when nothing was streaming (e.g. cancelled between rounds)', () => {
    const entries: HistoryEntry[] = [
      {
        kind: 'toolCall',
        callId: 'c1',
        status: 'completed',
        title: 'Read foo.py',
        toolKind: 'read',
        locations: [],
        expanded: false,
      },
    ];
    expect(applyInterruptedMarker(entries)).toEqual([
      ...entries,
      { kind: 'notice', text: '(interrupted)', streaming: false },
    ]);
  });

  it('appends a standalone notice for an already-finished trailing entry', () => {
    const entries: HistoryEntry[] = [{ kind: 'response', text: 'done', streaming: false }];
    expect(applyInterruptedMarker(entries)).toEqual([
      { kind: 'response', text: 'done', streaming: false },
      { kind: 'notice', text: '(interrupted)', streaming: false },
    ]);
  });
});

describe('serverLost', () => {
  it('applyHostMessage appends a serverError entry', () => {
    const entries = applyHostMessage([], { type: 'serverLost', message: 'child exited' });
    expect(entries).toEqual([{ kind: 'serverError', text: 'child exited', streaming: false }]);
  });
});

describe('isScrollPinnedToBottom', () => {
  it('is pinned when the viewport shows its bottom edge exactly', () => {
    expect(isScrollPinnedToBottom(400, 500, 100)).toBe(true);
  });

  it('is pinned within the default threshold of the bottom edge', () => {
    expect(isScrollPinnedToBottom(390, 500, 100)).toBe(true);
  });

  it('is not pinned once scrolled meaningfully away from the bottom edge', () => {
    expect(isScrollPinnedToBottom(200, 500, 100)).toBe(false);
  });
});

describe('isHistoryEntry', () => {
  it('accepts an object with a recognized kind, for every subtype', () => {
    expect(isHistoryEntry({ kind: 'prompt', text: 'hi', streaming: false })).toBe(true);
    expect(isHistoryEntry({ kind: 'toolCall', callId: 'c1' })).toBe(true);
    expect(isHistoryEntry({ kind: 'sessionStats' })).toBe(true);
  });

  it('rejects a bare primitive (the shape a stale/incompatible persisted state can hold)', () => {
    expect(isHistoryEntry('not a real entry')).toBe(false);
    expect(isHistoryEntry(42)).toBe(false);
    expect(isHistoryEntry(null)).toBe(false);
    expect(isHistoryEntry(undefined)).toBe(false);
  });

  it('rejects an object with no kind, or an unrecognized one', () => {
    expect(isHistoryEntry({})).toBe(false);
    expect(isHistoryEntry({ kind: 'somethingElse' })).toBe(false);
  });
});

describe('applySessionReplay', () => {
  it('replaces the history wholesale with the replayed entries', () => {
    const existing: HistoryEntry[] = [{ kind: 'notice', text: 'stale cache', streaming: false }];
    const replayed = applySessionReplay([
      { kind: 'prompt', text: 'hi', streaming: false },
      { kind: 'response', text: 'hello', streaming: false },
    ]);

    expect(replayed).toEqual([
      { kind: 'prompt', text: 'hi', streaming: false },
      { kind: 'response', text: 'hello', streaming: false },
    ]);
    // The input `existing` array itself is untouched -- applySessionReplay ignores it entirely
    // and builds a fresh array from the replay payload.
    expect(existing).toEqual([{ kind: 'notice', text: 'stale cache', streaming: false }]);
  });

  it('converts a null contentText (no matching response) to undefined for a tool-call entry', () => {
    const replayed = applySessionReplay([
      {
        kind: 'toolCall',
        callId: 'call-1',
        status: 'completed',
        title: 'ReadFile',
        toolKind: 'read',
        locations: [],
        contentText: null,
        expanded: false,
      },
    ]);

    expect(replayed).toEqual([
      {
        kind: 'toolCall',
        callId: 'call-1',
        status: 'completed',
        title: 'ReadFile',
        toolKind: 'read',
        locations: [],
        contentText: undefined,
        expanded: false,
      },
    ]);
  });

  it('is reachable via applyHostMessage for a sessionReplay message', () => {
    const existing: HistoryEntry[] = [{ kind: 'notice', text: 'stale cache', streaming: false }];

    const result = applyHostMessage(existing, {
      type: 'sessionReplay',
      entries: [{ kind: 'prompt', text: 'hi', streaming: false }],
    });

    expect(result).toEqual([{ kind: 'prompt', text: 'hi', streaming: false }]);
  });
});
