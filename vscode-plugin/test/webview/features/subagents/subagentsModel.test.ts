// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import type { HostMessage, SubagentNodeInfo } from 'shared/webviewMessages';
import {
  applySubagentTranscriptUpdate,
  applySubagentTreeUpdate,
  findRootNode,
  rowMarker,
  subagentTranscriptEntries,
} from 'webview/features/subagents';

const ROOT_NODE: SubagentNodeInfo = {
  id: 'root-1',
  parentId: null,
  address: '1',
  title: null,
  role: 'operator',
  state: null,
  aborted: false,
  model: 'anthropic/claude-sonnet-5',
  thinkingEnabled: false,
  thinkingEffort: 'medium',
  usedTokens: 0,
  maxTokens: 128000,
  outputTokens: 0,
};

const CHILD_NODE: SubagentNodeInfo = {
  id: 'subagent-1',
  parentId: 'root-1',
  address: '1.1',
  title: 'find the bug',
  role: 'explorer',
  state: 'running',
  aborted: false,
  model: 'moonshotai/kimi-k2.7-code',
  thinkingEnabled: true,
  thinkingEffort: 'high',
  usedTokens: 500,
  maxTokens: null,
  outputTokens: 50,
};

describe('applySubagentTreeUpdate', () => {
  it('replaces the node list wholesale on subagentTreeUpdate', () => {
    const message: HostMessage = { type: 'subagentTreeUpdate', nodes: [ROOT_NODE, CHILD_NODE] };
    expect(applySubagentTreeUpdate([], message)).toEqual([ROOT_NODE, CHILD_NODE]);
  });

  it('clears the node list on sessionReset', () => {
    expect(applySubagentTreeUpdate([ROOT_NODE], { type: 'sessionReset' })).toEqual([]);
  });

  it('leaves the node list unchanged for every other message', () => {
    const nodes = [ROOT_NODE];
    expect(applySubagentTreeUpdate(nodes, { type: 'turnStarted' })).toBe(nodes);
  });
});

describe('applySubagentTranscriptUpdate', () => {
  it('replaces the transcript wholesale on subagentTranscriptUpdate', () => {
    const message: HostMessage = {
      type: 'subagentTranscriptUpdate',
      sessionId: 'subagent-1',
      entries: [{ kind: 'response', text: 'found it', streaming: false }],
      state: 'finished',
      aborted: false,
      queuedMessages: [],
    };
    expect(applySubagentTranscriptUpdate(undefined, message)).toEqual({
      sessionId: 'subagent-1',
      entries: [{ kind: 'response', text: 'found it', streaming: false }],
      state: 'finished',
      aborted: false,
      queuedMessages: [],
    });
  });

  it('clears the transcript on sessionReset', () => {
    const existing = {
      sessionId: 'subagent-1',
      entries: [],
      state: 'running' as const,
      aborted: false,
      queuedMessages: [],
    };
    expect(applySubagentTranscriptUpdate(existing, { type: 'sessionReset' })).toBeUndefined();
  });

  it('leaves the transcript unchanged for every other message', () => {
    const existing = {
      sessionId: 'subagent-1',
      entries: [],
      state: 'running' as const,
      aborted: false,
      queuedMessages: [],
    };
    expect(applySubagentTranscriptUpdate(existing, { type: 'turnStarted' })).toBe(existing);
  });
});

describe('findRootNode', () => {
  it('finds the node with parentId: null', () => {
    expect(findRootNode([CHILD_NODE, ROOT_NODE])).toBe(ROOT_NODE);
  });

  it('returns undefined when no node is the root', () => {
    expect(findRootNode([CHILD_NODE])).toBeUndefined();
    expect(findRootNode([])).toBeUndefined();
  });
});

describe('rowMarker', () => {
  it('shows "!" for the attention session while blinkOn is true', () => {
    expect(rowMarker(CHILD_NODE, 'subagent-1', true)).toBe('!');
  });

  it('falls back to the running marker for the attention session while blinkOn is false', () => {
    expect(rowMarker(CHILD_NODE, 'subagent-1', false)).toBe('*');
  });

  it('shows "*" for a running row with no attention pending', () => {
    expect(rowMarker(CHILD_NODE, undefined, true)).toBe('*');
    expect(rowMarker(CHILD_NODE, null, true)).toBe('*');
  });

  it('shows no marker for an idle, non-attention row', () => {
    expect(rowMarker(ROOT_NODE, undefined, true)).toBeUndefined();
  });

  it('does not mark an idle row as attention-needed for a different session', () => {
    expect(rowMarker(ROOT_NODE, 'subagent-1', true)).toBeUndefined();
  });
});

describe('subagentTranscriptEntries', () => {
  it('returns an empty list while the transcript is undefined', () => {
    expect(subagentTranscriptEntries(undefined, new Set())).toEqual([]);
  });

  it('converts wire entries to HistoryEntry[]', () => {
    const transcript = {
      sessionId: 'subagent-1',
      entries: [
        { kind: 'prompt' as const, text: 'look into it', streaming: false as const },
        {
          kind: 'toolCall' as const,
          callId: 'call-1',
          status: 'completed' as const,
          title: 'Read foo.py',
          toolKind: 'read',
          locations: [],
          expanded: false,
        },
      ],
      state: 'running' as const,
      aborted: false,
      queuedMessages: [],
    };
    const entries = subagentTranscriptEntries(transcript, new Set());
    expect(entries).toEqual([
      { kind: 'prompt', text: 'look into it', streaming: false, id: expect.any(String) },
      {
        kind: 'toolCall',
        id: 'call-1',
        callId: 'call-1',
        status: 'completed',
        title: 'Read foo.py',
        toolKind: 'read',
        locations: [],
        contentText: undefined,
        expanded: false,
      },
    ]);
  });

  it("overrides a toolCall entry's expanded flag from expandedCallIds", () => {
    const transcript = {
      sessionId: 'subagent-1',
      entries: [
        {
          kind: 'toolCall' as const,
          callId: 'call-1',
          status: 'completed' as const,
          title: 'Read foo.py',
          toolKind: 'read',
          locations: [],
          expanded: false,
        },
      ],
      state: 'running' as const,
      aborted: false,
      queuedMessages: [],
    };
    const entries = subagentTranscriptEntries(transcript, new Set(['call-1']));
    expect(entries[0]).toMatchObject({ callId: 'call-1', expanded: true });
  });

  it('appends an italic queuedMessage entry per pending queued message', () => {
    const transcript = {
      sessionId: 'subagent-1',
      entries: [{ kind: 'response' as const, text: 'working on it', streaming: false as const }],
      state: 'running' as const,
      aborted: false,
      queuedMessages: ['also check the tests'],
    };
    const entries = subagentTranscriptEntries(transcript, new Set());
    expect(entries).toEqual([
      { kind: 'response', text: 'working on it', streaming: false, id: expect.any(String) },
      {
        kind: 'queuedMessage',
        text: 'also check the tests',
        streaming: false,
        id: expect.any(String),
      },
    ]);
  });
});
