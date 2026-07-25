// © Copyright 2026 Aaron Kimball

/**
 * The typed message protocol between the extension host and the webview, shared by both
 * tsconfigs (the host's tsconfig.json and the webview's tsconfig.webview.json). The webview
 * never speaks ACP: the host translates ACP session updates into `HostMessage`s and webview
 * user intent (`WebviewMessage`s) into ACP requests. Later increments extend these two
 * unions rather than inventing ad hoc message shapes.
 */

/** A new prompt turn began (the user's prompt was accepted and sent to the server). */
export interface TurnStartedMessage {
  type: 'turnStarted';
}

/** A streamed piece of the agent's response text for the current turn. */
export interface AgentChunkMessage {
  type: 'agentChunk';
  text: string;
}

/** A streamed piece of the agent's thinking text for the current turn. */
export interface ThoughtChunkMessage {
  type: 'thoughtChunk';
  text: string;
}

/** The current turn finished; `stopReason` is the ACP stop reason string (e.g. "end_turn",
 * "cancelled"). */
export interface TurnEndedMessage {
  type: 'turnEnded';
  stopReason: string;
}

/** The current turn (or an attempt to start one) failed with an error. */
export interface TurnErrorMessage {
  type: 'turnError';
  message: string;
}

/** The conversation was reset (a fresh session replaced the old one); clear the history. */
export interface SessionResetMessage {
  type: 'sessionReset';
}

/** One file location a tool call names, e.g. the file a read/edit call touched. */
export interface ToolCallLocation {
  path: string;
  line?: number;
}

/** One rendered line of a tool-call diff hunk -- mirrors `klorb.tools.util.diff_lines.DiffLine`
 * (see docs/specs/klorb-server.md's tool-call update mapping section), with its `old_lineno`/
 * `new_lineno` fields renamed to camelCase at this host/webview boundary. */
export interface DiffHunkLine {
  kind: 'context' | 'add' | 'del';
  oldLineno: number | null;
  newLineno: number | null;
  text: string;
}

/** A contiguous run of `DiffHunkLine`s, mirroring `klorb.tools.util.diff_lines.DiffHunk`. */
export interface DiffHunk {
  lines: DiffHunkLine[];
}

/** A tool call's diff content, flattened from an ACP `diff` content block: `oldText`/`newText`
 * are always present (ACP's own convention, `oldText: null` for a brand-new file); `hunks` is
 * present only when the server additionally attached `_meta.klorb.diffHunks` (klorb's own
 * server always does), and is preferred for rendering a colored gutter view when present. */
export interface ToolCallDiff {
  path: string;
  oldText: string | null;
  newText: string;
  hunks?: DiffHunk[];
}

/** A tool call was just started (ACP `tool_call` update): `kind` and `status` are left as
 * plain strings (mirroring `TurnEndedMessage.stopReason`) rather than a literal union, so an
 * ACP `ToolKind`/`ToolCallStatus` value this client doesn't recognize yet still round-trips
 * instead of failing to parse. */
export interface ToolCallStartedMessage {
  type: 'toolCallStarted';
  callId: string;
  title: string;
  kind: string;
  locations: ToolCallLocation[];
}

/** A tool call finished or otherwise changed (ACP `tool_call_update`); mutates the matching
 * history entry in place. */
export interface ToolCallUpdatedMessage {
  type: 'toolCallUpdated';
  callId: string;
  status: string;
  title?: string;
  contentText?: string;
  diff?: ToolCallDiff;
  locations?: ToolCallLocation[];
}

/** Every message the extension host may post to the webview. */
export type HostMessage =
  | TurnStartedMessage
  | AgentChunkMessage
  | ThoughtChunkMessage
  | TurnEndedMessage
  | TurnErrorMessage
  | SessionResetMessage
  | ToolCallStartedMessage
  | ToolCallUpdatedMessage;

/** The user submitted a prompt from the input box. */
export interface SubmitPromptMessage {
  type: 'submitPrompt';
  text: string;
}

/** The user asked to cancel the in-flight turn (Stop button or Escape). */
export interface CancelTurnMessage {
  type: 'cancelTurn';
}

/** The user clicked a tool-call location link: open that file (at `line`, if given) in the
 * editor. */
export interface OpenLocationMessage {
  type: 'openLocation';
  path: string;
  line?: number;
}

/** The user clicked a tool call's "Open diff" action. */
export interface OpenDiffMessage {
  type: 'openDiff';
  callId: string;
  path: string;
}

/** Every message the webview may post to the extension host. */
export type WebviewMessage =
  SubmitPromptMessage | CancelTurnMessage | OpenLocationMessage | OpenDiffMessage;

/** Message `type` values that carry a required string field, keyed by the field's name. */
interface FieldSpec {
  field: 'text' | 'message' | 'stopReason';
  types: readonly string[];
}

const HOST_FIELD_SPECS: readonly FieldSpec[] = [
  { field: 'text', types: ['agentChunk', 'thoughtChunk'] },
  { field: 'stopReason', types: ['turnEnded'] },
  { field: 'message', types: ['turnError'] },
];

const HOST_BARE_TYPES: readonly string[] = ['turnStarted', 'sessionReset'];

const WEBVIEW_FIELD_SPECS: readonly FieldSpec[] = [{ field: 'text', types: ['submitPrompt'] }];

const WEBVIEW_BARE_TYPES: readonly string[] = ['cancelTurn'];

function parseMessage(
  data: unknown,
  fieldSpecs: readonly FieldSpec[],
  bareTypes: readonly string[]
): Record<string, unknown> | undefined {
  if (typeof data !== 'object' || data === null) {
    return undefined;
  }
  const record = data as Record<string, unknown>;
  const type = record.type;
  if (typeof type !== 'string') {
    return undefined;
  }
  if (bareTypes.includes(type)) {
    return record;
  }
  const spec = fieldSpecs.find((candidate) => candidate.types.includes(type));
  if (spec === undefined || typeof record[spec.field] !== 'string') {
    return undefined;
  }
  return record;
}

function isToolCallLocation(value: unknown): value is ToolCallLocation {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  return typeof v.path === 'string' && (v.line === undefined || typeof v.line === 'number');
}

function isToolCallLocationArray(value: unknown): value is ToolCallLocation[] {
  return Array.isArray(value) && value.every(isToolCallLocation);
}

function isDiffHunkLine(value: unknown): value is DiffHunkLine {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    (v.kind === 'context' || v.kind === 'add' || v.kind === 'del') &&
    (v.oldLineno === null || typeof v.oldLineno === 'number') &&
    (v.newLineno === null || typeof v.newLineno === 'number') &&
    typeof v.text === 'string'
  );
}

function isDiffHunk(value: unknown): value is DiffHunk {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const lines = (value as Record<string, unknown>).lines;
  return Array.isArray(lines) && lines.every(isDiffHunkLine);
}

function isToolCallDiff(value: unknown): value is ToolCallDiff {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  if (typeof v.path !== 'string' || typeof v.newText !== 'string') {
    return false;
  }
  if (v.oldText !== null && typeof v.oldText !== 'string') {
    return false;
  }
  return v.hunks === undefined || (Array.isArray(v.hunks) && v.hunks.every(isDiffHunk));
}

function parseToolCallStarted(record: Record<string, unknown>): ToolCallStartedMessage | undefined {
  if (
    typeof record.callId === 'string' &&
    typeof record.title === 'string' &&
    typeof record.kind === 'string' &&
    isToolCallLocationArray(record.locations)
  ) {
    return record as unknown as ToolCallStartedMessage;
  }
  return undefined;
}

function parseToolCallUpdated(record: Record<string, unknown>): ToolCallUpdatedMessage | undefined {
  if (
    typeof record.callId !== 'string' ||
    typeof record.status !== 'string' ||
    (record.title !== undefined && typeof record.title !== 'string') ||
    (record.contentText !== undefined && typeof record.contentText !== 'string') ||
    (record.diff !== undefined && !isToolCallDiff(record.diff)) ||
    (record.locations !== undefined && !isToolCallLocationArray(record.locations))
  ) {
    return undefined;
  }
  return record as unknown as ToolCallUpdatedMessage;
}

function parseOpenLocation(record: Record<string, unknown>): OpenLocationMessage | undefined {
  if (
    typeof record.path === 'string' &&
    (record.line === undefined || typeof record.line === 'number')
  ) {
    return record as unknown as OpenLocationMessage;
  }
  return undefined;
}

function parseOpenDiff(record: Record<string, unknown>): OpenDiffMessage | undefined {
  if (typeof record.callId === 'string' && typeof record.path === 'string') {
    return record as unknown as OpenDiffMessage;
  }
  return undefined;
}

/** Narrows an untyped `postMessage` payload to a `HostMessage`, or `undefined` if it isn't one.
 * `toolCallStarted`/`toolCallUpdated` carry nested arrays/objects the `FieldSpec` mechanism
 * above can't express, so they're validated by dedicated guards instead. */
export function parseHostMessage(data: unknown): HostMessage | undefined {
  const simple = parseMessage(data, HOST_FIELD_SPECS, HOST_BARE_TYPES);
  if (simple !== undefined) {
    return simple as unknown as HostMessage;
  }
  if (typeof data !== 'object' || data === null) {
    return undefined;
  }
  const record = data as Record<string, unknown>;
  switch (record.type) {
    case 'toolCallStarted':
      return parseToolCallStarted(record);
    case 'toolCallUpdated':
      return parseToolCallUpdated(record);
    default:
      return undefined;
  }
}

/** Narrows an untyped `postMessage` payload to a `WebviewMessage`, or `undefined` if it isn't
 * one. */
export function parseWebviewMessage(data: unknown): WebviewMessage | undefined {
  const simple = parseMessage(data, WEBVIEW_FIELD_SPECS, WEBVIEW_BARE_TYPES);
  if (simple !== undefined) {
    return simple as unknown as WebviewMessage;
  }
  if (typeof data !== 'object' || data === null) {
    return undefined;
  }
  const record = data as Record<string, unknown>;
  switch (record.type) {
    case 'openLocation':
      return parseOpenLocation(record);
    case 'openDiff':
      return parseOpenDiff(record);
    default:
      return undefined;
  }
}
