// © Copyright 2026 Aaron Kimball
import type {
  CreateTerminalResponse,
  ReadTextFileResponse,
  RequestPermissionRequest,
  RequestPermissionResponse,
  SessionNotification,
  WriteTextFileResponse,
} from '@agentclientprotocol/sdk';

/** The `RequestError` class from the loaded ACP SDK module. Passed in as a value because the
 * SDK is ESM-only and the extension host is CommonJS — the module object arrives via the one
 * dynamic `import()` in `AcpConnection.start()` (see src/acpConnection.ts). */
export type RequestErrorClass = (typeof import('@agentclientprotocol/sdk'))['RequestError'];

/** Receives the streamed text the agent produces during a prompt turn. */
export interface SessionUpdateListener {
  /** A piece of streamed response text (`agent_message_chunk`). */
  onAgentText(text: string): void;
  /** A piece of streamed thinking text (`agent_thought_chunk`). */
  onThoughtText(text: string): void;
}

/** Logs a diagnostic line; injectable so tests can capture what would hit the console. */
export type LogFn = (message: string) => void;

/**
 * The klorb VS Code extension's implementation of the ACP SDK's `Client` interface: the
 * handler for requests and notifications the `klorb server` agent sends back over the
 * connection. Constructed fresh by each `AcpConnection.start()` alongside the SDK connection
 * it serves. This checkpoint dispatches `agent_message_chunk`/`agent_thought_chunk` session
 * updates to the listener and answers every permission ask with the first reject option; the
 * fs/terminal methods fail with JSON-RPC method-not-found since the client never advertises
 * those capabilities.
 */
export class KlorbAcpClient {
  private readonly _listener: SessionUpdateListener;
  private readonly _requestError: RequestErrorClass;
  private readonly _log: LogFn;

  public constructor(
    listener: SessionUpdateListener,
    requestError: RequestErrorClass,
    log: LogFn = (message: string) => console.warn(message),
  ) {
    this._listener = listener;
    this._requestError = requestError;
    this._log = log;
  }

  public async sessionUpdate(params: SessionNotification): Promise<void> {
    const update = params.update;
    switch (update.sessionUpdate) {
      case 'agent_message_chunk':
        if (update.content.type === 'text') {
          this._listener.onAgentText(update.content.text);
        } else {
          this._log(`klorb: ignoring non-text agent_message_chunk (${update.content.type})`);
        }
        break;
      case 'agent_thought_chunk':
        if (update.content.type === 'text') {
          this._listener.onThoughtText(update.content.text);
        } else {
          this._log(`klorb: ignoring non-text agent_thought_chunk (${update.content.type})`);
        }
        break;
      default:
        this._log(`klorb: ignoring unhandled session update: ${update.sessionUpdate}`);
        break;
    }
  }

  /**
   * Auto-answers with the first reject option (or a cancelled outcome if the server offered
   * no options), logging what was declined. TODO(aaron): replace with the interactive
   * approval panel when the plan-016-006 increment builds it.
   */
  public async requestPermission(
    params: RequestPermissionRequest,
  ): Promise<RequestPermissionResponse> {
    const rejectOption =
      params.options.find((option) => option.kind === 'reject_once') ??
      params.options.find((option) => option.kind === 'reject_always') ??
      params.options[0];
    if (rejectOption === undefined) {
      this._log('klorb: permission ask arrived with no options; answering cancelled');
      return { outcome: { outcome: 'cancelled' } };
    }
    this._log(
      `klorb: auto-rejecting permission ask "${params.toolCall.title ?? ''}" ` +
        `with option "${rejectOption.name}" (interactive approvals not implemented yet)`,
    );
    return { outcome: { outcome: 'selected', optionId: rejectOption.optionId } };
  }

  public readTextFile(): Promise<ReadTextFileResponse> {
    throw this._requestError.methodNotFound('fs/read_text_file');
  }

  public writeTextFile(): Promise<WriteTextFileResponse> {
    throw this._requestError.methodNotFound('fs/write_text_file');
  }

  public createTerminal(): Promise<CreateTerminalResponse> {
    throw this._requestError.methodNotFound('terminal/create');
  }
}
