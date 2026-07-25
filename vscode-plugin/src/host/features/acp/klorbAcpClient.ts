// © Copyright 2026 Aaron Kimball
import type {
  CreateTerminalResponse,
  ReadTextFileResponse,
  RequestPermissionRequest,
  RequestPermissionResponse,
  SessionNotification,
  ToolCall,
  ToolCallContent,
  ToolCallLocation as AcpToolCallLocation,
  ToolCallUpdate,
  WriteTextFileResponse,
} from '@agentclientprotocol/sdk';

import type {
  DiffHunk,
  DiffHunkLine,
  ToolCallDiff,
  ToolCallLocation,
  ToolCallStartedMessage,
  ToolCallUpdatedMessage,
} from 'shared/webviewMessages';

/** The `RequestError` class from the loaded ACP SDK module. Passed in as a value because the
 * SDK is ESM-only and the extension host is CommonJS — the module object arrives via the one
 * dynamic `import()` in `AcpConnection.start()` (see src/host/features/acp/acpConnection.ts). */
export type RequestErrorClass = (typeof import('@agentclientprotocol/sdk'))['RequestError'];

/** Receives the streamed text and tool-call activity the agent produces during a prompt turn. */
export interface SessionUpdateListener {
  /** A piece of streamed response text (`agent_message_chunk`). */
  onAgentText(text: string): void;
  /** A piece of streamed thinking text (`agent_thought_chunk`). */
  onThoughtText(text: string): void;
  /** A tool call was just started (`tool_call`). */
  onToolCallStarted(message: ToolCallStartedMessage): void;
  /** A tool call finished or otherwise changed (`tool_call_update`). */
  onToolCallUpdated(message: ToolCallUpdatedMessage): void;
}

function toLocationMessage(location: AcpToolCallLocation): ToolCallLocation {
  return location.line != null
    ? { path: location.path, line: location.line }
    : { path: location.path };
}

function isDiffLineKind(value: unknown): value is DiffHunkLine['kind'] {
  return value === 'context' || value === 'add' || value === 'del';
}

/** Validates and camelCases one `_meta.klorb.diffHunks[]` entry -- the wire shape is a verbatim
 * `klorb.tools.util.diff_lines.DiffHunk.model_dump()`, so its line entries carry pydantic's
 * plain (snake_case) field names, unlike the rest of this host/webview boundary. */
function parseDiffHunkLine(value: unknown): DiffHunkLine | undefined {
  if (typeof value !== 'object' || value === null) {
    return undefined;
  }
  const v = value as Record<string, unknown>;
  const oldLineno = v.old_lineno;
  const newLineno = v.new_lineno;
  if (
    !isDiffLineKind(v.kind) ||
    typeof v.text !== 'string' ||
    (oldLineno !== null && typeof oldLineno !== 'number') ||
    (newLineno !== null && typeof newLineno !== 'number')
  ) {
    return undefined;
  }
  return {
    kind: v.kind,
    oldLineno: oldLineno as number | null,
    newLineno: newLineno as number | null,
    text: v.text,
  };
}

function parseDiffHunk(value: unknown): DiffHunk | undefined {
  if (typeof value !== 'object' || value === null) {
    return undefined;
  }
  const lines = (value as Record<string, unknown>).lines;
  if (!Array.isArray(lines)) {
    return undefined;
  }
  const parsedLines = lines.map(parseDiffHunkLine);
  return parsedLines.some((line) => line === undefined)
    ? undefined
    : { lines: parsedLines as DiffHunkLine[] };
}

/** Extracts `hunks` from a diff content block's `_meta.klorb.diffHunks`, or `undefined` if
 * absent or malformed -- a client talking to a non-klorb ACP agent (or an older klorb server)
 * simply won't have this, and falls back to the block's own `oldText`/`newText`. */
function parseDiffHunksMeta(
  meta: { [key: string]: unknown } | null | undefined
): DiffHunk[] | undefined {
  if (meta == null || typeof meta.klorb !== 'object' || meta.klorb === null) {
    return undefined;
  }
  const diffHunks = (meta.klorb as Record<string, unknown>).diffHunks;
  if (!Array.isArray(diffHunks)) {
    return undefined;
  }
  const parsed = diffHunks.map(parseDiffHunk);
  return parsed.some((hunk) => hunk === undefined) ? undefined : (parsed as DiffHunk[]);
}

function isDiffContent(
  block: ToolCallContent
): block is Extract<ToolCallContent, { type: 'diff' }> {
  return block.type === 'diff';
}

function toDiffMessage(content: Extract<ToolCallContent, { type: 'diff' }>): ToolCallDiff {
  const hunks = parseDiffHunksMeta(content._meta);
  return {
    path: content.path,
    oldText: content.oldText ?? null,
    newText: content.newText,
    ...(hunks !== undefined ? { hunks } : {}),
  };
}

function toolCallStartedMessage(update: ToolCall): ToolCallStartedMessage {
  return {
    type: 'toolCallStarted',
    callId: update.toolCallId,
    title: update.title,
    kind: update.kind ?? 'other',
    locations: (update.locations ?? []).map(toLocationMessage),
  };
}

/** Klorb's own server sends exactly one content block per `tool_call_update` (see
 * docs/specs/klorb-server.md's tool-call update mapping section) -- the first `diff` block, if
 * any, wins; otherwise the first `text` block becomes `contentText`. Any other/additional block
 * is ignored rather than erroring, since a non-klorb agent's update shape isn't this client's
 * contract to enforce. */
function toolCallUpdatedMessage(update: ToolCallUpdate): ToolCallUpdatedMessage {
  const message: ToolCallUpdatedMessage = {
    type: 'toolCallUpdated',
    callId: update.toolCallId,
    status: update.status ?? 'completed',
  };
  if (update.title != null) {
    message.title = update.title;
  }
  if (update.locations != null) {
    message.locations = update.locations.map(toLocationMessage);
  }
  const diffBlock = update.content?.find(isDiffContent);
  if (diffBlock !== undefined) {
    message.diff = toDiffMessage(diffBlock);
  } else {
    const textBlock = update.content?.find(
      (block) => block.type === 'content' && block.content.type === 'text'
    );
    if (
      textBlock !== undefined &&
      textBlock.type === 'content' &&
      textBlock.content.type === 'text'
    ) {
      message.contentText = textBlock.content.text;
    }
  }
  return message;
}

/** Logs a diagnostic line; injectable so tests can capture what would hit the console. */
export type LogFn = (message: string) => void;

/**
 * The klorb VS Code extension's implementation of the ACP SDK's `Client` interface: the
 * handler for requests and notifications the `klorb server` agent sends back over the
 * connection. Constructed fresh by each `AcpConnection.start()` alongside the SDK connection
 * it serves. This checkpoint dispatches `agent_message_chunk`/`agent_thought_chunk` session
 * updates to the listener as streamed text, and `tool_call`/`tool_call_update` updates as
 * flattened `ToolCallStarted`/`ToolCallUpdated` messages (see `toolCallStartedMessage`/
 * `toolCallUpdatedMessage`); it answers every permission ask with the first reject option, and
 * the fs/terminal methods fail with JSON-RPC method-not-found since the client never advertises
 * those capabilities.
 */
export class KlorbAcpClient {
  private readonly _listener: SessionUpdateListener;
  private readonly _requestError: RequestErrorClass;
  private readonly _log: LogFn;

  public constructor(
    listener: SessionUpdateListener,
    requestError: RequestErrorClass,
    log: LogFn = (message: string) => console.warn(message)
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
      case 'tool_call':
        this._listener.onToolCallStarted(toolCallStartedMessage(update));
        break;
      case 'tool_call_update':
        this._listener.onToolCallUpdated(toolCallUpdatedMessage(update));
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
    params: RequestPermissionRequest
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
        `with option "${rejectOption.name}" (interactive approvals not implemented yet)`
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
