// © Copyright 2026 Aaron Kimball
import { Fragment, type JSX } from 'react';

import type { ChatMessageInfo, SubagentNodeInfo } from 'shared/webviewMessages';

import { CHAT_USER_ID, chatDisplayName, findChatMentionSpans } from '../chatRoomModel';

export interface ChatMessageEntryProps {
  message: ChatMessageInfo;
  nodes: readonly SubagentNodeInfo[];
  /** Selects the mentioned participant's own row. */
  onSelectParticipant(sessionId: string): void;
}

function formatTime(timestamp: string): string {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime())
    ? ''
    : date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

/** Renders `body` with every resolved `@mention` wrapped in a `.mention-chip` span showing its
 * live display name. An unresolved mention is left as its original raw text, unstyled. */
function ChatMessageBody({
  body,
  nodes,
  onSelectParticipant,
}: {
  body: string;
  nodes: readonly SubagentNodeInfo[];
  onSelectParticipant(sessionId: string): void;
}): JSX.Element {
  const spans = findChatMentionSpans(body, nodes);
  if (spans.length === 0) {
    return <>{body}</>;
  }
  const pieces: JSX.Element[] = [];
  let cursor = 0;
  spans.forEach((span, index) => {
    if (span.start > cursor) {
      pieces.push(<Fragment key={`text-${index}`}>{body.slice(cursor, span.start)}</Fragment>);
    }
    if (span.resolvedId === undefined) {
      pieces.push(<Fragment key={`mention-${index}`}>{body.slice(span.start, span.end)}</Fragment>);
    } else {
      const resolvedId = span.resolvedId;
      const clickable = resolvedId !== CHAT_USER_ID;
      pieces.push(
        <span
          className="mention-chip"
          key={`mention-${index}`}
          role={clickable ? 'button' : undefined}
          style={clickable ? undefined : { cursor: 'default' }}
          onClick={clickable ? () => onSelectParticipant(resolvedId) : undefined}>
          <strong>@{chatDisplayName(resolvedId, nodes)}</strong>
        </span>
      );
    }
    cursor = span.end;
  });
  if (cursor < body.length) {
    pieces.push(<Fragment key="text-end">{body.slice(cursor)}</Fragment>);
  }
  return <>{pieces}</>;
}

/** One IRC-style chat room row: `[HH:MM] <sender>: <body>`, left-justified full-width. */
export default function ChatMessageEntry({
  message,
  nodes,
  onSelectParticipant,
}: ChatMessageEntryProps): JSX.Element {
  const isOwn = message.senderId === CHAT_USER_ID;
  return (
    <div className={`chat-message${isOwn ? ' chat-message-own' : ''}`}>
      <span className="chat-message-time">[{formatTime(message.timestamp)}]</span>{' '}
      <strong>{chatDisplayName(message.senderId, nodes)}</strong>:{' '}
      <ChatMessageBody
        body={message.body}
        nodes={nodes}
        onSelectParticipant={onSelectParticipant}
      />
    </div>
  );
}
