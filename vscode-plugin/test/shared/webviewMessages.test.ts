// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import {
  parseChatHistoryResult,
  parseHostMessage,
  parseSubagentTranscriptResult,
  parseSubagentTreeResult,
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
      { type: 'notice', text: 'hook fired' },
      { type: 'serverLog', text: 'careful', level: 30 },
      { type: 'serverLog', text: 'boom', level: 40 },
      { type: 'sessionReset' },
      {
        type: 'toolCallStarted',
        callId: 'call-1',
        title: 'Read foo.py',
        kind: 'read',
        locations: [{ path: '/tmp/foo.py', line: 3 }],
        toolName: 'ReadFile',
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
      { type: 'statusUpdate' },
      {
        type: 'statusUpdate',
        model: 'gpt-5',
        thinkingEnabled: true,
        thinkingEffort: 'high',
        permissionMode: 'auto',
        usedTokens: 1400,
        maxTokens: 128000,
        outputTokens: 300,
        sessionTitle: 'Fix the bug',
        workspaceTrusted: true,
      },
      { type: 'statusUpdate', maxTokens: null, sessionTitle: null },
      {
        type: 'sessionStats',
        messageCounts: { 'User messages': 1 },
        toolBreakdown: [{ name: 'Bash', succeeded: 1, failed: 0 }],
        tokenUsage: { 'Input tokens': 100 },
        cachePercent: 0,
        totalCost: 0,
      },
      {
        type: 'taskListUpdate',
        summary: { openCount: 2, closedCount: 1, blockedCount: 1, currentTaskId: 12 },
        tasks: [
          {
            issueId: 12,
            text: '#12 Fix the bug',
            priority: 'high',
            status: 'in_progress',
            blocked: false,
            isCurrentTask: true,
            closed: false,
          },
          {
            text: 'Untitled',
            priority: 'medium',
            status: 'pending',
            blocked: true,
            isCurrentTask: false,
            closed: false,
          },
        ],
      },
      {
        type: 'taskListUpdate',
        summary: { openCount: 0, closedCount: 0, blockedCount: 0, currentTaskId: null },
        tasks: [],
      },
      { type: 'toggleTaskPanel' },
      {
        type: 'sessionReplay',
        entries: [
          { kind: 'prompt', text: 'hi', streaming: false },
          {
            kind: 'prompt',
            text: 'look at this',
            streaming: false,
            images: [{ name: 'shot.png', width: 123, height: 456 }, {}],
          },
          { kind: 'response', text: 'hello', streaming: false },
          { kind: 'thinking', text: 'pondering...', streaming: false },
          {
            kind: 'toolCall',
            callId: 'call-1',
            status: 'completed',
            title: 'Read foo.py',
            toolKind: 'read',
            locations: [{ path: '/tmp/foo.py' }],
            contentText: 'done',
            expanded: false,
          },
          {
            kind: 'toolCall',
            callId: 'call-2',
            status: 'failed',
            title: 'Mystery',
            toolKind: 'other',
            locations: [],
            expanded: false,
          },
        ],
      },
      { type: 'workspaceFiles', files: ['src/App.tsx', 'README.md'] },
      { type: 'workspaceFiles', files: [] },
      {
        type: 'statusUpdate',
        model: 'anthropic/claude-sonnet-5',
        activeModelVision: true,
      },
      {
        type: 'imageAttached',
        image: { mimeType: 'image/png', dataBase64: 'abcd', name: 'shot.png' },
      },
      {
        type: 'imageAttached',
        image: { mimeType: 'image/png', dataBase64: 'abcd' },
      },
      {
        type: 'permissionAsk',
        requestId: 2,
        title: '[subagent 1.1 (explorer)] Run: ls',
        options: [{ id: 'allow:once', name: 'Allow once', kind: 'allow_once', scope: 'once' }],
        klorbMeta: { resourceDescription: 'run shell command: ls' },
        originSessionId: 'subagent-1',
      },
      {
        type: 'questionAsk',
        requestId: 2,
        header: '[subagent 1.1 (explorer)] Format',
        question: 'Which format?',
        options: [{ label: 'JSON' }],
        index: 0,
        total: 1,
        originSessionId: 'subagent-1',
      },
      {
        type: 'subagentTreeUpdate',
        nodes: [
          {
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
          },
          {
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
          },
        ],
      },
      { type: 'subagentTreeUpdate', nodes: [] },
      {
        type: 'subagentTranscriptUpdate',
        sessionId: 'subagent-1',
        entries: [{ kind: 'response', text: 'found it', streaming: false }],
        state: 'finished',
        aborted: false,
        queuedMessages: [],
      },
      { type: 'toggleSubagentsPanel' },
      {
        type: 'chatHistoryUpdate',
        messages: [
          { seq: 1, senderId: 'root-1', timestamp: '2026-01-01T00:00:00', body: 'hi @user' },
          { seq: 2, senderId: 'user', timestamp: '2026-01-01T00:00:01', body: 'hi back' },
        ],
        unreadCount: 0,
        unreadMentionCount: 0,
      },
      { type: 'chatHistoryUpdate', messages: [], unreadCount: 2, unreadMentionCount: 1 },
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
    expect(parseHostMessage({ type: 'notice', text: 42 })).toBeUndefined();
    expect(parseHostMessage({ type: 'serverLog', text: 'careful' })).toBeUndefined();
    expect(parseHostMessage({ type: 'serverLog', text: 42, level: 30 })).toBeUndefined();
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
    expect(parseHostMessage({ type: 'statusUpdate', model: 42 })).toBeUndefined();
    expect(parseHostMessage({ type: 'statusUpdate', thinkingEffort: 'extreme' })).toBeUndefined();
    expect(parseHostMessage({ type: 'statusUpdate', usedTokens: '1400' })).toBeUndefined();
    expect(parseHostMessage({ type: 'statusUpdate', maxTokens: 'unlimited' })).toBeUndefined();
    expect(parseHostMessage({ type: 'statusUpdate', outputTokens: '300' })).toBeUndefined();
    expect(parseHostMessage({ type: 'statusUpdate', sessionTitle: 7 })).toBeUndefined();
    expect(parseHostMessage({ type: 'statusUpdate', workspaceTrusted: 'yes' })).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'sessionStats',
        messageCounts: {},
        toolBreakdown: [],
        tokenUsage: {},
        cachePercent: 0,
        // totalCost missing
      })
    ).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'sessionStats',
        messageCounts: { 'User messages': 'not a number' },
        toolBreakdown: [],
        tokenUsage: {},
        cachePercent: 0,
        totalCost: 0,
      })
    ).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'sessionStats',
        messageCounts: {},
        toolBreakdown: [{ name: 'Bash', succeeded: 1 }],
        tokenUsage: {},
        cachePercent: 0,
        totalCost: 0,
      })
    ).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'taskListUpdate',
        summary: { openCount: 0, closedCount: 0, blockedCount: 0, currentTaskId: null },
        // tasks missing
      })
    ).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'taskListUpdate',
        summary: { openCount: 0, closedCount: 0, blockedCount: 0, currentTaskId: 'not-a-number' },
        tasks: [],
      })
    ).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'taskListUpdate',
        summary: { openCount: 0, closedCount: 0, blockedCount: 0, currentTaskId: null },
        tasks: [
          { text: 'x', priority: 'low', status: 'pending', blocked: false, isCurrentTask: false },
        ],
      })
    ).toBeUndefined();
    expect(parseHostMessage({ type: 'sessionReplay' })).toBeUndefined();
    expect(
      parseHostMessage({ type: 'sessionReplay', entries: [{ kind: 'prompt', text: 'hi' }] })
    ).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'sessionReplay',
        entries: [{ kind: 'toolCall', callId: 'c1', status: 'running', title: 'x' }],
      })
    ).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'sessionReplay',
        entries: [{ kind: 'prompt', text: 'hi', streaming: false, images: 'not-an-array' }],
      })
    ).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'sessionReplay',
        entries: [{ kind: 'prompt', text: 'hi', streaming: false, images: [{ width: 'wide' }] }],
      })
    ).toBeUndefined();
    expect(parseHostMessage({ type: 'workspaceFiles' })).toBeUndefined();
    expect(parseHostMessage({ type: 'workspaceFiles', files: ['ok', 42] })).toBeUndefined();
    expect(parseHostMessage({ type: 'statusUpdate', activeModelVision: 'yes' })).toBeUndefined();
    expect(parseHostMessage({ type: 'imageAttached' })).toBeUndefined();
    expect(
      parseHostMessage({ type: 'imageAttached', image: { mimeType: 'image/png' } })
    ).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'imageAttached',
        image: { mimeType: 'image/png', dataBase64: 'abcd', name: 7 },
      })
    ).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'permissionAsk',
        requestId: 1,
        title: 'x',
        options: [],
        klorbMeta: {},
        originSessionId: 7,
      })
    ).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'questionAsk',
        requestId: 1,
        header: 'x',
        question: 'y?',
        options: [],
        index: 0,
        total: 1,
        originSessionId: 7,
      })
    ).toBeUndefined();
    expect(parseHostMessage({ type: 'subagentTreeUpdate' })).toBeUndefined();
    expect(parseHostMessage({ type: 'subagentTreeUpdate', nodes: [{ id: 'x' }] })).toBeUndefined();
    expect(parseHostMessage({ type: 'chatHistoryUpdate', messages: [] })).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'chatHistoryUpdate',
        messages: [{ seq: 1, senderId: 'x' }],
        unreadCount: 0,
        unreadMentionCount: 0,
      })
    ).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'chatHistoryUpdate',
        messages: [],
        unreadCount: '0',
        unreadMentionCount: 0,
      })
    ).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'subagentTranscriptUpdate',
        sessionId: 'x',
        entries: [],
        state: 'paused',
        aborted: false,
      })
    ).toBeUndefined();
    expect(
      parseHostMessage({
        type: 'subagentTranscriptUpdate',
        entries: [],
        state: 'running',
        aborted: false,
      })
    ).toBeUndefined();
  });
});

describe('parseSubagentTreeResult', () => {
  it('accepts a valid {nodes} result', () => {
    const value = {
      nodes: [
        {
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
          maxTokens: null,
          outputTokens: 0,
        },
      ],
    };
    expect(parseSubagentTreeResult(value)).toEqual(value.nodes);
  });

  it('rejects malformed results', () => {
    expect(parseSubagentTreeResult(undefined)).toBeUndefined();
    expect(parseSubagentTreeResult({})).toBeUndefined();
    expect(parseSubagentTreeResult({ nodes: 'not-an-array' })).toBeUndefined();
    expect(parseSubagentTreeResult({ nodes: [{ id: 'x' }] })).toBeUndefined();
  });
});

describe('parseSubagentTranscriptResult', () => {
  it('accepts a valid {entries, state, aborted, queuedMessages} result', () => {
    const value = {
      entries: [{ kind: 'response', text: 'found it', streaming: false }],
      state: 'finished',
      aborted: true,
      queuedMessages: ['hang on, also check the tests'],
    };
    expect(parseSubagentTranscriptResult(value)).toEqual(value);
  });

  it('rejects malformed results', () => {
    expect(parseSubagentTranscriptResult(undefined)).toBeUndefined();
    expect(
      parseSubagentTranscriptResult({ entries: [], state: 'running', queuedMessages: [] })
    ).toBeUndefined();
    expect(
      parseSubagentTranscriptResult({
        entries: [],
        state: 'paused',
        aborted: false,
        queuedMessages: [],
      })
    ).toBeUndefined();
    expect(
      parseSubagentTranscriptResult({
        entries: 'nope',
        state: 'running',
        aborted: false,
        queuedMessages: [],
      })
    ).toBeUndefined();
    expect(
      parseSubagentTranscriptResult({ entries: [], state: 'running', aborted: false })
    ).toBeUndefined();
    expect(
      parseSubagentTranscriptResult({
        entries: [],
        state: 'running',
        aborted: false,
        queuedMessages: [1],
      })
    ).toBeUndefined();
  });
});

describe('parseChatHistoryResult', () => {
  it('accepts a valid {messages, unreadCount, unreadMentionCount} result', () => {
    const value = {
      messages: [{ seq: 1, senderId: 'root-1', timestamp: '2026-01-01T00:00:00', body: 'hi' }],
      unreadCount: 1,
      unreadMentionCount: 0,
    };
    expect(parseChatHistoryResult(value)).toEqual(value);
  });

  it('rejects malformed results', () => {
    expect(parseChatHistoryResult(undefined)).toBeUndefined();
    expect(parseChatHistoryResult({})).toBeUndefined();
    expect(parseChatHistoryResult({ messages: 'not-an-array' })).toBeUndefined();
    expect(
      parseChatHistoryResult({
        messages: [{ senderId: 'x' }],
        unreadCount: 0,
        unreadMentionCount: 0,
      })
    ).toBeUndefined();
  });
});

describe('parseWebviewMessage', () => {
  it('round-trips every webview message shape', () => {
    const messages: WebviewMessage[] = [
      { type: 'submitPrompt', text: 'do the thing' },
      {
        type: 'submitPrompt',
        text: 'what is in this screenshot?',
        images: [{ mimeType: 'image/png', dataBase64: 'abcd', name: 'shot.png' }],
      },
      { type: 'submitPrompt', text: 'steer it', subagentId: 'subagent-1' },
      {
        type: 'enqueueMessage',
        text: 'also this one',
        images: [{ mimeType: 'image/png', dataBase64: 'abcd' }],
      },
      { type: 'attachImageFile' },
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
      { type: 'pickModel' },
      { type: 'pickThinking' },
      { type: 'cyclePermissionMode' },
      { type: 'setPermissionMode' },
      { type: 'showSessionStats' },
      { type: 'newSession' },
      { type: 'reloadSkills' },
      { type: 'listRecentSessions' },
      { type: 'webviewError', message: 'boom' },
      { type: 'webviewError', message: 'boom', stack: 'at App (App.tsx:1:1)' },
      { type: 'setSubagentsPanelVisible', visible: true },
      { type: 'setSubagentsPanelVisible', visible: false },
      { type: 'selectSubagent', sessionId: null },
      { type: 'selectSubagent', sessionId: 'subagent-1' },
      { type: 'cancelSubagent', sessionId: 'subagent-1' },
      { type: 'renameSession', title: 'Fix auth bug' },
      { type: 'renameSession', title: null },
      { type: 'selectChatRoom', selected: true },
      { type: 'selectChatRoom', selected: false },
      { type: 'submitChatMessage', text: 'hey @explorer-1.1' },
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
    expect(
      parseWebviewMessage({ type: 'submitPrompt', text: 'hi', images: 'not-an-array' })
    ).toBeUndefined();
    expect(
      parseWebviewMessage({ type: 'submitPrompt', text: 'hi', images: [{ mimeType: 'image/png' }] })
    ).toBeUndefined();
    expect(
      parseWebviewMessage({ type: 'submitPrompt', text: 'hi', subagentId: 7 })
    ).toBeUndefined();
    expect(
      parseWebviewMessage({
        type: 'enqueueMessage',
        text: 'hi',
        images: [{ mimeType: 'image/png', dataBase64: 'abcd', name: 7 }],
      })
    ).toBeUndefined();
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
    expect(parseWebviewMessage({ type: 'webviewError' })).toBeUndefined();
    expect(
      parseWebviewMessage({ type: 'webviewError', message: 'boom', stack: 7 })
    ).toBeUndefined();
    expect(parseWebviewMessage({ type: 'setSubagentsPanelVisible' })).toBeUndefined();
    expect(
      parseWebviewMessage({ type: 'setSubagentsPanelVisible', visible: 'yes' })
    ).toBeUndefined();
    expect(parseWebviewMessage({ type: 'selectSubagent' })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'selectSubagent', sessionId: 7 })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'cancelSubagent' })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'cancelSubagent', sessionId: null })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'renameSession', title: 7 })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'renameSession' })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'selectChatRoom' })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'selectChatRoom', selected: 'yes' })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'submitChatMessage' })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'submitChatMessage', text: 7 })).toBeUndefined();
  });
});
