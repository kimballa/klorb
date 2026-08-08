// © Copyright 2026 Aaron Kimball
import type { ChildProcessWithoutNullStreams } from 'child_process';
import { EventEmitter } from 'events';
import { PassThrough, Readable, Writable } from 'stream';

import * as acp from '@agentclientprotocol/sdk';

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
  public readonly receivedLoadSessions: acp.LoadSessionRequest[] = [];
  public readonly receivedPrompts: acp.PromptRequest[] = [];
  public readonly receivedCancels: acp.CancelNotification[] = [];
  public readonly receivedSetSessionModes: acp.SetSessionModeRequest[] = [];
  public readonly receivedExtMethods: { method: string; params: Record<string, unknown> }[] = [];
  public sessionIdToIssue = 'sess-1';
  /** Extra fields merged onto the default `{sessionId}` `session/new` response -- lets a test
   * script `modes`/`_meta` without overriding `newSession()` entirely. */
  public newSessionResult: Partial<acp.NewSessionResponse> = {};
  public onInitialize:
    ((params: acp.InitializeRequest) => Promise<acp.InitializeResponse>) | undefined;
  public onPrompt:
    | ((
        params: acp.PromptRequest,
        connection: acp.AgentSideConnection
      ) => Promise<acp.PromptResponse>)
    | undefined;
  /** Scripts `loadSession()`'s outcome. Unset by default, so `session/load` rejects the way an
   * agent that never advertised the `loadSession` capability would -- matching the behavior
   * tests already relied on before this method existed on `MockAgent`. */
  public onLoadSession:
    | ((
        params: acp.LoadSessionRequest,
        connection: acp.AgentSideConnection
      ) => Promise<acp.LoadSessionResponse>)
    | undefined;
  public onSetSessionMode:
    ((params: acp.SetSessionModeRequest) => Promise<acp.SetSessionModeResponse | void>) | undefined;
  public onExtMethod:
    | ((method: string, params: Record<string, unknown>) => Promise<Record<string, unknown>>)
    | undefined;
  /** Scripts `unstable_listSessions()`'s outcome. Unset by default, so `session/list` fails
   * with `methodNotFound` the way an agent that never advertised the `listSessions` capability
   * would (see `acp.d.ts`'s `unstable_listSessions?` being optional). */
  public onListSessions:
    ((params: acp.ListSessionsRequest) => Promise<acp.ListSessionsResponse>) | undefined;

  public async initialize(params: acp.InitializeRequest): Promise<acp.InitializeResponse> {
    this.receivedInitializes.push(params);
    if (this.onInitialize !== undefined) {
      return this.onInitialize(params);
    }
    return { protocolVersion: acp.PROTOCOL_VERSION, agentCapabilities: {} };
  }

  public async newSession(params: acp.NewSessionRequest): Promise<acp.NewSessionResponse> {
    this.receivedNewSessions.push(params);
    return { sessionId: this.sessionIdToIssue, ...this.newSessionResult };
  }

  public async authenticate(_params: acp.AuthenticateRequest): Promise<acp.AuthenticateResponse> {
    return {};
  }

  public async loadSession(params: acp.LoadSessionRequest): Promise<acp.LoadSessionResponse> {
    this.receivedLoadSessions.push(params);
    if (this.onLoadSession === undefined) {
      throw new Error('session is locked or no longer exists');
    }
    if (this.connection === undefined) {
      throw new Error('MockAgent.connection is not wired yet');
    }
    return this.onLoadSession(params, this.connection);
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

  public async setSessionMode(
    params: acp.SetSessionModeRequest
  ): Promise<acp.SetSessionModeResponse | void> {
    this.receivedSetSessionModes.push(params);
    if (this.onSetSessionMode !== undefined) {
      return this.onSetSessionMode(params);
    }
    return {};
  }

  public async unstable_listSessions(
    params: acp.ListSessionsRequest
  ): Promise<acp.ListSessionsResponse> {
    if (this.onListSessions === undefined) {
      throw new Error('unstable_listSessions is not scripted for this MockAgent');
    }
    return this.onListSessions(params);
  }

  public async extMethod(
    method: string,
    params: Record<string, unknown>
  ): Promise<Record<string, unknown>> {
    this.receivedExtMethods.push({ method, params });
    if (this.onExtMethod !== undefined) {
      return this.onExtMethod(method, params);
    }
    return {};
  }

  /** Streams one session update to the client, as the server does mid-turn. */
  public async sendUpdate(sessionId: string, update: acp.SessionUpdate): Promise<void> {
    if (this.connection === undefined) {
      throw new Error('MockAgent.connection is not wired yet');
    }
    await this.connection.sessionUpdate({ sessionId, update });
  }

  /** Sends one `_klorb/*` extension notification to the client, as the server does after a
   * turn (`_klorb/usage`) or other fire-and-forget event. */
  public async sendExtNotification(method: string, params: Record<string, unknown>): Promise<void> {
    if (this.connection === undefined) {
      throw new Error('MockAgent.connection is not wired yet');
    }
    await this.connection.extNotification(method, params);
  }
}

/** A fake child process whose stdio is served by an in-process `MockAgent`. */
export interface MockAgentChild {
  child: ChildProcessWithoutNullStreams;
  agent: MockAgent;
  /** The same stream as `child.stderr`, typed `Writable` so tests can write fake log output to
   * it directly -- `ChildProcessWithoutNullStreams.stderr` is typed `Readable` only. */
  stderr: Writable;
}

/**
 * Builds the fake `klorb server` child: `child.stdin`/`child.stdout`/`child.stderr` are
 * `PassThrough`s with the given agent's `AgentSideConnection` wired to the stdin/stdout ends,
 * so code under test that binds an ACP client connection to the child's stdio talks real
 * protocol traffic to the mock. `kill()` ends all three streams, which is what makes the
 * client side's connection-closed handling observable in tests.
 */
export function createMockAgentChild(agent: MockAgent = new MockAgent()): MockAgentChild {
  const stdin = new PassThrough();
  const stdout = new PassThrough();
  const stderr = new PassThrough();
  const emitter = new EventEmitter();
  const child = Object.assign(emitter, {
    stdin,
    stdout,
    stderr,
    killed: false,
    kill(): boolean {
      (child as { killed: boolean }).killed = true;
      stdin.end();
      stdout.end();
      stderr.end();
      return true;
    },
  }) as unknown as ChildProcessWithoutNullStreams;

  const stream = acp.ndJsonStream(
    Writable.toWeb(stdout) as unknown as WritableStream<Uint8Array>,
    Readable.toWeb(stdin) as unknown as ReadableStream<Uint8Array>
  );
  agent.connection = new acp.AgentSideConnection(() => agent, stream);
  return { child, agent, stderr };
}
