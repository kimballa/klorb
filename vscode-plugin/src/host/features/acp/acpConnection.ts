// © Copyright 2026 Aaron Kimball
import { Readable, Writable } from 'stream';
import { StringDecoder } from 'string_decoder';

import type {
  ClientSideConnection,
  InitializeResponse,
  SessionInfo as AcpSessionInfo,
} from '@agentclientprotocol/sdk';

import type { KlorbServerOptions, KlorbServerProcess } from 'host/klorbServerProcess';

import {
  KlorbAcpClient,
  sessionInfoFromResponse,
  type LogFn,
  type SessionUpdateListener,
} from './klorbAcpClient';

/** How long `start()` waits for the `initialize` reply before concluding the spawned binary
 * doesn't speak ACP at all (e.g. an older klorb that ignores the request without answering). */
const DEFAULT_INITIALIZE_TIMEOUT_MS = 10_000;

/** Renders an unknown thrown value as a human-readable message. JSON-RPC request failures
 * from the ACP SDK reject with a plain `{code, message}` object rather than an `Error`, so
 * both shapes (and anything else) need handling. */
export function errorMessage(err: unknown): string {
  if (err instanceof Error) {
    return err.message;
  }
  if (typeof err === 'object' && err !== null && 'message' in err) {
    const message = (err as { message: unknown }).message;
    if (typeof message === 'string') {
      return message;
    }
  }
  return String(err);
}

/** Reads one boolean flag from `initResult.agentCapabilities._meta.klorb`, `false` if the
 * server's `initialize()` reply carried no such flag at all (an older/non-klorb server, or one
 * that hasn't grown this capability yet). */
function klorbAgentCapability(initResult: InitializeResponse, flag: string): boolean {
  const meta = initResult.agentCapabilities?._meta;
  const klorb =
    meta != null && typeof meta.klorb === 'object' && meta.klorb !== null
      ? (meta.klorb as Record<string, unknown>)
      : undefined;
  return klorb?.[flag] === true;
}

/**
 * Owns the ACP client-side connection to the `klorb server` child process: spawning the child
 * (through `KlorbServerProcess`), performing the `initialize` + `session/new` handshake over
 * its stdio, and exposing the prompt/cancel surface the session view drives. The ACP SDK is
 * ESM-only while the extension host is CommonJS, so the SDK module is loaded with a dynamic
 * `import()` in `start()` rather than a top-level import (type-only imports are erased and
 * safe).
 */
export class AcpConnection {
  private readonly _serverProcess: KlorbServerProcess;
  private readonly _listener: SessionUpdateListener;
  private readonly _log: LogFn;
  private readonly _initializeTimeoutMs: number;
  private _connection: ClientSideConnection | undefined;
  private _client: KlorbAcpClient | undefined;
  private _sessionId: string | undefined;
  private _inflightReject: ((err: Error) => void) | undefined;
  /** Whether the connected server advertised `agentCapabilities._meta.klorb.enqueueMessage`
   * at `initialize()` -- set once per `start()` call and threaded into every `newSession()`'s
   * `SessionInfo`, since it's a property of the connection/server, not of any one session. */
  private _enqueueMessageCapable = false;

  public constructor(
    serverProcess: KlorbServerProcess,
    listener: SessionUpdateListener,
    log: LogFn = (message: string) => console.log(message),
    initializeTimeoutMs: number = DEFAULT_INITIALIZE_TIMEOUT_MS
  ) {
    this._serverProcess = serverProcess;
    this._listener = listener;
    this._log = log;
    this._initializeTimeoutMs = initializeTimeoutMs;
  }

  /** True once the handshake completed and a live session id is held. */
  public get isReady(): boolean {
    return this._connection !== undefined && this._sessionId !== undefined;
  }

  public get sessionId(): string | undefined {
    return this._sessionId;
  }

  /** The live `KlorbAcpClient` handling requests from the current connection, or `undefined`
   * before `start()` completes / after `stop()` -- lets `KlorbSessionViewProvider` forward a
   * `permissionDecision` webview message, or trigger `repostPendingAsk()` after the webview is
   * recreated, without this class exposing the underlying `Client` interface itself. */
  public get client(): KlorbAcpClient | undefined {
    return this._client;
  }

  /**
   * Stops any prior child, spawns a fresh `klorb server`, and performs the ACP handshake:
   * `initialize` (verifying protocol-version compatibility) then either `session/load` for
   * `resumeSessionId` (when given -- the `klorb.resumeLatestSession` setting's on-activation
   * resume, see extension.ts) or `session/new` for `cwd` otherwise. Rejects with a readable
   * error if the binary doesn't complete the handshake — e.g. an older, pre-ACP klorb.
   *
   * A failed `loadSession(cwd, resumeSessionId)` (the saved session is locked by another
   * process, or no longer exists) falls back to `newSession(cwd)` rather than rejecting `start()`
   * outright — per docs/specs/session-persistence.md, it's this client's job to start fresh when
   * a resume fails, not the server's.
   */
  public async start(
    options: KlorbServerOptions,
    cwd: string,
    resumeSessionId?: string
  ): Promise<void> {
    this.stop();
    const acp = await import('@agentclientprotocol/sdk');
    this._log(`klorb: starting "${options.command} server"`);
    const child = this._serverProcess.start(options, cwd);
    child.on('error', (err: Error) => {
      this._log(`klorb: server process error: ${err.message}`);
    });
    this._pipeStderr(child.stderr);
    const stream = acp.ndJsonStream(
      Writable.toWeb(child.stdin) as unknown as WritableStream<Uint8Array>,
      Readable.toWeb(child.stdout) as unknown as ReadableStream<Uint8Array>
    );
    const client = new KlorbAcpClient(this._listener, acp.RequestError, this._log);
    this._client = client;
    const connection = new acp.ClientSideConnection(() => client, stream);
    this._connection = connection;
    void connection.closed.then(() => {
      if (this._connection === connection) {
        this._log('klorb: server connection closed');
        this._handleClosed();
      }
    });

    let initResult: InitializeResponse;
    try {
      initResult = await this._withTimeout(
        this._raceClosed(
          connection.initialize({
            protocolVersion: acp.PROTOCOL_VERSION,
            clientCapabilities: {
              _meta: { klorb: { raiseToolCallLimit: true, askUserQuestions: true } },
            },
          }),
          connection
        ),
        this._initializeTimeoutMs
      );
    } catch (err) {
      this.stop();
      throw new Error(
        `klorb server did not complete the ACP initialize handshake (${errorMessage(err)}). ` +
          'The configured binary (klorb.serverPath) may be an older, pre-ACP klorb — ' +
          'update klorb and run "Klorb: Restart Server".'
      );
    }
    if (initResult.protocolVersion !== acp.PROTOCOL_VERSION) {
      this.stop();
      throw new Error(
        `klorb server speaks ACP protocol version ${initResult.protocolVersion}, but this ` +
          `extension requires version ${acp.PROTOCOL_VERSION}. Update klorb (or the ` +
          'extension) so the two match, then run "Klorb: Restart Server".'
      );
    }
    this._enqueueMessageCapable = klorbAgentCapability(initResult, 'enqueueMessage');
    this._log(`klorb: initialized (protocol v${initResult.protocolVersion})`);
    if (resumeSessionId !== undefined) {
      this._log(`klorb: attempting session/load for resumeSessionId=${resumeSessionId}`);
      try {
        await this.loadSession(cwd, resumeSessionId);
        this._log(`klorb: resume of session ${resumeSessionId} succeeded`);
        return;
      } catch (err) {
        this._log(
          `klorb: could not resume session ${resumeSessionId} (${errorMessage(err)}); starting a new one`
        );
      }
    } else {
      this._log('klorb: no resumeSessionId given; starting a new session');
    }
    // TODO(aaron): Clear the history view; any vscode-restored state is now invalid.
    // We do this with KlorbSessionViewProvider.postHostMessage({ type: 'sessionReset' })
    // ... but how do we get a KlorbSessionViewProvider reference from here?
    await this.newSession(cwd);
  }

  /** Creates a fresh conversation session for `cwd`, replacing the current one. */
  public async newSession(cwd: string): Promise<void> {
    const connection = this._connection;
    if (connection === undefined) {
      throw new Error('klorb server connection is not ready');
    }
    const session = await this._raceClosed(connection.newSession({ cwd, mcpServers: [] }));
    this._sessionId = session.sessionId;
    this._log(`klorb: session created: ${session.sessionId}`);
    this._listener.onSessionInfo(sessionInfoFromResponse(session, this._enqueueMessageCapable));
  }

  /** Loads a previously saved session (`session/load`), replacing the current one -- see
   * docs/specs/session-persistence.md. Rejects (rather than falling back on its own) if the
   * server can't restore it, e.g. it's locked by another process or no longer exists; the
   * caller decides whether to fall back to `newSession()`. On success, the server follows up
   * with a `_klorb/sessionReplay` extension notification (handled by `KlorbAcpClient.
   * extNotification`/`SessionUpdateListener.onSessionReplay`) carrying the restored
   * conversation -- this method itself only updates `sessionId`/`SessionInfo`. */
  public async loadSession(cwd: string, sessionId: string): Promise<void> {
    const connection = this._connection;
    if (connection === undefined) {
      throw new Error('klorb server connection is not ready');
    }
    const session = await this._raceClosed(
      connection.loadSession({ cwd, mcpServers: [], sessionId })
    );
    this._sessionId = sessionId;
    this._log(`klorb: session loaded: ${sessionId}`);
    this._listener.onSessionInfo(sessionInfoFromResponse(session, this._enqueueMessageCapable));
  }

  /** Lists this workspace's saved sessions (`session/list`), most recently touched first. */
  public async listSessions(cwd: string): Promise<{ id: string; title: string | null }[]> {
    const connection = this._connection;
    if (connection === undefined) {
      throw new Error('klorb server connection is not ready');
    }
    const response = await this._raceClosed(connection.unstable_listSessions({ cwd }));
    return response.sessions.map((session: AcpSessionInfo) => ({
      id: session.sessionId,
      title: session.title ?? null,
    }));
  }

  /** Changes the session's permission-framework mode (`session/set_mode`). The server also
   * broadcasts a `current_mode_update` confirming the change, which `KlorbAcpClient.
   * sessionUpdate()` forwards to the listener the same way this call's return does. */
  public async setSessionMode(modeId: string): Promise<void> {
    const { connection, sessionId } = this._requireReady();
    await this._raceClosed(connection.setSessionMode({ sessionId, modeId }));
  }

  /** Calls a `_klorb/*` extension method against the live session, injecting `sessionId`
   * automatically -- every klorb ext method requires it (see docs/specs/klorb-server.md). */
  public async extMethod(
    method: string,
    params: Record<string, unknown>
  ): Promise<Record<string, unknown>> {
    const { connection, sessionId } = this._requireReady();
    return this._raceClosed(connection.extMethod(method, { sessionId, ...params }));
  }

  /** Whether the connected server advertised `_klorb/enqueueMessage` -- gates whether a
   * mid-turn submit can call `enqueueMessage()` below at all (see `PromptInput`'s
   * `enqueueMessageCapable` prop). */
  public get enqueueMessageCapable(): boolean {
    return this._enqueueMessageCapable;
  }

  /** Queues `text` into the currently in-flight turn (`_klorb/enqueueMessage`) -- valid only
   * while a prompt is in flight for this session; the server errors otherwise (see
   * docs/specs/klorb-server.md). */
  public async enqueueMessage(text: string): Promise<void> {
    await this.extMethod('_klorb/enqueueMessage', { text });
  }

  private _requireReady(): { connection: ClientSideConnection; sessionId: string } {
    const connection = this._connection;
    const sessionId = this._sessionId;
    if (connection === undefined || sessionId === undefined) {
      throw new Error('klorb server connection is not ready');
    }
    return { connection, sessionId };
  }

  /**
   * Sends one prompt turn and resolves with the turn's ACP stop reason (e.g. "end_turn",
   * "cancelled"). Rejects if the server fails the turn, exits mid-turn, or `stop()` is
   * called while the turn is in flight.
   */
  public async prompt(text: string): Promise<string> {
    const connection = this._connection;
    const sessionId = this._sessionId;
    if (connection === undefined || sessionId === undefined) {
      throw new Error('klorb server connection is not ready');
    }
    if (this._inflightReject !== undefined) {
      throw new Error('a prompt turn is already in flight');
    }
    const request = connection.prompt({
      sessionId,
      prompt: [{ type: 'text', text }],
    });
    // If stop()/connection-closed wins the race below, the losing request promise may still
    // reject later; mark that rejection as handled so it can't surface as unhandled.
    void request.then(undefined, () => undefined);
    const interrupted = new Promise<never>((_resolve, reject) => {
      this._inflightReject = reject;
    });
    try {
      const response = await Promise.race([request, interrupted]);
      return response.stopReason;
    } finally {
      this._inflightReject = undefined;
    }
  }

  /** Asks the server to cancel the in-flight turn; the turn's `prompt()` still resolves
   * normally (with the "cancelled" stop reason) once the server winds it down. */
  public cancel(): void {
    const connection = this._connection;
    const sessionId = this._sessionId;
    if (connection === undefined || sessionId === undefined) {
      return;
    }
    this._log('klorb: cancelling in-flight turn');
    void connection.cancel({ sessionId }).catch((err: unknown) => {
      this._log(`klorb: cancel notification failed: ${errorMessage(err)}`);
    });
  }

  /** Kills the child process and rejects any in-flight prompt with a restart-style error. */
  public stop(): void {
    if (this._connection !== undefined) {
      this._log('klorb: stopping server connection');
    }
    const reject = this._inflightReject;
    this._inflightReject = undefined;
    this._connection = undefined;
    this._client = undefined;
    this._sessionId = undefined;
    this._enqueueMessageCapable = false;
    reject?.(new Error('klorb server restarted'));
    this._serverProcess.stop();
  }

  /** Tears down connection state after the wire closed underneath us (child crashed or its
   * stdout ended), rejecting any in-flight prompt so it doesn't hang forever. */
  private _handleClosed(): void {
    const reject = this._inflightReject;
    this._inflightReject = undefined;
    this._connection = undefined;
    this._client = undefined;
    this._sessionId = undefined;
    this._enqueueMessageCapable = false;
    reject?.(new Error('klorb server exited unexpectedly; run "Klorb: Restart Server"'));
    this._serverProcess.stop();
  }

  /** Forwards the child's stderr -- where klorb's Python `logging` output goes -- to `_log`
   * line-by-line, so it lands in the extension's output channel instead of an unread pipe
   * (an unread stderr pipe can eventually fill its OS buffer and block the child on write).
   * Buffers a trailing partial line (and multi-byte characters split across chunk boundaries,
   * via `StringDecoder`) until a newline or stream close completes it. */
  private _pipeStderr(stderr: Readable): void {
    const decoder = new StringDecoder('utf8');
    let buffer = '';
    stderr.on('data', (chunk: Buffer) => {
      buffer += decoder.write(chunk);
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        this._log(line);
      }
    });
    stderr.on('close', () => {
      buffer += decoder.end();
      if (buffer.length > 0) {
        this._log(buffer);
        buffer = '';
      }
    });
  }

  /** Races `request` against the connection closing, so a request against a dead server
   * rejects instead of pending forever (the SDK never rejects pending requests on close). */
  private _raceClosed<T>(request: Promise<T>, connection?: ClientSideConnection): Promise<T> {
    const conn = connection ?? this._connection;
    if (conn === undefined) {
      return request;
    }
    void request.then(undefined, () => undefined);
    const closedError: Promise<never> = conn.closed.then(() => {
      throw new Error('klorb server exited before completing the request');
    });
    void closedError.catch(() => undefined);
    return Promise.race([request, closedError]);
  }

  private _withTimeout<T>(request: Promise<T>, timeoutMs: number): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error(`timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      request.then(
        (value: T) => {
          clearTimeout(timer);
          resolve(value);
        },
        (err: unknown) => {
          clearTimeout(timer);
          reject(err instanceof Error ? err : new Error(errorMessage(err)));
        }
      );
    });
  }
}
