// © Copyright 2026 Aaron Kimball
import type {
  CreateTerminalResponse,
  PermissionOption,
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
  PermissionAskMessage,
  PermissionAskOption,
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
  /** Posts a permission ask to the webview, or re-posts an already-outstanding one after a
   * webview reload (see `KlorbAcpClient.repostPendingAsk()`). Fire-and-forget: the eventual
   * decision arrives back through `KlorbAcpClient.resolvePermissionDecision()`, not this call's
   * return value. */
  postPermissionAsk(message: PermissionAskMessage): void;
}

/** The user's decision on a permission ask, normalized from a `permissionDecision` webview
 * message into the shape `KlorbAcpClient` needs to build the ACP response (see
 * docs/specs/klorb-server.md's decision-mapping section). */
export type PermissionDecisionResult =
  { cancelled: true } | { optionId: string; otherText?: string };

/** Answers the `_klorb/raiseToolCallLimit` ext method's modal prompt; `true` doubles the
 * reached tool-call cap and lets the call proceed, mirroring the TUI's own confirmation. */
export type RaiseToolCallLimitFn = (message: string) => Promise<boolean>;

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

/** Extracts an ACP `_meta.klorb` payload, or `{}` if absent -- every klorb-specific field
 * (`resourceDescription`, `commandText`, `escalation`, an option's own `scope`, ...) rides
 * under this one namespaced key (see docs/specs/klorb-server.md's extensibility rules). */
function klorbMetaOf(meta: { [key: string]: unknown } | null | undefined): Record<string, unknown> {
  if (meta == null || typeof meta.klorb !== 'object' || meta.klorb === null) {
    return {};
  }
  return meta.klorb as Record<string, unknown>;
}

function toPermissionAskOption(option: PermissionOption): PermissionAskOption {
  const scope = klorbMetaOf(option._meta).scope;
  return {
    id: option.optionId,
    name: option.name,
    kind: option.kind,
    ...(typeof scope === 'string' ? { scope } : {}),
  };
}

function permissionAskMessage(
  params: RequestPermissionRequest,
  requestId: number
): PermissionAskMessage {
  return {
    type: 'permissionAsk',
    requestId,
    title: params.toolCall.title ?? '',
    options: params.options.map(toPermissionAskOption),
    klorbMeta: klorbMetaOf(params._meta),
  };
}

function permissionResponseFromDecision(
  decision: PermissionDecisionResult
): RequestPermissionResponse {
  if ('cancelled' in decision) {
    return { outcome: { outcome: 'cancelled' } };
  }
  return {
    outcome: {
      outcome: 'selected',
      optionId: decision.optionId,
      ...(decision.otherText !== undefined
        ? { _meta: { klorb: { otherText: decision.otherText } } }
        : {}),
    },
  };
}

/** Logs a diagnostic line; injectable so tests can capture what would hit the console. */
export type LogFn = (message: string) => void;

/**
 * The klorb VS Code extension's implementation of the ACP SDK's `Client` interface: the
 * handler for requests and notifications the `klorb server` agent sends back over the
 * connection. Constructed fresh by each `AcpConnection.start()` alongside the SDK connection
 * it serves. Dispatches `agent_message_chunk`/`agent_thought_chunk` session updates to the
 * listener as streamed text, and `tool_call`/`tool_call_update` updates as flattened
 * `ToolCallStarted`/`ToolCallUpdated` messages (see `toolCallStartedMessage`/
 * `toolCallUpdatedMessage`); `requestPermission()` posts a `permissionAsk` through the listener
 * and awaits the matching decision (see `_ask`/`resolvePermissionDecision`), and `extMethod()`
 * answers `_klorb/raiseToolCallLimit` via the injected `RaiseToolCallLimitFn`. The fs/terminal
 * methods fail with JSON-RPC method-not-found since the client never advertises those
 * capabilities.
 */
export class KlorbAcpClient {
  private readonly _listener: SessionUpdateListener;
  private readonly _requestError: RequestErrorClass;
  private readonly _log: LogFn;
  private readonly _raiseToolCallLimit: RaiseToolCallLimitFn;
  private _nextAskRequestId = 1;
  private _askBusy = false;
  private _askQueue: Array<() => void> = [];
  private _pendingAsk:
    | { message: PermissionAskMessage; resolve: (decision: PermissionDecisionResult) => void }
    | undefined;

  public constructor(
    listener: SessionUpdateListener,
    requestError: RequestErrorClass,
    log: LogFn = (message: string) => console.warn(message),
    raiseToolCallLimit: RaiseToolCallLimitFn = () => Promise.resolve(false)
  ) {
    this._listener = listener;
    this._requestError = requestError;
    this._log = log;
    this._raiseToolCallLimit = raiseToolCallLimit;
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
   * Posts a permission ask (or `EscalatePrivileges` ask, distinguished by
   * `klorbMeta.escalation` -- see docs/specs/klorb-server.md) to the webview via the listener
   * and resolves once a matching `permissionDecision` arrives through
   * `resolvePermissionDecision()`. Calls are served strictly one at a time: a second concurrent
   * ask queues behind the first (via `_askBusy`/`_askQueue`) rather than posting a second
   * `ApprovalPanel` on top of the first, even though the server itself only ever asks one at a
   * time in practice. The first (non-concurrent) ask posts synchronously, within this call.
   */
  public requestPermission(params: RequestPermissionRequest): Promise<RequestPermissionResponse> {
    return new Promise<RequestPermissionResponse>((resolve) => {
      const run = (): void => {
        void this._ask(params).then((decision) => {
          this._askBusy = false;
          resolve(permissionResponseFromDecision(decision));
          this._askQueue.shift()?.();
        });
      };
      if (this._askBusy) {
        this._askQueue.push(run);
      } else {
        this._askBusy = true;
        run();
      }
    });
  }

  private _ask(params: RequestPermissionRequest): Promise<PermissionDecisionResult> {
    const requestId = this._nextAskRequestId++;
    const message = permissionAskMessage(params, requestId);
    return new Promise<PermissionDecisionResult>((resolve) => {
      this._pendingAsk = { message, resolve };
      this._listener.postPermissionAsk(message);
    });
  }

  /** Resolves the outstanding ask named `requestId`, if any -- called when the webview posts
   * back a `permissionDecision`. A decision naming a stale/unknown `requestId` (e.g. a
   * duplicate post after a reload race) is logged and ignored rather than resolving the wrong
   * ask. */
  public resolvePermissionDecision(requestId: number, decision: PermissionDecisionResult): void {
    if (this._pendingAsk === undefined || this._pendingAsk.message.requestId !== requestId) {
      this._log(`klorb: ignoring permission decision for unknown request ${requestId}`);
      return;
    }
    const { resolve } = this._pendingAsk;
    this._pendingAsk = undefined;
    resolve(decision);
  }

  /** Re-posts the currently outstanding ask, if any, to the webview -- called when the webview
   * is recreated (e.g. after a reload) while an ask from before the reload is still awaiting an
   * answer, so the fresh webview instance can render it instead of leaving it stuck invisible. */
  public repostPendingAsk(): void {
    if (this._pendingAsk !== undefined) {
      this._listener.postPermissionAsk(this._pendingAsk.message);
    }
  }

  public async extMethod(
    method: string,
    params: Record<string, unknown>
  ): Promise<Record<string, unknown>> {
    if (method === '_klorb/raiseToolCallLimit') {
      const message = typeof params.message === 'string' ? params.message : '';
      const approved = await this._raiseToolCallLimit(message);
      return { approved };
    }
    throw this._requestError.methodNotFound(method);
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
