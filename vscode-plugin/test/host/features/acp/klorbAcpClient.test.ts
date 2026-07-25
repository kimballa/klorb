// © Copyright 2026 Aaron Kimball
import { RequestError } from '@agentclientprotocol/sdk';
import { describe, expect, it } from 'vitest';

import { KlorbAcpClient, type SessionUpdateListener } from 'host/features/acp';
import type { ToolCallStartedMessage, ToolCallUpdatedMessage } from 'shared/webviewMessages';

function makeListener(): {
  listener: SessionUpdateListener;
  agentText: string[];
  thoughtText: string[];
  toolCallsStarted: ToolCallStartedMessage[];
  toolCallsUpdated: ToolCallUpdatedMessage[];
} {
  const agentText: string[] = [];
  const thoughtText: string[] = [];
  const toolCallsStarted: ToolCallStartedMessage[] = [];
  const toolCallsUpdated: ToolCallUpdatedMessage[] = [];
  return {
    agentText,
    thoughtText,
    toolCallsStarted,
    toolCallsUpdated,
    listener: {
      onAgentText: (text: string) => agentText.push(text),
      onThoughtText: (text: string) => thoughtText.push(text),
      onToolCallStarted: (message: ToolCallStartedMessage) => toolCallsStarted.push(message),
      onToolCallUpdated: (message: ToolCallUpdatedMessage) => toolCallsUpdated.push(message),
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

  it('auto-rejects a permission request with a reject_once option', async () => {
    const { listener } = makeListener();
    const logs: string[] = [];
    const client = new KlorbAcpClient(listener, RequestError, (msg: string) => logs.push(msg));

    const response = await client.requestPermission({
      sessionId: 's1',
      toolCall: { toolCallId: 't1', title: 'Run rm -rf' },
      options: [
        { optionId: 'allow', name: 'Allow', kind: 'allow_once' },
        { optionId: 'deny', name: 'Deny', kind: 'reject_once' },
      ],
    });

    expect(response).toEqual({ outcome: { outcome: 'selected', optionId: 'deny' } });
    expect(logs.some((line) => line.includes('auto-rejecting'))).toBe(true);
  });

  it('answers cancelled when no options are offered', async () => {
    const { listener } = makeListener();
    const client = new KlorbAcpClient(listener, RequestError, () => undefined);

    const response = await client.requestPermission({
      sessionId: 's1',
      toolCall: { toolCallId: 't1', title: 'Mystery' },
      options: [],
    });

    expect(response).toEqual({ outcome: { outcome: 'cancelled' } });
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
