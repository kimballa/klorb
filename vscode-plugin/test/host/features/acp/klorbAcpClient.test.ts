// © Copyright 2026 Aaron Kimball
import { RequestError } from '@agentclientprotocol/sdk';
import { describe, expect, it } from 'vitest';

import { KlorbAcpClient, type SessionUpdateListener } from 'host/features/acp';
import type {
  PermissionAskMessage,
  QuestionAskMessage,
  SessionReplayEntry,
  TaskListUpdateMessage,
  ToolCallLimitAskMessage,
  ToolCallStartedMessage,
  ToolCallUpdatedMessage,
} from 'shared/webviewMessages';

function makeListener(): {
  listener: SessionUpdateListener;
  agentText: string[];
  thoughtText: string[];
  toolCallsStarted: ToolCallStartedMessage[];
  toolCallsUpdated: ToolCallUpdatedMessage[];
  permissionAsks: PermissionAskMessage[];
  questionAsks: QuestionAskMessage[];
  toolCallLimitAsks: ToolCallLimitAskMessage[];
  modeChanges: string[];
  titleChanges: (string | null)[];
  usageUpdates: { usedTokens: number; maxTokens: number | null; outputTokens: number }[];
  taskListUpdates: TaskListUpdateMessage[];
  messagesQueued: string[];
  queuedMessagesSent: string[];
  sessionReplays: SessionReplayEntry[][];
} {
  const agentText: string[] = [];
  const thoughtText: string[] = [];
  const toolCallsStarted: ToolCallStartedMessage[] = [];
  const toolCallsUpdated: ToolCallUpdatedMessage[] = [];
  const permissionAsks: PermissionAskMessage[] = [];
  const questionAsks: QuestionAskMessage[] = [];
  const toolCallLimitAsks: ToolCallLimitAskMessage[] = [];
  const modeChanges: string[] = [];
  const titleChanges: (string | null)[] = [];
  const usageUpdates: { usedTokens: number; maxTokens: number | null; outputTokens: number }[] = [];
  const taskListUpdates: TaskListUpdateMessage[] = [];
  const messagesQueued: string[] = [];
  const queuedMessagesSent: string[] = [];
  const sessionReplays: SessionReplayEntry[][] = [];
  return {
    agentText,
    thoughtText,
    toolCallsStarted,
    toolCallsUpdated,
    permissionAsks,
    questionAsks,
    toolCallLimitAsks,
    modeChanges,
    titleChanges,
    usageUpdates,
    taskListUpdates,
    messagesQueued,
    queuedMessagesSent,
    sessionReplays,
    listener: {
      onAgentText: (text: string) => agentText.push(text),
      onThoughtText: (text: string) => thoughtText.push(text),
      onToolCallStarted: (message: ToolCallStartedMessage) => toolCallsStarted.push(message),
      onToolCallUpdated: (message: ToolCallUpdatedMessage) => toolCallsUpdated.push(message),
      postPermissionAsk: (message: PermissionAskMessage) => permissionAsks.push(message),
      postQuestionAsk: (message: QuestionAskMessage) => questionAsks.push(message),
      postToolCallLimitAsk: (message: ToolCallLimitAskMessage) => toolCallLimitAsks.push(message),
      onSessionInfo: () => undefined,
      onModeChanged: (modeId: string) => modeChanges.push(modeId),
      onSessionTitleChanged: (title: string | null) => titleChanges.push(title),
      onUsageUpdate: (usedTokens: number, maxTokens: number | null, outputTokens: number) =>
        usageUpdates.push({ usedTokens, maxTokens, outputTokens }),
      onTaskListUpdate: (message: TaskListUpdateMessage) => taskListUpdates.push(message),
      onMessageQueued: (text: string) => messagesQueued.push(text),
      onQueuedMessageSent: (text: string) => queuedMessagesSent.push(text),
      onSessionReplay: (entries: SessionReplayEntry[]) => sessionReplays.push(entries),
      onSessionReset: () => undefined,
    },
  };
}

describe('KlorbAcpClient', () => {
  it('dispatches agent_message_chunk text to the listener', async () => {
    const { listener, agentText } = makeListener();
    const client = new KlorbAcpClient(listener, RequestError, () => undefined);

    await client.sessionUpdate({
      sessionId: 's1',
      update: { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'hi' } },
    });

    expect(agentText).toEqual(['hi']);
  });

  it('dispatches agent_thought_chunk text to the listener', async () => {
    const { listener, thoughtText } = makeListener();
    const client = new KlorbAcpClient(listener, RequestError, () => undefined);

    await client.sessionUpdate({
      sessionId: 's1',
      update: { sessionUpdate: 'agent_thought_chunk', content: { type: 'text', text: 'hmm' } },
    });

    expect(thoughtText).toEqual(['hmm']);
  });

  it('dispatches current_mode_update to onModeChanged', async () => {
    const { listener, modeChanges } = makeListener();
    const client = new KlorbAcpClient(listener, RequestError, () => undefined);

    await client.sessionUpdate({
      sessionId: 's1',
      update: { sessionUpdate: 'current_mode_update', currentModeId: 'auto' },
    });

    expect(modeChanges).toEqual(['auto']);
  });

  it('dispatches session_info_update to onSessionTitleChanged when title is present', async () => {
    const { listener, titleChanges } = makeListener();
    const client = new KlorbAcpClient(listener, RequestError, () => undefined);

    await client.sessionUpdate({
      sessionId: 's1',
      update: { sessionUpdate: 'session_info_update', title: 'Fix the bug' },
    });
    await client.sessionUpdate({
      sessionId: 's1',
      update: { sessionUpdate: 'session_info_update', title: null },
    });
    await client.sessionUpdate({
      sessionId: 's1',
      update: { sessionUpdate: 'session_info_update' },
    });

    expect(titleChanges).toEqual(['Fix the bug', null]);
  });

  describe('plan', () => {
    it("flattens a plan update using each entry's own _meta.klorb detail", async () => {
      const { listener, taskListUpdates } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      await client.sessionUpdate({
        sessionId: 's1',
        update: {
          sessionUpdate: 'plan',
          entries: [
            {
              content: '#12 Fix the bug',
              priority: 'high',
              status: 'in_progress',
              _meta: {
                klorb: {
                  issueId: 12,
                  openBlockerCount: 0,
                  blockedBy: [],
                  closed: false,
                  isCurrentTask: true,
                },
              },
            },
            {
              content: '#13 Write docs',
              priority: 'medium',
              status: 'pending',
              _meta: {
                klorb: {
                  issueId: 13,
                  openBlockerCount: 1,
                  blockedBy: [12],
                  closed: false,
                  isCurrentTask: false,
                },
              },
            },
            {
              content: '#11 Old task',
              priority: 'low',
              status: 'completed',
              _meta: {
                klorb: {
                  issueId: 11,
                  openBlockerCount: 0,
                  blockedBy: [],
                  closed: true,
                  isCurrentTask: false,
                },
              },
            },
          ],
          _meta: { klorb: { openCount: 2, closedCount: 1, blockedCount: 1, currentTaskId: 12 } },
        },
      });

      expect(taskListUpdates).toEqual([
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
              issueId: 13,
              text: '#13 Write docs',
              priority: 'medium',
              status: 'pending',
              blocked: true,
              isCurrentTask: false,
              closed: false,
            },
            {
              issueId: 11,
              text: '#11 Old task',
              priority: 'low',
              status: 'completed',
              blocked: false,
              isCurrentTask: false,
              closed: true,
            },
          ],
        },
      ]);
    });

    it('degrades to status-derived detail for a stock ACP agent plan with no _meta.klorb', async () => {
      const { listener, taskListUpdates } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      await client.sessionUpdate({
        sessionId: 's1',
        update: {
          sessionUpdate: 'plan',
          entries: [
            { content: 'Investigate the crash', priority: 'high', status: 'in_progress' },
            { content: 'Ship the fix', priority: 'medium', status: 'pending' },
            { content: 'Backport', priority: 'low', status: 'completed' },
          ],
        },
      });

      expect(taskListUpdates).toEqual([
        {
          type: 'taskListUpdate',
          summary: { openCount: 2, closedCount: 1, blockedCount: 0, currentTaskId: null },
          tasks: [
            {
              text: 'Investigate the crash',
              priority: 'high',
              status: 'in_progress',
              blocked: false,
              isCurrentTask: true,
              closed: false,
            },
            {
              text: 'Ship the fix',
              priority: 'medium',
              status: 'pending',
              blocked: false,
              isCurrentTask: false,
              closed: false,
            },
            {
              text: 'Backport',
              priority: 'low',
              status: 'completed',
              blocked: false,
              isCurrentTask: false,
              closed: true,
            },
          ],
        },
      ]);
    });
  });

  describe('extNotification', () => {
    it('dispatches _klorb/usage to onUsageUpdate', async () => {
      const { listener, usageUpdates } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      await client.extNotification('_klorb/usage', {
        usedTokens: 1400,
        maxTokens: 128000,
        outputTokens: 300,
      });
      await client.extNotification('_klorb/usage', {
        usedTokens: 50,
        maxTokens: null,
        outputTokens: 20,
      });

      expect(usageUpdates).toEqual([
        { usedTokens: 1400, maxTokens: 128000, outputTokens: 300 },
        { usedTokens: 50, maxTokens: null, outputTokens: 20 },
      ]);
    });

    it('logs and ignores malformed _klorb/usage params', async () => {
      const { listener, usageUpdates } = makeListener();
      const logs: string[] = [];
      const client = new KlorbAcpClient(listener, RequestError, (msg: string) => logs.push(msg));

      await client.extNotification('_klorb/usage', { usedTokens: 'not a number' });

      expect(usageUpdates).toEqual([]);
      expect(logs.some((line) => line.includes('malformed'))).toBe(true);
    });

    it('dispatches _klorb/messageQueued to onMessageQueued', async () => {
      const { listener, messagesQueued } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      await client.extNotification('_klorb/messageQueued', { sessionId: 's1', text: 'hi' });

      expect(messagesQueued).toEqual(['hi']);
    });

    it('dispatches _klorb/queuedMessageSent to onQueuedMessageSent', async () => {
      const { listener, queuedMessagesSent } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      await client.extNotification('_klorb/queuedMessageSent', { sessionId: 's1', text: 'hi' });

      expect(queuedMessagesSent).toEqual(['hi']);
    });

    it('dispatches _klorb/sessionReplay to onSessionReplay', async () => {
      const { listener, sessionReplays } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);
      const entries: SessionReplayEntry[] = [
        { kind: 'prompt', text: 'hi', streaming: false },
        { kind: 'response', text: 'hello', streaming: false },
      ];

      await client.extNotification('_klorb/sessionReplay', { sessionId: 's1', entries });

      expect(sessionReplays).toEqual([entries]);
    });

    it('logs and ignores malformed _klorb/sessionReplay params', async () => {
      const logs: string[] = [];
      const { listener, sessionReplays } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, (msg: string) => logs.push(msg));

      await client.extNotification('_klorb/sessionReplay', { sessionId: 's1', entries: 'nope' });

      expect(sessionReplays).toEqual([]);
      expect(logs).toEqual([
        'klorb: malformed _klorb/sessionReplay params: {"sessionId":"s1","entries":"nope"}',
      ]);
    });

    it('logs and ignores malformed _klorb/messageQueued params', async () => {
      const { listener, messagesQueued } = makeListener();
      const logs: string[] = [];
      const client = new KlorbAcpClient(listener, RequestError, (msg: string) => logs.push(msg));

      await client.extNotification('_klorb/messageQueued', { sessionId: 's1' });

      expect(messagesQueued).toEqual([]);
      expect(logs.some((line) => line.includes('malformed'))).toBe(true);
    });

    it('logs and ignores an unrecognized ext notification', async () => {
      const { listener } = makeListener();
      const logs: string[] = [];
      const client = new KlorbAcpClient(listener, RequestError, (msg: string) => logs.push(msg));

      await client.extNotification('_klorb/somethingElse', {});

      expect(logs.some((line) => line.includes('unrecognized'))).toBe(true);
    });
  });

  describe('requestPermission', () => {
    it('posts a flattened permissionAsk and resolves with the selected option', async () => {
      const { listener, permissionAsks } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      const responsePromise = client.requestPermission({
        sessionId: 's1',
        toolCall: { toolCallId: 't1', title: 'Run rm -rf' },
        options: [
          { optionId: 'allow:once', name: 'Allow once', kind: 'allow_once' },
          {
            optionId: 'deny:once',
            name: 'Deny',
            kind: 'reject_once',
            _meta: { klorb: { scope: 'once' } },
          },
        ],
        _meta: { klorb: { resourceDescription: 'run shell command: rm -rf' } },
      });

      expect(permissionAsks).toEqual([
        {
          type: 'permissionAsk',
          requestId: 1,
          title: 'Run rm -rf',
          options: [
            { id: 'allow:once', name: 'Allow once', kind: 'allow_once' },
            { id: 'deny:once', name: 'Deny', kind: 'reject_once', scope: 'once' },
          ],
          klorbMeta: { resourceDescription: 'run shell command: rm -rf' },
        },
      ]);

      client.resolvePermissionDecision(1, { optionId: 'allow:once' });
      await expect(responsePromise).resolves.toEqual({
        outcome: { outcome: 'selected', optionId: 'allow:once' },
      });
    });

    it('encodes otherText into the response _meta.klorb.otherText', async () => {
      const { listener } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      const responsePromise = client.requestPermission({
        sessionId: 's1',
        toolCall: { toolCallId: 't1', title: 'Run something' },
        options: [{ optionId: 'deny:once', name: 'Deny', kind: 'reject_once' }],
      });

      client.resolvePermissionDecision(1, { optionId: 'deny:once', otherText: 'do X instead' });
      await expect(responsePromise).resolves.toEqual({
        outcome: {
          outcome: 'selected',
          optionId: 'deny:once',
          _meta: { klorb: { otherText: 'do X instead' } },
        },
      });
    });

    it('resolves cancelled', async () => {
      const { listener } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      const responsePromise = client.requestPermission({
        sessionId: 's1',
        toolCall: { toolCallId: 't1', title: 'Run something' },
        options: [{ optionId: 'deny:once', name: 'Deny', kind: 'reject_once' }],
      });

      client.resolvePermissionDecision(1, { cancelled: true });
      await expect(responsePromise).resolves.toEqual({ outcome: { outcome: 'cancelled' } });
    });

    it('queues a second concurrent ask behind the first', async () => {
      const { listener, permissionAsks } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      const first = client.requestPermission({
        sessionId: 's1',
        toolCall: { toolCallId: 't1', title: 'First' },
        options: [{ optionId: 'deny:once', name: 'Deny', kind: 'reject_once' }],
      });
      const second = client.requestPermission({
        sessionId: 's1',
        toolCall: { toolCallId: 't2', title: 'Second' },
        options: [{ optionId: 'deny:once', name: 'Deny', kind: 'reject_once' }],
      });

      await Promise.resolve();
      await Promise.resolve();
      expect(permissionAsks.map((ask) => ask.title)).toEqual(['First']);

      client.resolvePermissionDecision(1, { optionId: 'deny:once' });
      await first;
      expect(permissionAsks.map((ask) => ask.title)).toEqual(['First', 'Second']);

      client.resolvePermissionDecision(2, { optionId: 'deny:once' });
      await second;
    });

    it('re-posts the outstanding ask via repostPendingInteraction()', async () => {
      const { listener, permissionAsks } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      const responsePromise = client.requestPermission({
        sessionId: 's1',
        toolCall: { toolCallId: 't1', title: 'First' },
        options: [{ optionId: 'deny:once', name: 'Deny', kind: 'reject_once' }],
      });

      client.repostPendingInteraction();
      expect(permissionAsks).toHaveLength(2);
      expect(permissionAsks[0]).toEqual(permissionAsks[1]);

      client.resolvePermissionDecision(1, { optionId: 'deny:once' });
      await responsePromise;
    });

    it('ignores a decision naming a stale/unknown requestId', async () => {
      const { listener } = makeListener();
      const logs: string[] = [];
      const client = new KlorbAcpClient(listener, RequestError, (msg: string) => logs.push(msg));

      const responsePromise = client.requestPermission({
        sessionId: 's1',
        toolCall: { toolCallId: 't1', title: 'First' },
        options: [{ optionId: 'deny:once', name: 'Deny', kind: 'reject_once' }],
      });

      client.resolvePermissionDecision(99, { optionId: 'deny:once' });
      expect(logs.some((line) => line.includes('unknown request'))).toBe(true);

      client.resolvePermissionDecision(1, { optionId: 'deny:once' });
      await responsePromise;
    });
  });

  describe('extMethod', () => {
    it('posts a toolCallLimitAsk and resolves with approved when the decision is approved', async () => {
      const { listener, toolCallLimitAsks } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      const resultPromise = client.extMethod('_klorb/raiseToolCallLimit', {
        sessionId: 's1',
        message: 'Cap reached',
      });

      // The ask should have been posted to the listener
      expect(toolCallLimitAsks).toHaveLength(1);
      expect(toolCallLimitAsks[0]!.message).toBe('Cap reached');
      expect(toolCallLimitAsks[0]!.type).toBe('toolCallLimitAsk');

      // Resolve the decision
      client.resolveToolCallLimitDecision(toolCallLimitAsks[0]!.requestId, { approved: true });

      const result = await resultPromise;
      expect(result).toEqual({ approved: true });
    });

    it('posts a toolCallLimitAsk and resolves with denied when the decision is cancelled', async () => {
      const { listener, toolCallLimitAsks } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      const resultPromise = client.extMethod('_klorb/raiseToolCallLimit', {
        sessionId: 's1',
        message: 'Cap reached',
      });

      expect(toolCallLimitAsks).toHaveLength(1);

      client.resolveToolCallLimitDecision(toolCallLimitAsks[0]!.requestId, { cancelled: true });

      const result = await resultPromise;
      expect(result).toEqual({ approved: false });
    });

    it('throws method-not-found for an unrecognized ext method', async () => {
      const { listener } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      await expect(client.extMethod('_klorb/unknown', {})).rejects.toThrow(RequestError);
    });
  });

  describe('_klorb/askUserQuestions', () => {
    it('posts a flattened questionAsk and resolves with the selected option', async () => {
      const { listener, questionAsks } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      const resultPromise = client.extMethod('_klorb/askUserQuestions', {
        sessionId: 's1',
        header: 'Format',
        question: 'Which format?',
        options: [{ label: 'JSON' }, { label: 'YAML', description: 'human-friendly' }],
        index: 0,
        total: 2,
      });

      expect(questionAsks).toEqual([
        {
          type: 'questionAsk',
          requestId: 1,
          header: 'Format',
          question: 'Which format?',
          options: [{ label: 'JSON' }, { label: 'YAML', description: 'human-friendly' }],
          index: 0,
          total: 2,
        },
      ]);

      client.resolveQuestionAnswer(1, { selectedOptionIndex: 1 });
      await expect(resultPromise).resolves.toEqual({ selectedOptionIndex: 1 });
    });

    it('resolves otherText and cancelled shapes', async () => {
      const { listener } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      const otherPromise = client.extMethod('_klorb/askUserQuestions', {
        sessionId: 's1',
        header: 'Name',
        question: 'What should we call it?',
        options: [],
        index: 0,
        total: 1,
      });
      client.resolveQuestionAnswer(1, { otherText: 'widget' });
      await expect(otherPromise).resolves.toEqual({ otherText: 'widget' });

      const cancelPromise = client.extMethod('_klorb/askUserQuestions', {
        sessionId: 's1',
        header: 'Ship it?',
        question: 'Ready?',
        options: [],
        index: 0,
        total: 1,
      });
      client.resolveQuestionAnswer(2, { cancelled: true });
      await expect(cancelPromise).resolves.toEqual({ cancelled: true });
    });

    it('resolves cancelled immediately for malformed params, with no wire traffic', async () => {
      const { listener, questionAsks } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      const result = await client.extMethod('_klorb/askUserQuestions', { header: 'bad' });

      expect(result).toEqual({ cancelled: true });
      expect(questionAsks).toEqual([]);
    });

    it('queues a permission ask and a question ask against the same interaction slot', async () => {
      const { listener, permissionAsks, questionAsks } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      const permissionPromise = client.requestPermission({
        sessionId: 's1',
        toolCall: { toolCallId: 't1', title: 'First' },
        options: [{ optionId: 'deny:once', name: 'Deny', kind: 'reject_once' }],
      });
      const questionPromise = client.extMethod('_klorb/askUserQuestions', {
        sessionId: 's1',
        header: 'Name',
        question: 'What should we call it?',
        options: [],
        index: 0,
        total: 1,
      });

      await Promise.resolve();
      await Promise.resolve();
      expect(permissionAsks).toHaveLength(1);
      expect(questionAsks).toHaveLength(0);

      client.resolvePermissionDecision(1, { optionId: 'deny:once' });
      await permissionPromise;
      expect(questionAsks).toHaveLength(1);

      client.resolveQuestionAnswer(2, { cancelled: true });
      await expect(questionPromise).resolves.toEqual({ cancelled: true });
    });
  });

  it('flattens a tool_call update into a toolCallStarted message', async () => {
    const { listener, toolCallsStarted } = makeListener();
    const client = new KlorbAcpClient(listener, RequestError, () => undefined);

    await client.sessionUpdate({
      sessionId: 's1',
      update: {
        sessionUpdate: 'tool_call',
        toolCallId: 'call-1',
        title: 'Read foo.py',
        kind: 'read',
        status: 'in_progress',
        locations: [{ path: '/tmp/foo.py', line: 3 }],
      },
    });

    expect(toolCallsStarted).toEqual([
      {
        type: 'toolCallStarted',
        callId: 'call-1',
        title: 'Read foo.py',
        kind: 'read',
        locations: [{ path: '/tmp/foo.py', line: 3 }],
      },
    ]);
  });

  it('defaults kind to "other" and locations to [] when the server omits them', async () => {
    const { listener, toolCallsStarted } = makeListener();
    const client = new KlorbAcpClient(listener, RequestError, () => undefined);

    await client.sessionUpdate({
      sessionId: 's1',
      update: { sessionUpdate: 'tool_call', toolCallId: 'call-1', title: 'Mystery' },
    });

    expect(toolCallsStarted).toEqual([
      { type: 'toolCallStarted', callId: 'call-1', title: 'Mystery', kind: 'other', locations: [] },
    ]);
  });

  it('flattens a tool_call_update with text content into contentText', async () => {
    const { listener, toolCallsUpdated } = makeListener();
    const client = new KlorbAcpClient(listener, RequestError, () => undefined);

    await client.sessionUpdate({
      sessionId: 's1',
      update: {
        sessionUpdate: 'tool_call_update',
        toolCallId: 'call-1',
        status: 'completed',
        content: [{ type: 'content', content: { type: 'text', text: 'read 10 lines' } }],
      },
    });

    expect(toolCallsUpdated).toEqual([
      {
        type: 'toolCallUpdated',
        callId: 'call-1',
        status: 'completed',
        contentText: 'read 10 lines',
      },
    ]);
  });

  it('flattens a diff content block, preferring _meta.klorb.diffHunks when present', async () => {
    const { listener, toolCallsUpdated } = makeListener();
    const client = new KlorbAcpClient(listener, RequestError, () => undefined);

    await client.sessionUpdate({
      sessionId: 's1',
      update: {
        sessionUpdate: 'tool_call_update',
        toolCallId: 'call-1',
        status: 'completed',
        content: [
          {
            type: 'diff',
            path: '/tmp/foo.py',
            oldText: 'old',
            newText: 'new',
            _meta: {
              klorb: {
                diffHunks: [
                  {
                    lines: [
                      { kind: 'del', old_lineno: 1, new_lineno: null, text: 'old' },
                      { kind: 'add', old_lineno: null, new_lineno: 1, text: 'new' },
                    ],
                  },
                ],
              },
            },
          },
        ],
      },
    });

    expect(toolCallsUpdated).toEqual([
      {
        type: 'toolCallUpdated',
        callId: 'call-1',
        status: 'completed',
        diff: {
          path: '/tmp/foo.py',
          oldText: 'old',
          newText: 'new',
          hunks: [
            {
              lines: [
                { kind: 'del', oldLineno: 1, newLineno: null, text: 'old' },
                { kind: 'add', oldLineno: null, newLineno: 1, text: 'new' },
              ],
            },
          ],
        },
      },
    ]);
  });

  it('falls back to plain oldText/newText when _meta.klorb.diffHunks is absent', async () => {
    const { listener, toolCallsUpdated } = makeListener();
    const client = new KlorbAcpClient(listener, RequestError, () => undefined);

    await client.sessionUpdate({
      sessionId: 's1',
      update: {
        sessionUpdate: 'tool_call_update',
        toolCallId: 'call-1',
        status: 'completed',
        content: [{ type: 'diff', path: '/tmp/foo.py', oldText: null, newText: 'new file' }],
      },
    });

    expect(toolCallsUpdated).toEqual([
      {
        type: 'toolCallUpdated',
        callId: 'call-1',
        status: 'completed',
        diff: { path: '/tmp/foo.py', oldText: null, newText: 'new file' },
      },
    ]);
  });

  it('defaults status to "completed" when the server omits it', async () => {
    const { listener, toolCallsUpdated } = makeListener();
    const client = new KlorbAcpClient(listener, RequestError, () => undefined);

    await client.sessionUpdate({
      sessionId: 's1',
      update: { sessionUpdate: 'tool_call_update', toolCallId: 'call-1' },
    });

    expect(toolCallsUpdated).toEqual([
      { type: 'toolCallUpdated', callId: 'call-1', status: 'completed' },
    ]);
  });

  it('fails fs/terminal methods with method-not-found', () => {
    const { listener } = makeListener();
    const client = new KlorbAcpClient(listener, RequestError, () => undefined);

    expect(() => client.readTextFile()).toThrow(RequestError);
    expect(() => client.writeTextFile()).toThrow(RequestError);
    expect(() => client.createTerminal()).toThrow(RequestError);
  });

  describe('stale-session filtering', () => {
    it('drops a session/update for a session other than currentSessionId()', async () => {
      const { listener, agentText } = makeListener();
      const client = new KlorbAcpClient(
        listener,
        RequestError,
        () => undefined,
        () => 'sess-2'
      );

      await client.sessionUpdate({
        sessionId: 'sess-1',
        update: { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'stale' } },
      });

      expect(agentText).toEqual([]);
    });

    it('forwards a session/update matching currentSessionId()', async () => {
      const { listener, agentText } = makeListener();
      const client = new KlorbAcpClient(
        listener,
        RequestError,
        () => undefined,
        () => 'sess-1'
      );

      await client.sessionUpdate({
        sessionId: 'sess-1',
        update: { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'fresh' } },
      });

      expect(agentText).toEqual(['fresh']);
    });

    it('drops a _klorb/* ext notification for a session other than currentSessionId()', async () => {
      const { listener, messagesQueued } = makeListener();
      const client = new KlorbAcpClient(
        listener,
        RequestError,
        () => undefined,
        () => 'sess-2'
      );

      await client.extNotification('_klorb/messageQueued', { sessionId: 'sess-1', text: 'stale' });

      expect(messagesQueued).toEqual([]);
    });

    it('forwards a _klorb/* ext notification matching currentSessionId()', async () => {
      const { listener, messagesQueued } = makeListener();
      const client = new KlorbAcpClient(
        listener,
        RequestError,
        () => undefined,
        () => 'sess-1'
      );

      await client.extNotification('_klorb/messageQueued', { sessionId: 'sess-1', text: 'fresh' });

      expect(messagesQueued).toEqual(['fresh']);
    });
  });
});
