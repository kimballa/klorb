// © Copyright 2026 Aaron Kimball
import type { JSX } from 'react';

import type { ToolCallHistoryEntry } from '../historyModel';

export interface MessagingToolCallChipProps {
  entry: ToolCallHistoryEntry;
}

/** Shape of the JSON envelope that `default_tool_call_detail()` produces. */
interface DefaultToolCallDetail {
  name: string;
  args: Record<string, unknown>;
  result?: unknown;
  error?: string;
}

/** One message inside a `GetMessages` result. */
interface AgentMessage {
  sender_id: string;
  sender_role: string;
  body: string;
}

/** Try to parse `contentText` as `default_tool_call_detail` JSON; returns `undefined` when
 * the text is absent, not valid JSON, or doesn't have the expected `{name, args, ...}` shape. */
function parseDetailJson(contentText: string | undefined): DefaultToolCallDetail | undefined {
  if (contentText === undefined) {
    return undefined;
  }
  try {
    const parsed: unknown = JSON.parse(contentText);
    if (
      typeof parsed === 'object' &&
      parsed !== null &&
      'name' in parsed &&
      'args' in parsed &&
      typeof (parsed as Record<string, unknown>).name === 'string' &&
      typeof (parsed as Record<string, unknown>).args === 'object' &&
      (parsed as Record<string, unknown>).args !== null
    ) {
      return parsed as DefaultToolCallDetail;
    }
  } catch {
    // Not JSON — fall through to raw-text rendering.
  }
  return undefined;
}

/** Renders the structured detail for a `SendMessage` call: target agent id and message body. */
function SendMessageDetail({ detail }: { detail: DefaultToolCallDetail }): JSX.Element {
  const targetId = typeof detail.args.id === 'string' ? detail.args.id : '(unknown)';
  const message = typeof detail.args.message === 'string' ? detail.args.message : '';
  return (
    <div className="messaging-detail messaging-detail-send">
      <div className="messaging-detail-field">
        <span className="messaging-detail-label">To: </span>
        <span className="messaging-detail-value">{targetId}</span>
      </div>
      <div className="messaging-detail-field">
        <span className="messaging-detail-label">Message: </span>
        <pre className="messaging-detail-body">{message}</pre>
      </div>
      {detail.error !== undefined && (
        <div className="messaging-detail-field messaging-detail-error">
          <span className="messaging-detail-label">Error: </span>
          <span className="messaging-detail-value">{detail.error}</span>
        </div>
      )}
    </div>
  );
}

/** Type guard for a well-formed `AgentMessage` entry. */
function isAgentMessage(value: unknown): value is AgentMessage {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as Record<string, unknown>).sender_id === 'string' &&
    typeof (value as Record<string, unknown>).sender_role === 'string' &&
    typeof (value as Record<string, unknown>).body === 'string'
  );
}

/** Renders the structured detail for a `GetMessages` call: message count plus each message as
 * a native `<details>` with a summary showing sender/role and the body inside. */
function GetMessagesDetail({ detail }: { detail: DefaultToolCallDetail }): JSX.Element {
  const result = detail.result;
  const rawMessages: unknown =
    typeof result === 'object' && result !== null && 'messages' in result
      ? (result as Record<string, unknown>).messages
      : [];
  const messages: AgentMessage[] = Array.isArray(rawMessages)
    ? rawMessages.filter(isAgentMessage)
    : [];
  return (
    <div className="messaging-detail messaging-detail-get">
      <div className="messaging-detail-count">
        {messages.length} {messages.length === 1 ? 'message' : 'messages'}
      </div>
      {messages.map((msg, index) => (
        <details key={index} className="messaging-message">
          <summary className="messaging-message-summary">
            <span className="messaging-message-sender">{msg.sender_id}</span>
            <span className="messaging-message-role">({msg.sender_role})</span>
          </summary>
          <pre className="messaging-message-body">{msg.body}</pre>
        </details>
      ))}
      {detail.error !== undefined && (
        <div className="messaging-detail-field messaging-detail-error">
          <span className="messaging-detail-label">Error: </span>
          <span className="messaging-detail-value">{detail.error}</span>
        </div>
      )}
    </div>
  );
}

/** A dedicated chip for SendMessage/GetMessages tool calls that renders with a native `<details>`
 * disclosure and a mail icon, showing the messaging result in a structured, readable format. */
export default function MessagingToolCallChip({ entry }: MessagingToolCallChipProps): JSX.Element {
  const statusIcon =
    entry.status === 'in_progress' ? (
      <vscode-progress-ring className="tool-call-icon" />
    ) : entry.status === 'failed' ? (
      <vscode-icon className="tool-call-icon tool-call-icon-error" name="error" />
    ) : (
      <vscode-icon className="tool-call-icon" name="mail" />
    );

  const detail = parseDetailJson(entry.contentText);

  return (
    <div className={`tool-call tool-call-${entry.status} messaging-tool-call`}>
      <details>
        <summary className="tool-call-header">
          {statusIcon}
          <span className="tool-call-title">{entry.title}</span>
        </summary>
        <div className="tool-call-detail">
          {detail !== undefined ? (
            detail.name === 'SendMessage' ? (
              <SendMessageDetail detail={detail} />
            ) : detail.name === 'GetMessages' ? (
              <GetMessagesDetail detail={detail} />
            ) : (
              <div className="tool-call-content-text">{entry.contentText}</div>
            )
          ) : entry.contentText !== undefined ? (
            <div className="tool-call-content-text">{entry.contentText}</div>
          ) : null}
        </div>
      </details>
    </div>
  );
}
