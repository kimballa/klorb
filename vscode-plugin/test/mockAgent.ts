// © Copyright 2026 Aaron Kimball
import * as acp from '@agentclientprotocol/sdk';
import type { ChildProcessWithoutNullStreams } from 'child_process';
import { EventEmitter } from 'events';
import { PassThrough, Readable, Writable } from 'stream';

/**
 * A scriptable ACP agent for driving the code under test from the *agent* side of the
 * protocol: the real SDK `AgentSideConnection` running in-process over `PassThrough` stream
 * pairs, standing in for a `klorb server` child process. Defaults answer the handshake
 * successfully; tests override `onInitialize`/`onPrompt` to script failures, streamed
 * session updates, or hangs, and inspect the `received*` arrays for what the client sent.
 */
export class MockAgent implements acp.Agent {
  public connection: acp.AgentSideConnection | undefined;
  public readonly receivedInitializes: acp.InitializeRequest[] = [];
  public readonly receivedNewSessions: acp.NewSessionRequest[] = [];
  public readonly receivedPrompts: acp.PromptRequest[] = [];
  public readonly receivedCancels: acp.CancelNotification[] = [];
  public sessionIdToIssue = 'sess-1';
  public onInitialize: ((params: acp.InitializeRequest) => Promise<acp.InitializeResponse>) | undefined;
  public onPrompt:
    | ((params: acp.PromptRequest, connection: acp.AgentSideConnection) => Promise<acp.PromptResponse>)
    | undefined;

  public async initialize(params: acp.InitializeRequest): Promise<acp.InitializeResponse> {
    this.receivedInitializes.push(params);
    if (this.onInitialize !== undefined) {
      return this.onInitialize(params);
    }
    return { protocolVersion: acp.PROTOCOL_VERSION, agentCapabilities: {} };
  }

  public async newSession(params: acp.NewSessionRequest): Promise<acp.NewSessionResponse> {
    this.receivedNewSessions.push(params);
    return { sessionId: this.sessionIdToIssue };
  }

  public async authenticate(_params: acp.AuthenticateRequest): Promise<acp.AuthenticateResponse> {
    return {};
  }

  public async prompt(params: acp.PromptRequest): Promise<acp.PromptResponse> {
    this.receivedPrompts.push(params);
    if (this.onPrompt !== undefined) {
      if (this.connection === undefined) {
        throw new Error('MockAgent.connection is not wired yet');
      }
      return this.onPrompt(params, this.connection);
    }
    return { stopReason: 'end_turn' };
  }

  public async cancel(params: acp.CancelNotification): Promise<void> {
    this.receivedCancels.push(params);
  }

  /** Streams one session update to the client, as the server does mid-turn. */
  public async sendUpdate(sessionId: string, update: acp.SessionUpdate): Promise<void> {
    if (this.connection === undefined) {
      throw new Error('MockAgent.connection is not wired yet');
    }
    await this.connection.sessionUpdate({ sessionId, update });
  }
}

/** A fake child process whose stdio is served by an in-process `MockAgent`. */
export interface MockAgentChild {
  child: ChildProcessWithoutNullStreams;
  agent: MockAgent;
}

/**
 * Builds the fake `klorb server` child: `child.stdin`/`child.stdout` are `PassThrough`s with
 * the given agent's `AgentSideConnection` wired to their far ends, so code under test that
 * binds an ACP client connection to the child's stdio talks real protocol traffic to the
 * mock. `kill()` ends both streams, which is what makes the client side's connection-closed
 * handling observable in tests.
 */
export function createMockAgentChild(agent: MockAgent = new MockAgent()): MockAgentChild {
  const stdin = new PassThrough();
  const stdout = new PassThrough();
  const emitter = new EventEmitter();
  const child = Object.assign(emitter, {
    stdin,
    stdout,
    killed: false,
    kill(): boolean {
      (child as { killed: boolean }).killed = true;
      stdin.end();
      stdout.end();
      return true;
    },
  }) as unknown as ChildProcessWithoutNullStreams;

  const stream = acp.ndJsonStream(
    Writable.toWeb(stdout) as unknown as WritableStream<Uint8Array>,
    Readable.toWeb(stdin) as unknown as ReadableStream<Uint8Array>,
  );
  agent.connection = new acp.AgentSideConnection(() => agent, stream);
  return { child, agent };
}
