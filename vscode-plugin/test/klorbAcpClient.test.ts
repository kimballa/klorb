// © Copyright 2026 Aaron Kimball
import { RequestError } from '@agentclientprotocol/sdk';
import { describe, expect, it } from 'vitest';

import { KlorbAcpClient, type SessionUpdateListener } from '../src/klorbAcpClient';

function makeListener(): { listener: SessionUpdateListener; agentText: string[]; thoughtText: string[] } {
  const agentText: string[] = [];
  const thoughtText: string[] = [];
  return {
    agentText,
    thoughtText,
    listener: {
      onAgentText: (text: string) => agentText.push(text),
      onThoughtText: (text: string) => thoughtText.push(text),
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

  it('fails fs/terminal methods with method-not-found', () => {
    const { listener } = makeListener();
    const client = new KlorbAcpClient(listener, RequestError, () => undefined);

    expect(() => client.readTextFile()).toThrow(RequestError);
    expect(() => client.writeTextFile()).toThrow(RequestError);
    expect(() => client.createTerminal()).toThrow(RequestError);
  });
});
