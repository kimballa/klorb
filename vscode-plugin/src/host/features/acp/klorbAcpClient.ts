// © Copyright 2026 Aaron Kimball
import type {
  CreateTerminalResponse,
  LoadSessionResponse,
  NewSessionResponse,
  PermissionOption,
  Plan,
  PlanEntry,
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
  SessionReplayEntry,
  TaskInfo,
  TaskListSummary,
  TaskListUpdateMessage,
  ToolCallDiff,
  ToolCallLocation,
  ToolCallStartedMessage,
  ToolCallUpdatedMessage,
} from 'shared/webviewMessages';

/** The `RequestError` class from the loaded ACP SDK module. Passed in as a value because the
 * SDK is ESM-only and the extension host is CommonJS — the module object arrives via the one
 * dynamic `import()` in `AcpConnection.start()` (see src/host/features/acp/acpConnection.ts). */
export type RequestErrorClass = (typeof import('@agentclientprotocol/sdk'))['RequestError'];

/** The session's initial control-plane state, captured from `session/new`'s response (its
 * `modes.currentModeId` and `_meta.klorb.workspace`/`.title`) -- see
 * `sessionInfoFromResponse()`. A field is `undefined` when the response didn't carry it (e.g.
 * a peer ACP agent that doesn't advertise `modes` at all); `title` is additionally `null` for
 * "no title yet", distinct from `undefined` for "the response didn't say".
 *
 * `enqueueMessageCapable` is not part of `session/new`'s own response -- it's the connection's
 * `initialize()`-negotiated `agentCapabilities._meta.klorb.enqueueMessage` flag, threaded
 * through here by `AcpConnection` (which keeps it for the connection's whole lifetime, since
 * capabilities don't change per session) so `SessionControls`/`StatusRow` learn it the same way
 * they learn everything else about the session's starting state. */
export interface SessionInfo {
  modeId: string | undefined;
  workspacePath: string | undefined;
  workspaceTrusted: boolean | undefined;
  title: string | null | undefined;
  enqueueMessageCapable: boolean;
}

/** Receives the streamed text, tool-call activity, and control-plane state the agent produces
 * over the life of a session. */
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
  /** `session/new`'s response just built (or replaced) the session -- its starting
   * permission mode, workspace trust state, and title, so the status row (and workspace
   * trust bridging) reflect the true starting state instead of guessing. */
  onSessionInfo(info: SessionInfo): void;
  /** The session's permission mode changed (`current_mode_update`), whether from this
   * client's own `session/set_mode` or (in principle) the agent changing it autonomously. */
  onModeChanged(modeId: string): void;
  /** The session's title changed (`session_info_update`'s `title` field). */
  onSessionTitleChanged(title: string | null): void;
  /** The session's chainlink-backed task list changed (`plan`). Replaces the task panel's
   * snapshot wholesale -- the server always sends every task on each update, never a delta. */
  onTaskListUpdate(message: TaskListUpdateMessage): void;
  /** The turn that just finished used `usedTokens` of `maxTokens`, and the session has
   * generated `outputTokens` in total (`_klorb/usage`, sent once per turn). */
  onUsageUpdate(usedTokens: number, maxTokens: number | null, outputTokens: number): void;
  /** A message submitted while a turn was in flight was accepted into the running turn
   * (`_klorb/messageQueued`) -- render it in italic "Queued message" styling. */
  onMessageQueued(text: string): void;
  /** A previously-queued message was actually delivered (`_klorb/queuedMessageSent`) -- flip
   * its rendering from queued to a regular delivered prompt. */
  onQueuedMessageSent(text: string): void;
  /** A `session/load` just restored a previously saved session's conversation
   * (`_klorb/sessionReplay`) -- replace the history wholesale with `entries`. */
  onSessionReplay(entries: SessionReplayEntry[]): void;
}

/** Extracts `SessionInfo` from a `session/new` response's `modes.currentModeId` and
 * `_meta.klorb.workspace`/`.title` -- see docs/specs/klorb-server.md's "Wire protocol" and
 * "Session naming and token usage" sections. `enqueueMessageCapable` isn't part of the
 * response itself; the caller (`AcpConnection`) threads through the connection's own
 * `initialize()`-negotiated value (see `SessionInfo`'s own doc comment). */
export function sessionInfoFromResponse(
  session: NewSessionResponse | LoadSessionResponse,
  enqueueMessageCapable: boolean
): SessionInfo {
  const meta = klorbMetaOf(session._meta);
  const workspace =
    typeof meta.workspace === 'object' && meta.workspace !== null
      ? (meta.workspace as Record<string, unknown>)
      : undefined;
  const title = meta.title;
  return {
    modeId: session.modes?.currentModeId,
    workspacePath: typeof workspace?.path === 'string' ? workspace.path : undefined,
    workspaceTrusted: typeof workspace?.trusted === 'boolean' ? workspace.trusted : undefined,
    title: title === null ? null : typeof title === 'string' ? title : undefined,
    enqueueMessageCapable,
  };
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

/** Flattens one ACP `PlanEntry` into a `TaskInfo`, preferring its own `_meta.klorb` detail
 * (`issueId`/`openBlockerCount`/`closed`/`isCurrentTask` -- see docs/specs/klorb-server.md's
 * "Chainlink task-plan updates" section) when the server attached it, else deriving
 * `closed`/`isCurrentTask` from `status` alone and reporting no `issueId`/`blocked` at all --
 * the degraded path for a stock (non-klorb) ACP agent's plain `PlanEntry`. */
function taskInfoFromEntry(entry: PlanEntry): TaskInfo {
  const meta = klorbMetaOf(entry._meta);
  const issueId = typeof meta.issueId === 'number' ? meta.issueId : undefined;
  const openBlockerCount =
    typeof meta.openBlockerCount === 'number' ? meta.openBlockerCount : undefined;
  const closed = typeof meta.closed === 'boolean' ? meta.closed : entry.status === 'completed';
  const isCurrentTask =
    typeof meta.isCurrentTask === 'boolean' ? meta.isCurrentTask : entry.status === 'in_progress';
  return {
    ...(issueId !== undefined ? { issueId } : {}),
    text: entry.content,
    priority: entry.priority,
    status: entry.status,
    blocked: openBlockerCount !== undefined && openBlockerCount > 0,
    isCurrentTask,
    closed,
  };
}

/** Builds the update-level `TaskListSummary`, preferring the `plan` update's own `_meta.klorb`
 * counts when present, else deriving them by counting `tasks` -- the same degrade a stock ACP
 * agent's plan (no `_meta.klorb` at all) falls back to. */
function taskListSummary(meta: Record<string, unknown>, tasks: TaskInfo[]): TaskListSummary {
  return {
    openCount:
      typeof meta.openCount === 'number'
        ? meta.openCount
        : tasks.filter((task) => !task.closed).length,
    closedCount:
      typeof meta.closedCount === 'number'
        ? meta.closedCount
        : tasks.filter((task) => task.closed).length,
    blockedCount:
      typeof meta.blockedCount === 'number'
        ? meta.blockedCount
        : tasks.filter((task) => task.blocked).length,
    currentTaskId: typeof meta.currentTaskId === 'number' ? meta.currentTaskId : null,
  };
}

function taskListUpdateMessage(update: Plan): TaskListUpdateMessage {
  const tasks = update.entries.map(taskInfoFromEntry);
  return {
    type: 'taskListUpdate',
    summary: taskListSummary(klorbMetaOf(update._meta), tasks),
    tasks,
  };
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
      case 'current_mode_update':
        this._listener.onModeChanged(update.currentModeId);
        break;
      case 'session_info_update':
        if (update.title !== undefined) {
          this._listener.onSessionTitleChanged(update.title);
        }
        break;
      case 'plan':
        this._listener.onTaskListUpdate(taskListUpdateMessage(update));
        break;
      default:
        this._log(`klorb: ignoring unhandled session update: ${update.sessionUpdate}`);
        break;
    }
  }

  /** Handles a `_klorb/*` extension notification -- today just `_klorb/usage`, sent once per
   * turn (see docs/specs/klorb-server.md's extension-methods section). An unrecognized
   * extension notification is logged and ignored, per ACP's own extensibility rules. */
  public async extNotification(method: string, params: Record<string, unknown>): Promise<void> {
    if (method === '_klorb/usage') {
      const { usedTokens, maxTokens, outputTokens } = params;
      if (
        typeof usedTokens === 'number' &&
        (maxTokens === null || typeof maxTokens === 'number') &&
        typeof outputTokens === 'number'
      ) {
        this._listener.onUsageUpdate(usedTokens, maxTokens ?? null, outputTokens);
      } else {
        this._log(`klorb: malformed _klorb/usage params: ${JSON.stringify(params)}`);
      }
      return;
    }
    if (method === '_klorb/messageQueued' || method === '_klorb/queuedMessageSent') {
      const text = params.text;
      if (typeof text !== 'string') {
        this._log(`klorb: malformed ${method} params: ${JSON.stringify(params)}`);
        return;
      }
      if (method === '_klorb/messageQueued') {
        this._listener.onMessageQueued(text);
      } else {
        this._listener.onQueuedMessageSent(text);
      }
      return;
    }
    if (method === '_klorb/sessionReplay') {
      const entries = params.entries;
      if (!Array.isArray(entries)) {
        this._log(`klorb: malformed _klorb/sessionReplay params: ${JSON.stringify(params)}`);
        return;
      }
      this._listener.onSessionReplay(entries as SessionReplayEntry[]);
      return;
    }
    this._log(`klorb: ignoring unrecognized ext notification: ${method}`);
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
