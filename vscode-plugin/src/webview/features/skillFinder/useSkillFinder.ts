// © Copyright 2026 Aaron Kimball
import Fuse from 'fuse.js';
import { useMemo, useRef, useState } from 'react';

import type { SkillEntry } from 'shared/webviewMessages';

import {
  buildSkillInsertion,
  detectSkillMention,
  type SkillInsertion,
  type SkillMentionContext,
} from './skillFinderModel';

const MAX_MATCHES = 25;

export interface SkillFinderMatch {
  skill: SkillEntry;
}

interface SkillFinderState {
  mention: SkillMentionContext;
  matches: SkillFinderMatch[];
}

export type SkillFinderSelection = SkillInsertion;

export interface SkillFinder {
  isOpen: boolean;
  matches: SkillFinderMatch[];
  activeIndex: number;
  sync(text: string, cursor: number): void;
  setActiveIndex(index: number): void;
  moveActive(delta: number): void;
  dismiss(): void;
  select(text: string, index?: number): SkillFinderSelection | undefined;
}

export default function useSkillFinder(skills: SkillEntry[]): SkillFinder {
  const [state, setState] = useState<SkillFinderState | undefined>(undefined);
  const [activeIndex, setActiveIndex] = useState(0);
  const escapedStartRef = useRef<number | null>(null);

  const candidates = useMemo<SkillFinderMatch[]>(
    () => skills.map((skill): SkillFinderMatch => ({ skill })),
    [skills]
  );

  const fuse = useMemo(
    () =>
      new Fuse(candidates, {
        keys: ['skill.name', 'skill.description'],
        threshold: 0.4,
        ignoreLocation: true,
        includeScore: true,
      }),
    [candidates]
  );

  function rankMatches(query: string): SkillFinderMatch[] {
    if (query.length === 0) {
      return candidates.slice(0, MAX_MATCHES);
    }
    return fuse
      .search(query)
      .slice(0, MAX_MATCHES)
      .map((result) => result.item);
  }

  function sync(text: string, cursor: number): void {
    const mention = detectSkillMention(text, cursor);
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

  function select(text: string, index?: number): SkillFinderSelection | undefined {
    if (state === undefined || state.matches.length === 0) {
      return undefined;
    }
    const idx = index ?? activeIndex;
    const match = state.matches[idx];
    if (match === undefined) {
      return undefined;
    }
    const cursor = state.mention.start + 1 + state.mention.query.length;
    const insertion = buildSkillInsertion(text, state.mention.start, cursor, match.skill);
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
