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

/** Every message the extension host may post to the webview. */
export type HostMessage =
  | TurnStartedMessage
  | AgentChunkMessage
  | ThoughtChunkMessage
  | TurnEndedMessage
  | TurnErrorMessage
  | SessionResetMessage;

/** The user submitted a prompt from the input box. */
export interface SubmitPromptMessage {
  type: 'submitPrompt';
  text: string;
}

/** The user asked to cancel the in-flight turn (Stop button or Escape). */
export interface CancelTurnMessage {
  type: 'cancelTurn';
}

/** Every message the webview may post to the extension host. */
export type WebviewMessage = SubmitPromptMessage | CancelTurnMessage;

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
  bareTypes: readonly string[],
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

/** Narrows an untyped `postMessage` payload to a `HostMessage`, or `undefined` if it isn't one. */
export function parseHostMessage(data: unknown): HostMessage | undefined {
  return parseMessage(data, HOST_FIELD_SPECS, HOST_BARE_TYPES) as HostMessage | undefined;
}

/** Narrows an untyped `postMessage` payload to a `WebviewMessage`, or `undefined` if it isn't
 * one. */
export function parseWebviewMessage(data: unknown): WebviewMessage | undefined {
  return parseMessage(data, WEBVIEW_FIELD_SPECS, WEBVIEW_BARE_TYPES) as WebviewMessage | undefined;
}
