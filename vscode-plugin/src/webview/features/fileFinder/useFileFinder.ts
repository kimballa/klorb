// © Copyright 2026 Aaron Kimball
import Fuse, { type FuseResult } from 'fuse.js';
import { useMemo, useRef, useState } from 'react';

import {
  ancestorDirectories,
  buildDirectoryInsertion,
  buildMentionInsertion,
  detectMentionQuery,
  pathDepth,
  splitQueryDirectory,
  type FinderMatch,
  type FinderSelection,
  type MentionContext,
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

/** Added to a directory candidate's Fuse.js score per `/` in its path (worsening it, since a
 * lower Fuse score is better), so among similarly-scored directories the one nearest the
 * workspace root outranks one nested many levels deep. */
const DIRECTORY_DEPTH_PENALTY = 0.02;

interface FinderState {
  mention: MentionContext;
  matches: FinderMatch[];
}

export interface Finder<M> {
  isOpen: boolean;
  matches: M[];
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
  select(text: string, index?: number): FinderSelection | undefined;
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
export default function useFileFinder(files: string[]): Finder<FinderMatch> {
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

  /** Orders `items` (a mix of files and directories, all real descendants of `dirPrefix`) with
   * the immediate contents of `dirPrefix` first -- its own subdirectories, then its own files,
   * all of them regardless of `MAX_MATCHES` -- followed by everything nested deeper (again
   * subdirectories before files, then shallower before deeper, then alphabetically), which
   * fills in only up to `MAX_MATCHES` total. Used when there's no fuzzy-match fragment to rank
   * by, so browsing a directory (or the workspace root, for which `dirPrefix` is `''`) always
   * shows everything sitting directly in it instead of letting a large subtree's grandchildren
   * crowd out a sibling file that a plain depth-then-alphabetical sort with a flat cutoff could
   * otherwise push past the limit. */
  function breadthFirstMatches(items: FinderMatch[], dirPrefix: string): FinderMatch[] {
    const relativeDepth = (path: string): number => pathDepth(path.slice(dirPrefix.length));
    const ordered = [...items].sort((a, b) => {
      const aDepth = relativeDepth(a.path);
      const bDepth = relativeDepth(b.path);
      if ((aDepth !== 0) !== (bDepth !== 0)) {
        return aDepth !== 0 ? 1 : -1;
      }
      if (a.isDir !== b.isDir) {
        return a.isDir ? -1 : 1;
      }
      if (aDepth !== bDepth) {
        return aDepth - bDepth;
      }
      return a.path < b.path ? -1 : a.path > b.path ? 1 : 0;
    });
    const immediateCount = items.filter((match) => relativeDepth(match.path) === 0).length;
    return ordered.slice(0, Math.max(MAX_MATCHES, immediateCount));
  }

  /** Ranks Fuse `results` up to `MAX_MATCHES`, ascending by score (lower is better) with
   * `DIRECTORY_SCORE_BUMP` subtracted and `DIRECTORY_DEPTH_PENALTY` per path segment added for
   * a directory match. Fuse's own `threshold` already excludes non-matches before either
   * adjustment is applied, so this only ever reorders candidates that independently matched. */
  function rankFuseResults<T>(
    results: FuseResult<T>[],
    toMatch: (item: T) => FinderMatch
  ): FinderMatch[] {
    return results
      .map((result) => {
        const match = toMatch(result.item);
        const rawScore = result.score ?? 0;
        const score = match.isDir
          ? rawScore - DIRECTORY_SCORE_BUMP + DIRECTORY_DEPTH_PENALTY * pathDepth(match.path)
          : rawScore;
        return { match, score };
      })
      .sort((a, b) => a.score - b.score)
      .slice(0, MAX_MATCHES)
      .map((ranked) => ranked.match);
  }

  /** Ranks `candidates` against `query`. `query` splits at its last `/`
   * (`splitQueryDirectory`) into a literal directory prefix and a remaining fuzzy-match
   * fragment: candidates are first narrowed to real descendants of that prefix (a plain string-
   * prefix check, not fuzzy), so a query built by selecting a directory match -- which always
   * ends in `/` -- actually scopes to that subtree instead of fuzzy-matching the directory's
   * name against every workspace path (which could resurface an unrelated path that merely
   * happens to contain the same text, e.g. `.klorb/` for a `klorb/` prefix). Within that scope,
   * an empty fragment (nothing to rank) falls back to `breadthFirstMatches`; otherwise
   * candidates rank by Fuse score of the fragment against each candidate's path *relative to
   * the prefix* (`rankFuseResults`). An unprefixed query reuses the persistent, memoized `fuse`
   * index built over the whole workspace; a prefixed one builds a throwaway index over just the
   * (already much smaller) scoped subset, since the relative path it must search against
   * differs per prefix and so can't be precomputed once. */
  function rankMatches(query: string): FinderMatch[] {
    const { dirPrefix, fragment } = splitQueryDirectory(query);
    const scoped =
      dirPrefix.length === 0
        ? candidates
        : candidates.filter((match) => match.path.startsWith(dirPrefix));
    if (fragment.length === 0) {
      return breadthFirstMatches(scoped, dirPrefix);
    }
    if (dirPrefix.length === 0) {
      return rankFuseResults(fuse.search(fragment), (item) => item);
    }
    const scopedFuse = new Fuse(
      scoped.map((match) => ({ match, rel: match.path.slice(dirPrefix.length) })),
      { keys: ['rel'], threshold: 0.4, ignoreLocation: true, includeScore: true }
    );
    return rankFuseResults(scopedFuse.search(fragment), (item) => item.match);
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

  function select(text: string, index: number = activeIndex): FinderSelection | undefined {
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
