// © Copyright 2026 Aaron Kimball
import Fuse from 'fuse.js';
import { useMemo, useRef, useState } from 'react';

import type { SubagentNodeInfo } from 'shared/webviewMessages';
import { detectMentionQuery, type Finder, type MentionContext } from 'webview/features/fileFinder';

import { buildChatMentionInsertion, type ChatMentionMatch } from './chatMentionFinderModel';
import { chatNickname } from './chatRoomModel';

/** How many ranked matches the popup keeps. */
const MAX_MATCHES = 25;

interface ChatMentionFinderState {
  mention: MentionContext;
  matches: ChatMentionMatch[];
}

/** Drives the chat room's `@`-mention finder popup for `PromptInput`, reusing `detectMentionQuery`
 * for the `@`-trigger scan since that logic doesn't depend on the mention grammar that follows
 * it. */
export default function useChatMentionFinder(nodes: SubagentNodeInfo[]): Finder<ChatMentionMatch> {
  const [state, setState] = useState<ChatMentionFinderState | undefined>(undefined);
  const [activeIndex, setActiveIndex] = useState(0);
  const escapedStartRef = useRef<number | null>(null);

  const candidates = useMemo<ChatMentionMatch[]>(
    () => nodes.map((node): ChatMentionMatch => ({ node, nickname: chatNickname(node) })),
    [nodes]
  );

  const fuse = useMemo(
    () =>
      new Fuse(candidates, {
        keys: ['nickname'],
        threshold: 0.4,
        ignoreLocation: true,
        includeScore: true,
      }),
    [candidates]
  );

  function rankMatches(query: string): ChatMentionMatch[] {
    if (query.length === 0) {
      return candidates.slice(0, MAX_MATCHES);
    }
    return fuse
      .search(query)
      .slice(0, MAX_MATCHES)
      .map((result) => result.item);
  }

  function sync(text: string, cursor: number): void {
    const mention = detectMentionQuery(text, cursor);
    if (mention === undefined) {
      escapedStartRef.current = null;
      setState(undefined);
      return;
    }
    if (mention.start === escapedStartRef.current) {
      setState({ mention, matches: [] });
      return;
    }
    escapedStartRef.current = null;
    setState({ mention, matches: rankMatches(mention.query) });
    setActiveIndex(0);
  }

  function moveActive(delta: number): void {
    const count = state?.matches.length ?? 0;
    if (count === 0) {
      return;
    }
    setActiveIndex((prev) => (prev + delta + count) % count);
  }

  function dismiss(): void {
    if (state !== undefined) {
      escapedStartRef.current = state.mention.start;
    }
    setState({ mention: state?.mention ?? { start: -1, query: '' }, matches: [] });
  }

  function select(text: string, index: number = activeIndex) {
    if (state === undefined) {
      return undefined;
    }
    const match = state.matches[index];
    if (match === undefined) {
      return undefined;
    }
    const cursor = state.mention.start + 1 + state.mention.query.length;
    const insertion = buildChatMentionInsertion(text, state.mention.start, cursor, match);
    escapedStartRef.current = null;
    setState(undefined);
    return insertion;
  }

  return {
    isOpen: (state?.matches.length ?? 0) > 0,
    matches: state?.matches ?? [],
    activeIndex,
    sync,
    setActiveIndex,
    moveActive,
    dismiss,
    select,
  };
}
