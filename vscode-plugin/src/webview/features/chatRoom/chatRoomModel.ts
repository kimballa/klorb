// © Copyright 2026 Aaron Kimball
import type {
  ChatHistoryUpdateMessage,
  HostMessage,
  SubagentNodeInfo,
} from 'shared/webviewMessages';

/** The reserved participant id standing in for the human user. */
export const CHAT_USER_ID = 'user';

/** A start/whitespace-anchored `@token`. */
const CHAT_MENTION_RE = /(?<!\S)@([A-Za-z0-9_.-]+)/g;

/** A live participant's `role-address` nickname, e.g. `explorer-1.1`. */
export function chatNickname(node: SubagentNodeInfo): string {
  return `${node.role}-${node.address}`;
}

/** The display name for `senderId`: `"You"` for the human user, a live participant's own
 * `chatNickname()`, or `senderId` itself when no node in `nodes` matches (a session no longer in
 * the tree). */
export function chatDisplayName(senderId: string, nodes: readonly SubagentNodeInfo[]): string {
  if (senderId === CHAT_USER_ID) {
    return 'You';
  }
  const node = nodes.find((candidate) => candidate.id === senderId);
  return node !== undefined ? chatNickname(node) : senderId;
}

/** Token -> canonical participant id, case-insensitive: the reserved `"user"` literal plus every
 * live node's raw id and `chatNickname()` form. */
function buildMentionTargets(nodes: readonly SubagentNodeInfo[]): Map<string, string> {
  const targets = new Map<string, string>();
  targets.set(CHAT_USER_ID, CHAT_USER_ID);
  for (const node of nodes) {
    targets.set(node.id.toLowerCase(), node.id);
    targets.set(chatNickname(node).toLowerCase(), node.id);
  }
  return targets;
}

/** One `@mention` found in a chat message body: `start`/`end` bound the raw `@token` span,
 * `resolvedId` is the participant it resolves to (case-insensitively), or `undefined` for an
 * unresolved token, left as plain unstyled text at render time. */
export interface ChatMentionSpan {
  start: number;
  end: number;
  resolvedId: string | undefined;
}

/** Finds every `@mention` in `body`, resolving each case-insensitively against `nodes`'s current
 * live participants. */
export function findChatMentionSpans(
  body: string,
  nodes: readonly SubagentNodeInfo[]
): ChatMentionSpan[] {
  const targets = buildMentionTargets(nodes);
  const spans: ChatMentionSpan[] = [];
  for (const match of body.matchAll(CHAT_MENTION_RE)) {
    const start = match.index ?? 0;
    const token = match[1] ?? '';
    spans.push({
      start,
      end: start + match[0].length,
      resolvedId: targets.get(token.toLowerCase()),
    });
  }
  return spans;
}

/** The chat room's coalesced snapshot, without the message envelope's `type` discriminant. */
export type ChatRoomSnapshot = Omit<ChatHistoryUpdateMessage, 'type'>;

/** Tracks the chat room's snapshot from the same message stream: `chatHistoryUpdate` replaces it
 * wholesale, `sessionReset` clears it, and every other message leaves it unchanged. */
export function applyChatHistoryUpdate(
  snapshot: ChatRoomSnapshot | undefined,
  message: HostMessage
): ChatRoomSnapshot | undefined {
  switch (message.type) {
    case 'chatHistoryUpdate':
      return {
        messages: message.messages,
        unreadCount: message.unreadCount,
        unreadMentionCount: message.unreadMentionCount,
      };
    case 'sessionReset':
      return undefined;
    default:
      return snapshot;
  }
}

/** The subagents panel's "Chat Room" row marker: a steady `'!'` once the user has any unread
 * message, upgraded to a blinking `'!'` once one of those messages `@mention`s the user directly.
 * `undefined` while the chat room is the current selection. */
export function chatRowMarker(
  unreadCount: number,
  unreadMentionCount: number,
  chatRoomSelected: boolean,
  blinkOn: boolean
): '!' | undefined {
  if (chatRoomSelected) {
    return undefined;
  }
  if (unreadMentionCount > 0) {
    return blinkOn ? '!' : undefined;
  }
  return unreadCount > 0 ? '!' : undefined;
}
