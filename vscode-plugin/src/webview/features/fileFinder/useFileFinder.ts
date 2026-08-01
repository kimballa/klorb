// © Copyright 2026 Aaron Kimball
import Fuse from 'fuse.js';
import { useMemo, useRef, useState } from 'react';

import {
  ancestorDirectories,
  buildDirectoryInsertion,
  buildMentionInsertion,
  detectMentionQuery,
  type FinderMatch,
  type MentionContext,
  type MentionInsertion,
} from './fileFinderModel';

/** How many ranked matches the popup keeps, scrollable within its fixed on-screen height (see
 * `media/main.css`'s `.file-finder-panel` `max-height`) -- well beyond what fits on screen at
 * once, so a broad query still surfaces distant matches instead of hiding them outright. */
const MAX_MATCHES = 25;

/** Added to a directory candidate's Fuse.js score (0 = perfect match, 1 = no match -- lower is
 * better) before ranking, so a directory whose name matches about as well as a file surfaces
 * above it -- letting the user drill into a subtree via a directory row instead of only ever
 * seeing leaf files. Subtracted, not added, since a lower Fuse score is a better match. */
const DIRECTORY_SCORE_BUMP = 0.1;

export type FileFinderSelection = MentionInsertion;

interface FinderState {
  mention: MentionContext;
  matches: FinderMatch[];
}

export interface FileFinder {
  isOpen: boolean;
  matches: FinderMatch[];
  activeIndex: number;
  /** Recomputes the finder's mention/matches from the textarea's current text and cursor
   * position -- call on every keystroke and cursor move (input, navigation keyup, click). */
  sync(text: string, cursor: number): void;
  setActiveIndex(index: number): void;
  moveActive(delta: number): void;
  /** Escape: closes the popup without clearing the mention itself, so typing further within the
   * same `@query` doesn't immediately reopen it (see `sync`'s `escapedStartRef` handling). */
  dismiss(): void;
  /** Applies the match at `index` (default: the active one) to `text`. A file match splices in
   * a completed `@mention` and closes the finder; a directory match instead narrows the query
   * into that subtree (`buildDirectoryInsertion`) and leaves the finder open, re-synced against
   * the narrowed text -- a directory isn't a valid mention target on its own. Returns the new
   * text and cursor position either way, or `undefined` if there's nothing to select. */
  select(text: string, index?: number): FileFinderSelection | undefined;
}

/**
 * Drives the `@`-mention file finder popup for `PromptInput`: tracks whether the textarea's
 * cursor currently sits inside an `@query` mention (`fileFinderModel.ts`'s `detectMentionQuery`),
 * fuzzy-matches `files` plus every ancestor directory of one (`ancestorDirectories`) against
 * that query with Fuse.js -- a directory's score gets `DIRECTORY_SCORE_BUMP` so it ranks above
 * an equally-relevant file -- and exposes keyboard/mouse navigation over the resulting (up to
 * `MAX_MATCHES`) matches. `files` is the workspace's file list as pushed by the extension host
 * (see `App`'s `workspaceFiles` state) -- this hook does no fetching of its own.
 */
export default function useFileFinder(files: string[]): FileFinder {
  const [state, setState] = useState<FinderState | undefined>(undefined);
  const [activeIndex, setActiveIndex] = useState(0);
  // The mention `start` Escape last dismissed -- while `sync()` keeps seeing that same start
  // (the user hasn't moved to a different `@`), the popup stays closed even though matches would
  // otherwise reopen it.
  const escapedStartRef = useRef<number | null>(null);

  const candidates = useMemo<FinderMatch[]>(() => {
    const directories = ancestorDirectories(files);
    return [
      ...files.map((path): FinderMatch => ({ path, isDir: false })),
      ...Array.from(directories, (path): FinderMatch => ({ path, isDir: true })),
    ];
  }, [files]);

  const fuse = useMemo(
    () =>
      new Fuse(candidates, {
        keys: ['path'],
        threshold: 0.4,
        ignoreLocation: true,
        includeScore: true,
      }),
    [candidates]
  );

  /** Ranks `candidates` against `query`, up to `MAX_MATCHES`: alphabetically with directories
   * ahead of files for an empty query (nothing to rank), otherwise by ascending Fuse score
   * (lower is better) with `DIRECTORY_SCORE_BUMP` subtracted for directories. Fuse's own
   * `threshold` already excludes non-matches before the bump is applied, so it only ever
   * reorders candidates that independently matched. */
  function rankMatches(query: string): FinderMatch[] {
    if (query.length === 0) {
      return [...candidates]
        .sort((a, b) => {
          if (a.isDir !== b.isDir) {
            return a.isDir ? -1 : 1;
          }
          return a.path < b.path ? -1 : a.path > b.path ? 1 : 0;
        })
        .slice(0, MAX_MATCHES);
    }
    return fuse
      .search(query)
      .map((result) => ({
        match: result.item,
        score: (result.score ?? 0) - (result.item.isDir ? DIRECTORY_SCORE_BUMP : 0),
      }))
      .sort((a, b) => a.score - b.score)
      .slice(0, MAX_MATCHES)
      .map((ranked) => ranked.match);
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
    const match = state.matches[index];
    if (match === undefined) {
      return undefined;
    }
    const cursor = state.mention.start + 1 + state.mention.query.length;
    if (match.isDir) {
      const insertion = buildDirectoryInsertion(text, state.mention.start, cursor, match.path);
      escapedStartRef.current = null;
      sync(insertion.text, insertion.cursor);
      return insertion;
    }
    const insertion = buildMentionInsertion(text, state.mention.start, cursor, match.path);
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
