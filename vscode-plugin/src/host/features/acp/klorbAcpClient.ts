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
  QuestionAskMessage,
  QuestionAskOption,
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
   * webview reload (see `KlorbAcpClient.repostPendingInteraction()`). Fire-and-forget: the
   * eventual decision arrives back through `KlorbAcpClient.resolvePermissionDecision()`, not
   * this call's return value. */
  postPermissionAsk(message: PermissionAskMessage): void;
  /** Posts a `_klorb/askUserQuestions` ask to the webview, or re-posts an already-outstanding
   * one after a webview reload (see `KlorbAcpClient.repostPendingInteraction()`).
   * Fire-and-forget: the eventual answer arrives back through
   * `KlorbAcpClient.resolveQuestionAnswer()`, not this call's return value. */
  postQuestionAsk(message: QuestionAskMessage): void;
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

/** The user's answer to a `_klorb/askUserQuestions` ask, normalized from a `questionAnswer`
 * webview message into the shape `KlorbAcpClient` needs to build the ext method's result (see
 * docs/specs/klorb-server.md's extension-method registry). */
export type QuestionAnswerResult =
  { cancelled: true } | { selectedOptionIndex: number } | { otherText: string };

function isQuestionAskOptionParam(value: unknown): value is QuestionAskOption {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    typeof v.label === 'string' &&
    (v.description === undefined || typeof v.description === 'string')
  );
}

/** Validates a `_klorb/askUserQuestions` ext request's raw params into a `QuestionAskMessage`
 * (minus `requestId`, assigned by the caller), or `undefined` if the server sent something this
 * client can't make sense of -- defensive against a future/buggy server, since ext method params
 * arrive as untyped `Record<string, unknown>`. */
function questionAskMessageFromParams(
  params: Record<string, unknown>
): Omit<QuestionAskMessage, 'requestId'> | undefined {
  const { header, question, options, index, total } = params;
  if (
    typeof header !== 'string' ||
    typeof question !== 'string' ||
    typeof index !== 'number' ||
    typeof total !== 'number' ||
    !Array.isArray(options) ||
    !options.every(isQuestionAskOptionParam)
  ) {
    return undefined;
  }
  return { type: 'questionAsk', header, question, options, index, total };
}

function questionAnswerResultToWire(result: QuestionAnswerResult): Record<string, unknown> {
  if ('cancelled' in result) {
    return { cancelled: true };
  }
  if ('otherText' in result) {
    return { otherText: result.otherText };
  }
  return { selectedOptionIndex: result.selectedOptionIndex };
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
 * `toolCallUpdatedMessage`); `requestPermission()` posts a `permissionAsk` and `extMethod()`'s
 * `_klorb/askUserQuestions` handling posts a `questionAsk`, both through the listener and both
 * sharing one "pending interaction" slot/queue (see `_pendingInteraction`/`_enqueueInteraction`)
 * since the server only ever has one blocking ask outstanding at a time regardless of kind.
 * `extMethod()` additionally answers `_klorb/raiseToolCallLimit` via the injected
 * `RaiseToolCallLimitFn`. The fs/terminal methods fail with JSON-RPC method-not-found since the
 * client never advertises those capabilities.
 */
export class KlorbAcpClient {
  private readonly _listener: SessionUpdateListener;
  private readonly _requestError: RequestErrorClass;
  private readonly _log: LogFn;
  private readonly _raiseToolCallLimit: RaiseToolCallLimitFn;
  private _nextRequestId = 1;
  private _interactionBusy = false;
  private _interactionQueue: Array<() => void> = [];
  private _pendingInteraction:
    | {
        kind: 'permission';
        message: PermissionAskMessage;
        resolve: (decision: PermissionDecisionResult) => void;
      }
    | {
        kind: 'question';
        message: QuestionAskMessage;
        resolve: (answer: QuestionAnswerResult) => void;
      }
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
   * `resolvePermissionDecision()`. Calls are served strictly one at a time, sharing the same
   * pending-interaction queue a `_klorb/askUserQuestions` ask uses (see `_enqueueInteraction()`)
   * rather than posting a second `ApprovalPanel` on top of the first, even though the server
   * itself only ever asks one thing at a time in practice. The first (non-concurrent) ask posts
   * synchronously, within this call.
   */
  public requestPermission(params: RequestPermissionRequest): Promise<RequestPermissionResponse> {
    return new Promise<RequestPermissionResponse>((resolve) => {
      this._enqueueInteraction(() => {
        const requestId = this._nextRequestId++;
        const message = permissionAskMessage(params, requestId);
        this._pendingInteraction = {
          kind: 'permission',
          message,
          resolve: (decision) => {
            this._finishInteraction();
            resolve(permissionResponseFromDecision(decision));
          },
        };
        this._listener.postPermissionAsk(message);
      });
    });
  }

  /** Resolves the outstanding ask named `requestId`, if any -- called when the webview posts
   * back a `permissionDecision`. A decision naming a stale/unknown `requestId` (e.g. a
   * duplicate post after a reload race) is logged and ignored rather than resolving the wrong
   * ask. */
  public resolvePermissionDecision(requestId: number, decision: PermissionDecisionResult): void {
    if (
      this._pendingInteraction === undefined ||
      this._pendingInteraction.kind !== 'permission' ||
      this._pendingInteraction.message.requestId !== requestId
    ) {
      this._log(`klorb: ignoring permission decision for unknown request ${requestId}`);
      return;
    }
    this._pendingInteraction.resolve(decision);
  }

  /** Resolves the outstanding question named `requestId`, if any -- called when the webview
   * posts back a `questionAnswer`. An answer naming a stale/unknown `requestId` is logged and
   * ignored, mirroring `resolvePermissionDecision()`. */
  public resolveQuestionAnswer(requestId: number, answer: QuestionAnswerResult): void {
    if (
      this._pendingInteraction === undefined ||
      this._pendingInteraction.kind !== 'question' ||
      this._pendingInteraction.message.requestId !== requestId
    ) {
      this._log(`klorb: ignoring question answer for unknown request ${requestId}`);
      return;
    }
    this._pendingInteraction.resolve(answer);
  }

  /** Re-posts the currently outstanding interaction (a permission ask or a question ask), if
   * any, to the webview -- called when the webview is recreated (e.g. after a reload) while an
   * interaction from before the reload is still awaiting an answer, so the fresh webview
   * instance can render it instead of leaving it stuck invisible. */
  public repostPendingInteraction(): void {
    if (this._pendingInteraction === undefined) {
      return;
    }
    if (this._pendingInteraction.kind === 'permission') {
      this._listener.postPermissionAsk(this._pendingInteraction.message);
    } else {
      this._listener.postQuestionAsk(this._pendingInteraction.message);
    }
  }

  /** Runs `run` immediately if no interaction is outstanding, else queues it behind the current
   * one -- the shared serialization a permission ask and a question ask both go through, so at
   * most one is ever posted to the webview at a time. */
  private _enqueueInteraction(run: () => void): void {
    if (this._interactionBusy) {
      this._interactionQueue.push(run);
    } else {
      this._interactionBusy = true;
      run();
    }
  }

  /** Clears the current pending interaction and starts the next queued one, if any -- called
   * from a resolved interaction's own `resolve` before it settles its promise. */
  private _finishInteraction(): void {
    this._pendingInteraction = undefined;
    this._interactionBusy = false;
    this._interactionQueue.shift()?.();
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
    if (method === '_klorb/askUserQuestions') {
      return this._askUserQuestions(params);
    }
    throw this._requestError.methodNotFound(method);
  }

  private _askUserQuestions(params: Record<string, unknown>): Promise<Record<string, unknown>> {
    const partial = questionAskMessageFromParams(params);
    if (partial === undefined) {
      this._log(`klorb: malformed _klorb/askUserQuestions params: ${JSON.stringify(params)}`);
      return Promise.resolve({ cancelled: true });
    }
    return new Promise<Record<string, unknown>>((resolve) => {
      this._enqueueInteraction(() => {
        const message: QuestionAskMessage = { ...partial, requestId: this._nextRequestId++ };
        this._pendingInteraction = {
          kind: 'question',
          message,
          resolve: (answer) => {
            this._finishInteraction();
            resolve(questionAnswerResultToWire(answer));
          },
        };
        this._listener.postQuestionAsk(message);
      });
    });
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
