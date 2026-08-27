// © Copyright 2026 Aaron Kimball
import type { SubagentNodeInfo } from 'shared/webviewMessages';
import type { FinderSelection } from 'webview/features/fileFinder';

/** One row the chat room's `@`-mention finder can show: a live participant plus its precomputed
 * `chatNickname()`, so Fuse.js can rank against a plain string field. */
export interface ChatMentionMatch {
  node: SubagentNodeInfo;
  nickname: string;
}

/** Replaces the `@<query>` mention spanning `[mentionStart, cursor)` in `text` with `@` followed
 * by `match.nickname` and a trailing space. */
export function buildChatMentionInsertion(
  text: string,
  mentionStart: number,
  cursor: number,
  match: ChatMentionMatch
): FinderSelection {
  const insertion = `@${match.nickname} `;
  return {
    text: text.slice(0, mentionStart) + insertion + text.slice(cursor),
    cursor: mentionStart + insertion.length,
  };
}
