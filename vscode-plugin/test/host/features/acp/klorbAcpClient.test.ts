// © Copyright 2026 Aaron Kimball
import { RequestError } from '@agentclientprotocol/sdk';
import { describe, expect, it } from 'vitest';

import { KlorbAcpClient, type SessionUpdateListener } from 'host/features/acp';
import type {
  PermissionAskMessage,
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
} {
  const agentText: string[] = [];
  const thoughtText: string[] = [];
  const toolCallsStarted: ToolCallStartedMessage[] = [];
  const toolCallsUpdated: ToolCallUpdatedMessage[] = [];
  const permissionAsks: PermissionAskMessage[] = [];
  return {
    agentText,
    thoughtText,
    toolCallsStarted,
    toolCallsUpdated,
    permissionAsks,
    listener: {
      onAgentText: (text: string) => agentText.push(text),
      onThoughtText: (text: string) => thoughtText.push(text),
      onToolCallStarted: (message: ToolCallStartedMessage) => toolCallsStarted.push(message),
      onToolCallUpdated: (message: ToolCallUpdatedMessage) => toolCallsUpdated.push(message),
      postPermissionAsk: (message: PermissionAskMessage) => permissionAsks.push(message),
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

    it('re-posts the outstanding ask via repostPendingAsk()', async () => {
      const { listener, permissionAsks } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      const responsePromise = client.requestPermission({
        sessionId: 's1',
        toolCall: { toolCallId: 't1', title: 'First' },
        options: [{ optionId: 'deny:once', name: 'Deny', kind: 'reject_once' }],
      });

      client.repostPendingAsk();
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
    it('returns approved from the injected raiseToolCallLimit function', async () => {
      const { listener } = makeListener();
      const client = new KlorbAcpClient(
        listener,
        RequestError,
        () => undefined,
        () => Promise.resolve(true)
      );

      const result = await client.extMethod('_klorb/raiseToolCallLimit', {
        sessionId: 's1',
        message: 'Cap reached',
      });

      expect(result).toEqual({ approved: true });
    });

    it('returns denied (false) when the injected raiseToolCallLimit function declines', async () => {
      const { listener } = makeListener();
      const client = new KlorbAcpClient(
        listener,
        RequestError,
        () => undefined,
        () => Promise.resolve(false)
      );

      const result = await client.extMethod('_klorb/raiseToolCallLimit', {
        sessionId: 's1',
        message: 'Cap reached',
      });

      expect(result).toEqual({ approved: false });
    });

    it('defaults to denied (false) when no raiseToolCallLimit function is injected', async () => {
      const { listener } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      const result = await client.extMethod('_klorb/raiseToolCallLimit', {
        sessionId: 's1',
        message: 'Cap reached',
      });

      expect(result).toEqual({ approved: false });
    });

    it('throws method-not-found for an unrecognized ext method', async () => {
      const { listener } = makeListener();
      const client = new KlorbAcpClient(listener, RequestError, () => undefined);

      await expect(client.extMethod('_klorb/unknown', {})).rejects.toThrow(RequestError);
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
});
