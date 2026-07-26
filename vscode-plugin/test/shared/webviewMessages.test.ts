// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import {
  parseHostMessage,
  parseWebviewMessage,
  type HostMessage,
  type WebviewMessage,
} from 'shared/webviewMessages';

describe('parseHostMessage', () => {
  it('round-trips every host message shape', () => {
    const messages: HostMessage[] = [
      { type: 'turnStarted' },
      { type: 'agentChunk', text: 'hello' },
      { type: 'thoughtChunk', text: 'hmm' },
      { type: 'turnEnded', stopReason: 'end_turn' },
      { type: 'turnError', message: 'boom' },
      { type: 'sessionReset' },
      {
        type: 'toolCallStarted',
        callId: 'call-1',
        title: 'Read foo.py',
        kind: 'read',
        locations: [{ path: '/tmp/foo.py', line: 3 }],
      },
      { type: 'toolCallStarted', callId: 'call-2', title: 'Mystery', kind: 'other', locations: [] },
      {
        type: 'toolCallUpdated',
        callId: 'call-1',
        status: 'completed',
        contentText: 'done',
      },
      {
        type: 'permissionAsk',
        requestId: 1,
        title: 'Run: ls',
        options: [
          { id: 'allow:once', name: 'Allow once', kind: 'allow_once', scope: 'once' },
          { id: 'deny:once', name: 'Deny', kind: 'reject_once' },
        ],
        klorbMeta: { resourceDescription: 'run shell command: ls' },
      },
      {
        type: 'questionAsk',
        requestId: 1,
        header: 'Format',
        question: 'Which format?',
        options: [{ label: 'JSON' }, { label: 'YAML', description: 'human-friendly' }],
        index: 0,
        total: 2,
      },
      {
        type: 'toolCallUpdated',
        callId: 'call-2',
        status: 'failed',
        title: 'Edit foo.py',
        locations: [{ path: '/tmp/foo.py' }],
        diff: {
          path: '/tmp/foo.py',
          oldText: 'old',
          newText: 'new',
          hunks: [
            { lines: [{ kind: 'del', oldLineno: 1, newLineno: null, text: 'old' }] },
            { lines: [{ kind: 'add', oldLineno: null, newLineno: 1, text: 'new' }] },
          ],
        },
      },
    ];
    for (const message of messages) {
      expect(parseHostMessage(message)).toEqual(message);
    }
  });

  it('rejects unknown types and malformed payloads', () => {
    expect(parseHostMessage(undefined)).toBeUndefined();
    expect(parseHostMessage(null)).toBeUndefined();
    expect(parseHostMessage('turnStarted')).toBeUndefined();
    expect(parseHostMessage({ type: 'reply', text: 'legacy shape' })).toBeUndefined();
    expect(parseHostMessage({ type: 'agentChunk' })).toBeUndefined();
    expect(parseHostMessage({ type: 'agentChunk', text: 42 })).toBeUndefined();
    expect(parseHostMessage({ type: 'turnEnded' })).toBeUndefined();
    expect(parseHostMessage({ type: 'turnError', message: null })).toBeUndefined();
    expect(parseHostMessage({ type: 'submitPrompt', text: 'wrong direction' })).toBeUndefined();
    expect(parseHostMessage({ type: 'toolCallStarted', callId: 'c1', title: 'x' })).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'toolCallStarted',
        callId: 'c1',
        title: 'x',
        kind: 'read',
        locations: [{ line: 3 }],
      })
    ).toBeUndefined();
    expect(parseHostMessage({ type: 'toolCallUpdated', callId: 'c1' })).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'toolCallUpdated',
        callId: 'c1',
        status: 'completed',
        diff: { path: '/x', newText: 'y' },
      })
    ).toBeUndefined();
    expect(
      parseHostMessage({ type: 'permissionAsk', requestId: 1, title: 'x', options: [] })
    ).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'permissionAsk',
        requestId: 'not-a-number',
        title: 'x',
        options: [],
        klorbMeta: {},
      })
    ).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'permissionAsk',
        requestId: 1,
        title: 'x',
        options: [{ id: 'a', name: 'A' }],
        klorbMeta: {},
      })
    ).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'questionAsk',
        requestId: 1,
        header: 'x',
        question: 'y?',
        options: [{ label: 'a' }],
      })
    ).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'questionAsk',
        requestId: 1,
        header: 'x',
        question: 'y?',
        options: [{ description: 'no label' }],
        index: 0,
        total: 1,
      })
    ).toBeUndefined();
  });
});

describe('parseWebviewMessage', () => {
  it('round-trips every webview message shape', () => {
    const messages: WebviewMessage[] = [
      { type: 'submitPrompt', text: 'do the thing' },
      { type: 'cancelTurn' },
      { type: 'openLocation', path: '/tmp/foo.py', line: 5 },
      { type: 'openLocation', path: '/tmp/foo.py' },
      { type: 'openDiff', callId: 'call-1', path: '/tmp/foo.py' },
      { type: 'permissionDecision', requestId: 1, optionId: 'allow:once' },
      { type: 'permissionDecision', requestId: 1, optionId: 'deny:once', otherText: 'do X' },
      { type: 'permissionDecision', requestId: 1, cancelled: true },
      { type: 'questionAnswer', requestId: 1, selectedOptionIndex: 0 },
      { type: 'questionAnswer', requestId: 1, otherText: 'widget' },
      { type: 'questionAnswer', requestId: 1, cancelled: true },
    ];
    for (const message of messages) {
      expect(parseWebviewMessage(message)).toEqual(message);
    }
  });

  it('rejects unknown types and malformed payloads', () => {
    expect(parseWebviewMessage(undefined)).toBeUndefined();
    expect(parseWebviewMessage({ type: 'submit', text: 'legacy shape' })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'submitPrompt' })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'submitPrompt', text: 7 })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'agentChunk', text: 'wrong direction' })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'openLocation' })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'openLocation', path: '/x', line: 'nope' })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'openDiff', path: '/x' })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'permissionDecision', requestId: 1 })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'permissionDecision', optionId: 'x' })).toBeUndefined();
    expect(
      parseWebviewMessage({ type: 'permissionDecision', requestId: 1, otherText: 'x' })
    ).toBeUndefined();
    expect(parseWebviewMessage({ type: 'questionAnswer', requestId: 1 })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'questionAnswer', selectedOptionIndex: 0 })).toBeUndefined();
  });
});
