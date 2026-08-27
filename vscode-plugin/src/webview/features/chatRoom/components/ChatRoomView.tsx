// © Copyright 2026 Aaron Kimball
import { type JSX, useEffect } from 'react';
import { Virtuoso, type Components, type ContextProp, type ItemProps } from 'react-virtuoso';

import type { ChatMessageInfo, SubagentNodeInfo } from 'shared/webviewMessages';
import usePinnedScroll from 'webview/hooks/usePinnedScroll';

import ChatMessageEntry from './ChatMessageEntry';

export interface ChatRoomViewProps {
  messages: ChatMessageInfo[];
  nodes: readonly SubagentNodeInfo[];
  onSelectParticipant(sessionId: string): void;
}

function ChatRoomItem({
  item: _item,
  context: _context,
  ...rest
}: ItemProps<ChatMessageInfo> & ContextProp<unknown>): JSX.Element {
  return <div {...rest} className="chat-room-item" />;
}

const CHAT_ROOM_COMPONENTS: Components<ChatMessageInfo> = { Item: ChatRoomItem };

/** Pixels of extra content Virtuoso keeps mounted outside the visible viewport. */
const OVERSCAN_PX = 600;

/** The subagents panel's chat room pane: an IRC-style, left-justified transcript, rendered as its
 * own small `Virtuoso` list since its per-sender layout differs from `HistoryView`'s turn-based
 * one. */
export default function ChatRoomView({
  messages,
  nodes,
  onSelectParticipant,
}: ChatRoomViewProps): JSX.Element {
  const { virtuosoRef, handleAtBottomStateChange, scrollToBottomIfPinned } = usePinnedScroll(
    messages.length
  );

  useEffect(() => {
    scrollToBottomIfPinned();
  }, [messages, scrollToBottomIfPinned]);

  return (
    <div id="chat-room-wrapper">
      <Virtuoso
        id="chat-room"
        ref={virtuosoRef}
        data={messages}
        computeItemKey={(_index, message) => message.seq}
        atBottomStateChange={handleAtBottomStateChange}
        atBottomThreshold={24}
        increaseViewportBy={OVERSCAN_PX}
        components={CHAT_ROOM_COMPONENTS}
        itemContent={(_index, message) => (
          <ChatMessageEntry
            message={message}
            nodes={nodes}
            onSelectParticipant={onSelectParticipant}
          />
        )}
      />
    </div>
  );
}
