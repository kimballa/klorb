// © Copyright 2026 Aaron Kimball
import Fuse from 'fuse.js';
import { useMemo, useRef, useState } from 'react';

import {
  buildMentionInsertion,
  detectMentionQuery,
  type MentionContext,
  type MentionInsertion,
} from './fileFinderModel';

/** How many matches the popup shows at once (see docs/specs/vscode-plugin.md's file-finder
 * section). */
const MAX_MATCHES = 6;

export type FileFinderSelection = MentionInsertion;

interface FinderState {
  mention: MentionContext;
  matches: string[];
}

export interface FileFinder {
  isOpen: boolean;
  matches: string[];
  activeIndex: number;
  /** Recomputes the finder's mention/matches from the textarea's current text and cursor
   * position -- call on every keystroke and cursor move (input, navigation keyup, click). */
  sync(text: string, cursor: number): void;
  setActiveIndex(index: number): void;
  moveActive(delta: number): void;
  /** Escape: closes the popup without clearing the mention itself, so typing further within the
   * same `@query` doesn't immediately reopen it (see `sync`'s `escapedStartRef` handling). */
  dismiss(): void;
  /** Splices the match at `index` (default: the active one) into `text`, returning the new text
   * and cursor position, and closes the finder. `undefined` if there's nothing to select. */
  select(text: string, index?: number): FileFinderSelection | undefined;
}

/**
 * Drives the `@`-mention file finder popup for `PromptInput`: tracks whether the textarea's
 * cursor currently sits inside an `@query` mention (`fileFinderModel.ts`'s `detectMentionQuery`),
 * fuzzy-matches `files` against that query with Fuse.js, and exposes keyboard/mouse navigation
 * over the resulting (up to `MAX_MATCHES`) matches. `files` is the workspace's file list as
 * pushed by the extension host (see `App`'s `workspaceFiles` state) -- this hook does no
 * fetching of its own.
 */
export default function useFileFinder(files: string[]): FileFinder {
  const [state, setState] = useState<FinderState | undefined>(undefined);
  const [activeIndex, setActiveIndex] = useState(0);
  // The mention `start` Escape last dismissed -- while `sync()` keeps seeing that same start
  // (the user hasn't moved to a different `@`), the popup stays closed even though matches would
  // otherwise reopen it.
  const escapedStartRef = useRef<number | null>(null);

  const fuse = useMemo(() => new Fuse(files, { threshold: 0.4, ignoreLocation: true }), [files]);

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
    const matches =
      mention.query.length === 0
        ? files.slice(0, MAX_MATCHES)
        : fuse.search(mention.query, { limit: MAX_MATCHES }).map((result) => result.item);
    setState({ mention, matches });
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
    if (state === undefined) {
      return;
    }
    escapedStartRef.current = state.mention.start;
    setState({ mention: state.mention, matches: [] });
  }

  function select(text: string, index: number = activeIndex): FileFinderSelection | undefined {
    if (state === undefined) {
      return undefined;
    }
    const path = state.matches[index];
    if (path === undefined) {
      return undefined;
    }
    const cursor = state.mention.start + 1 + state.mention.query.length;
    const insertion = buildMentionInsertion(text, state.mention.start, cursor, path);
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
