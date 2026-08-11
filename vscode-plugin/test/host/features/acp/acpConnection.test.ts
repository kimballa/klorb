// © Copyright 2026 Aaron Kimball
import * as acp from '@agentclientprotocol/sdk';
import { describe, expect, it, vi } from 'vitest';

import { AcpConnection, errorMessage, type SessionUpdateListener } from 'host/features/acp';
import { KlorbServerProcess } from 'host/klorbServerProcess';

import { createMockAgentChild, MockAgent } from '../../../mockAgent';

const OPTIONS = { command: 'klorb', env: {} };

interface Harness {
  agent: MockAgent;
  connection: AcpConnection;
  events: string[];
  messagesQueued: string[];
  queuedMessagesSent: string[];
}

function makeHarness(agent: MockAgent = new MockAgent()): Harness {
  const { child } = createMockAgentChild(agent);
  const serverProcess = new KlorbServerProcess(() => child);
  const events: string[] = [];
  const messagesQueued: string[] = [];
  const queuedMessagesSent: string[] = [];
  const listener: SessionUpdateListener = {
    onAgentText: (text: string) => events.push(`agent:${text}`),
    onThoughtText: (text: string) => events.push(`thought:${text}`),
    onToolCallStarted: (message) => events.push(`toolCallStarted:${message.callId}`),
    onToolCallUpdated: (message) => events.push(`toolCallUpdated:${message.callId}`),
    postPermissionAsk: (message) => events.push(`permissionAsk:${message.requestId}`),
    postQuestionAsk: (message) => events.push(`questionAsk:${message.requestId}`),
    postToolCallLimitAsk: (message) => events.push(`toolCallLimitAsk:${message.requestId}`),
    onSessionInfo: (info) =>
      events.push(`sessionInfo:${info.modeId ?? ''}:${info.enqueueMessageCapable}`),
    onModeChanged: (modeId) => events.push(`modeChanged:${modeId}`),
    onSessionTitleChanged: (title) => events.push(`titleChanged:${title ?? ''}`),
    onUsageUpdate: (usedTokens) => events.push(`usage:${usedTokens}`),
    onTaskListUpdate: () => undefined,
    onMessageQueued: (text) => messagesQueued.push(text),
    onQueuedMessageSent: (text) => queuedMessagesSent.push(text),
    onNotice: (text) => events.push(`notice:${text}`),
    onSessionReplay: (entries) => events.push(`sessionReplay:${entries.length}`),
    onSessionReset: () => events.push('sessionReset'),
  };
  const connection = new AcpConnection(serverProcess, listener, () => undefined, 500);
  return { agent, connection, events, messagesQueued, queuedMessagesSent };
}

describe('errorMessage', () => {
  it('unwraps Error instances', () => {
    expect(errorMessage(new Error('boom'))).toBe('boom');
  });

  it('unwraps JSON-RPC error objects', () => {
    expect(errorMessage({ code: -32601, message: 'Method not found' })).toBe('Method not found');
  });

  it('stringifies everything else', () => {
    expect(errorMessage(42)).toBe('42');
  });
});

describe('AcpConnection', () => {
  it('performs the initialize/newSession handshake on start()', async () => {
    const { agent, connection } = makeHarness();
    await connection.start(OPTIONS, '/work');

    expect(connection.isReady).toBe(true);
    expect(connection.sessionId).toBe('sess-1');
    expect(agent.receivedInitializes).toHaveLength(1);
    expect(agent.receivedInitializes[0]!.protocolVersion).toBe(acp.PROTOCOL_VERSION);
    expect(agent.receivedInitializes[0]!.clientCapabilities?._meta).toEqual({
      klorb: { raiseToolCallLimit: true, askUserQuestions: true },
    });
    expect(agent.receivedNewSessions).toHaveLength(1);
    expect(agent.receivedNewSessions[0]!.cwd).toBe('/work');
    expect(agent.receivedNewSessions[0]!.mcpServers).toEqual([]);
  });

  it('fires onSessionReset before newSession() when no resume was requested', async () => {
    const { connection, events } = makeHarness();
    await connection.start(OPTIONS, '/work');

    expect(events).toContain('sessionReset');
    expect(events.indexOf('sessionReset')).toBeLessThan(events.indexOf('sessionInfo::false'));
  });

  it('fires onSessionReset and falls back to newSession() when session/load fails', async () => {
    const { agent, connection, events } = makeHarness();
    await connection.start(OPTIONS, '/work', 'stale-session-id');

    expect(connection.sessionId).toBe('sess-1');
    expect(agent.receivedNewSessions).toHaveLength(1);
    expect(events).toContain('sessionReset');
  });

  it('loadSession() delivers a sessionReplay notification sent before session/load responds', async () => {
    const agent = new MockAgent();
    agent.onLoadSession = async (params, connection) => {
      await connection.extNotification('_klorb/sessionReplay', {
        sessionId: params.sessionId,
        entries: [{ kind: 'prompt', text: 'hi', streaming: false }],
      });
      return {};
    };
    const { connection, events } = makeHarness(agent);
    await connection.start(OPTIONS, '/work');

    events.length = 0;
    await connection.loadSession('/work', 'sess-2');

    expect(connection.sessionId).toBe('sess-2');
    expect(events).toContain('sessionReplay:1');
  });

  it('loadSession() restores the previous sessionId when the load fails', async () => {
    const { connection, events } = makeHarness();
    await connection.start(OPTIONS, '/work');
    expect(connection.sessionId).toBe('sess-1');

    events.length = 0;
    await expect(connection.loadSession('/work', 'sess-2')).rejects.toThrow();

    expect(connection.sessionId).toBe('sess-1');
    expect(events).not.toContain('sessionReset');
  });

  it('forwards session/new response state via onSessionInfo', async () => {
    const agent = new MockAgent();
    agent.newSessionResult = {
      modes: {
        currentModeId: 'ask',
        availableModes: [{ id: 'ask', name: 'Ask before acting' }],
      },
      _meta: { klorb: { workspace: { path: '/work', trusted: false }, title: null } },
    };
    const { connection, events } = makeHarness(agent);

    await connection.start(OPTIONS, '/work');

    expect(events).toContain('sessionInfo:ask:false');
  });

  it('threads the initialize()-negotiated enqueueMessage capability through onSessionInfo', async () => {
    const agent = new MockAgent();
    agent.onInitialize = async () => ({
      protocolVersion: acp.PROTOCOL_VERSION,
      agentCapabilities: { _meta: { klorb: { enqueueMessage: true } } },
    });
    const { connection, events } = makeHarness(agent);

    await connection.start(OPTIONS, '/work');

    expect(connection.enqueueMessageCapable).toBe(true);
    expect(events).toContain('sessionInfo::true');
  });

  it('threads the initialize()-negotiated subagents capability through subagentsCapable', async () => {
    const agent = new MockAgent();
    agent.onInitialize = async () => ({
      protocolVersion: acp.PROTOCOL_VERSION,
      agentCapabilities: { _meta: { klorb: { subagents: true } } },
    });
    const { connection } = makeHarness(agent);

    expect(connection.subagentsCapable).toBe(false);
    await connection.start(OPTIONS, '/work');

    expect(connection.subagentsCapable).toBe(true);
  });

  it('subagentsCapable defaults to false for a server that does not advertise it', async () => {
    const { connection } = makeHarness(new MockAgent());

    await connection.start(OPTIONS, '/work');

    expect(connection.subagentsCapable).toBe(false);
  });

  it('resets subagentsCapable to false on stop()', async () => {
    const agent = new MockAgent();
    agent.onInitialize = async () => ({
      protocolVersion: acp.PROTOCOL_VERSION,
      agentCapabilities: { _meta: { klorb: { subagents: true } } },
    });
    const { connection } = makeHarness(agent);
    await connection.start(OPTIONS, '/work');
    expect(connection.subagentsCapable).toBe(true);

    connection.stop();

    expect(connection.subagentsCapable).toBe(false);
  });

  it('enqueueMessage() sends _klorb/enqueueMessage with the text', async () => {
    const agent = new MockAgent();
    agent.onExtMethod = async () => ({ queued: true });
    const { connection } = makeHarness(agent);
    await connection.start(OPTIONS, '/work');

    await connection.enqueueMessage('also check the tests');

    expect(agent.receivedExtMethods).toEqual([
      {
        method: '_klorb/enqueueMessage',
        params: { sessionId: 'sess-1', text: 'also check the tests' },
      },
    ]);
  });

  it('setSessionMode() sends session/set_mode for the live session', async () => {
    const { agent, connection } = makeHarness();
    await connection.start(OPTIONS, '/work');

    await connection.setSessionMode('auto');

    expect(agent.receivedSetSessionModes).toEqual([{ sessionId: 'sess-1', modeId: 'auto' }]);
  });

  it('extMethod() injects sessionId and returns the agent result', async () => {
    const agent = new MockAgent();
    agent.onExtMethod = async () => ({ skillCount: 3 });
    const { connection } = makeHarness(agent);
    await connection.start(OPTIONS, '/work');

    const result = await connection.extMethod('_klorb/reloadSkills', {});

    expect(agent.receivedExtMethods).toEqual([
      { method: '_klorb/reloadSkills', params: { sessionId: 'sess-1' } },
    ]);
    expect(result).toEqual({ skillCount: 3 });
  });

  it('resolves prompt() with the stop reason', async () => {
    const { agent, connection } = makeHarness();
    await connection.start(OPTIONS, '/work');

    await expect(connection.prompt('hello')).resolves.toBe('end_turn');
    expect(agent.receivedPrompts).toHaveLength(1);
    expect(agent.receivedPrompts[0]!.sessionId).toBe('sess-1');
    expect(agent.receivedPrompts[0]!.prompt).toEqual([{ type: 'text', text: 'hello' }]);
  });

  it('delivers streamed response and thought chunks to the listener in order', async () => {
    const agent = new MockAgent();
    agent.onPrompt = async (params, conn) => {
      const send = async (update: acp.SessionUpdate): Promise<void> => {
        await conn.sessionUpdate({ sessionId: params.sessionId, update });
      };
      await send({
        sessionUpdate: 'agent_thought_chunk',
        content: { type: 'text', text: 'pondering' },
      });
      await send({
        sessionUpdate: 'agent_message_chunk',
        content: { type: 'text', text: 'Hello' },
      });
      await send({
        sessionUpdate: 'agent_message_chunk',
        content: { type: 'text', text: ' world' },
      });
      return { stopReason: 'end_turn' };
    };
    const { connection, events } = makeHarness(agent);
    await connection.start(OPTIONS, '/work');

    await connection.prompt('hi');
    expect(events).toEqual([
      'sessionReset',
      'sessionInfo::false',
      'thought:pondering',
      'agent:Hello',
      'agent: world',
    ]);
  });

  it('sends session/cancel for the live session on cancel()', async () => {
    const agent = new MockAgent();
    let finishPrompt: (() => void) | undefined;
    agent.onPrompt = (_params, _conn) =>
      new Promise<acp.PromptResponse>((resolve) => {
        finishPrompt = () => resolve({ stopReason: 'cancelled' });
      });
    const { connection } = makeHarness(agent);
    await connection.start(OPTIONS, '/work');

    const turn = connection.prompt('long task');
    await vi.waitFor(() => expect(agent.receivedPrompts).toHaveLength(1));
    connection.cancel();
    await vi.waitFor(() => expect(agent.receivedCancels).toHaveLength(1));
    expect(agent.receivedCancels[0]!.sessionId).toBe('sess-1');

    finishPrompt?.();
    await expect(turn).resolves.toBe('cancelled');
  });

  it('rejects a second prompt while one is in flight', async () => {
    const agent = new MockAgent();
    let finishPrompt: (() => void) | undefined;
    agent.onPrompt = () =>
      new Promise<acp.PromptResponse>((resolve) => {
        finishPrompt = () => resolve({ stopReason: 'end_turn' });
      });
    const { connection } = makeHarness(agent);
    await connection.start(OPTIONS, '/work');

    const first = connection.prompt('one');
    await vi.waitFor(() => expect(agent.receivedPrompts).toHaveLength(1));
    await expect(connection.prompt('two')).rejects.toThrow('already in flight');

    finishPrompt?.();
    await expect(first).resolves.toBe('end_turn');
  });

  it('produces a readable error when initialize fails (old pre-ACP server)', async () => {
    const agent = new MockAgent();
    agent.onInitialize = () => {
      throw acp.RequestError.methodNotFound('initialize');
    };
    const { connection } = makeHarness(agent);

    await expect(connection.start(OPTIONS, '/work')).rejects.toThrow(/pre-ACP/);
    expect(connection.isReady).toBe(false);
  });

  it('produces the same readable error when initialize never answers', async () => {
    const agent = new MockAgent();
    agent.onInitialize = () => new Promise<acp.InitializeResponse>(() => undefined);
    const { connection } = makeHarness(agent);

    await expect(connection.start(OPTIONS, '/work')).rejects.toThrow(/pre-ACP/);
  });

  it('rejects the handshake when the server speaks a different protocol version', async () => {
    const agent = new MockAgent();
    agent.onInitialize = async () => ({ protocolVersion: 0, agentCapabilities: {} });
    const { connection } = makeHarness(agent);

    await expect(connection.start(OPTIONS, '/work')).rejects.toThrow(/protocol version/);
  });

  it('forwards child stderr to the log, line-by-line, buffering partial lines', async () => {
    const logs: string[] = [];
    const { child, stderr } = createMockAgentChild();
    const serverProcess = new KlorbServerProcess(() => child);
    const listener: SessionUpdateListener = {
      onAgentText: () => undefined,
      onThoughtText: () => undefined,
      onToolCallStarted: () => undefined,
      onToolCallUpdated: () => undefined,
      postPermissionAsk: () => undefined,
      postQuestionAsk: () => undefined,
      postToolCallLimitAsk: () => undefined,
      onSessionInfo: () => undefined,
      onModeChanged: () => undefined,
      onSessionTitleChanged: () => undefined,
      onUsageUpdate: () => undefined,
      onTaskListUpdate: () => undefined,
      onMessageQueued: () => undefined,
      onQueuedMessageSent: () => undefined,
      onNotice: () => undefined,
      onSessionReplay: () => undefined,
      onSessionReset: () => undefined,
    };
    const connection = new AcpConnection(
      serverProcess,
      listener,
      (message: string) => logs.push(message),
      500
    );
    await connection.start(OPTIONS, '/work');

    stderr.write('DEBUG one\nDEBUG two\nDEBUG thr');
    await vi.waitFor(() => expect(logs).toContain('DEBUG two'));
    expect(logs).not.toContain('DEBUG thr');

    stderr.write('ee\n');
    await vi.waitFor(() => expect(logs).toContain('DEBUG three'));
  });

  it('newSession() interrupts an in-flight prompt, cancelling the old session', async () => {
    const agent = new MockAgent();
    agent.onPrompt = () => new Promise<acp.PromptResponse>(() => undefined);
    const { connection } = makeHarness(agent);
    await connection.start(OPTIONS, '/work');
    const generationBefore = connection.turnGeneration;

    const turn = connection.prompt('long task');
    await vi.waitFor(() => expect(agent.receivedPrompts).toHaveLength(1));

    agent.sessionIdToIssue = 'sess-2';
    await connection.newSession('/other');

    await expect(turn).rejects.toThrow('interrupted');
    expect(agent.receivedCancels).toEqual([{ sessionId: 'sess-1' }]);
    expect(connection.sessionId).toBe('sess-2');
    expect(connection.turnGeneration).toBe(generationBefore + 1);
  });

  it('drops a stale session/update sent for a turn newSession() already superseded', async () => {
    const agent = new MockAgent();
    let finishPrompt: (() => void) | undefined;
    agent.onPrompt = (_params, _conn) =>
      new Promise<acp.PromptResponse>((resolve) => {
        finishPrompt = () => resolve({ stopReason: 'cancelled' });
      });
    const { connection, events } = makeHarness(agent);
    await connection.start(OPTIONS, '/work');

    const turn = connection.prompt('long task');
    await vi.waitFor(() => expect(agent.receivedPrompts).toHaveLength(1));

    agent.sessionIdToIssue = 'sess-2';
    await connection.newSession('/other');
    await expect(turn).rejects.toThrow('interrupted');

    events.length = 0;
    await agent.sendUpdate('sess-1', {
      sessionUpdate: 'agent_message_chunk',
      content: { type: 'text', text: 'stale' },
    });
    expect(events).toEqual([]);

    finishPrompt?.();
  });

  it('stop() rejects an in-flight prompt with a restart-style error', async () => {
    const agent = new MockAgent();
    agent.onPrompt = () => new Promise<acp.PromptResponse>(() => undefined);
    const { connection } = makeHarness(agent);
    await connection.start(OPTIONS, '/work');

    const turn = connection.prompt('never finishes');
    await vi.waitFor(() => expect(agent.receivedPrompts).toHaveLength(1));
    connection.stop();

    await expect(turn).rejects.toThrow('klorb server restarted');
    expect(connection.isReady).toBe(false);
  });

  it('listSessions() maps sessionId/title/updatedAt from session/list', async () => {
    const agent = new MockAgent();
    agent.onListSessions = async () => ({
      sessions: [
        {
          cwd: '/work',
          sessionId: 'sess-2',
          title: 'Fix auth bug',
          updatedAt: '2026-08-07T10:00:00',
        },
        { cwd: '/work', sessionId: 'sess-3', title: null, updatedAt: null },
      ],
    });
    const { connection } = makeHarness(agent);
    await connection.start(OPTIONS, '/work');

    const sessions = await connection.listSessions('/work');

    expect(sessions).toEqual([
      { id: 'sess-2', title: 'Fix auth bug', updatedAt: '2026-08-07T10:00:00' },
      { id: 'sess-3', title: null, updatedAt: null },
    ]);
  });
});
