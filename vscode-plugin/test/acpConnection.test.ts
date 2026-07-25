// © Copyright 2026 Aaron Kimball
import * as acp from '@agentclientprotocol/sdk';
import { describe, expect, it, vi } from 'vitest';

import { AcpConnection, errorMessage } from '../src/acpConnection';
import type { SessionUpdateListener } from '../src/klorbAcpClient';
import { KlorbServerProcess } from '../src/klorbServerProcess';
import { createMockAgentChild, MockAgent } from './mockAgent';

const OPTIONS = { command: 'klorb', env: {} };

interface Harness {
  agent: MockAgent;
  connection: AcpConnection;
  events: string[];
}

function makeHarness(agent: MockAgent = new MockAgent()): Harness {
  const { child } = createMockAgentChild(agent);
  const serverProcess = new KlorbServerProcess(() => child);
  const events: string[] = [];
  const listener: SessionUpdateListener = {
    onAgentText: (text: string) => events.push(`agent:${text}`),
    onThoughtText: (text: string) => events.push(`thought:${text}`),
  };
  const connection = new AcpConnection(serverProcess, listener, () => undefined, 500);
  return { agent, connection, events };
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
    expect(agent.receivedInitializes[0].protocolVersion).toBe(acp.PROTOCOL_VERSION);
    expect(agent.receivedNewSessions).toHaveLength(1);
    expect(agent.receivedNewSessions[0].cwd).toBe('/work');
    expect(agent.receivedNewSessions[0].mcpServers).toEqual([]);
  });

  it('resolves prompt() with the stop reason', async () => {
    const { agent, connection } = makeHarness();
    await connection.start(OPTIONS, '/work');

    await expect(connection.prompt('hello')).resolves.toBe('end_turn');
    expect(agent.receivedPrompts).toHaveLength(1);
    expect(agent.receivedPrompts[0].sessionId).toBe('sess-1');
    expect(agent.receivedPrompts[0].prompt).toEqual([{ type: 'text', text: 'hello' }]);
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
      await send({ sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'Hello' } });
      await send({ sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: ' world' } });
      return { stopReason: 'end_turn' };
    };
    const { connection, events } = makeHarness(agent);
    await connection.start(OPTIONS, '/work');

    await connection.prompt('hi');
    expect(events).toEqual(['thought:pondering', 'agent:Hello', 'agent: world']);
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
    expect(agent.receivedCancels[0].sessionId).toBe('sess-1');

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
});
