// © Copyright 2026 Aaron Kimball

import type { SkillEntry } from 'shared/webviewMessages';

import type { FinderSelection } from '../fileFinder/fileFinderModel';

/** The `/`-mention context: where the active `/<name>` reference starts in the full text and
 * the query typed so far (everything after `/` up to the cursor). */
export interface SkillMentionContext {
  start: number;
  query: string;
}

const WHITESPACE = /\s/;

/**
 * Finds the `/<name>` reference (if any) the cursor currently sits inside: scans backward from
 * `cursor` for the nearest `/` not separated from it by whitespace, itself preceded by either
 * the start of the text or whitespace. Returns `undefined` when the cursor isn't inside such a
 * reference.
 */
export function detectSkillMention(text: string, cursor: number): SkillMentionContext | undefined {
  for (let i = cursor - 1; i >= 0; i--) {
    const ch = text[i];
    if (ch === undefined || WHITESPACE.test(ch)) {
      return undefined;
    }
    if (ch === '/') {
      const precedingChar = i === 0 ? undefined : text[i - 1];
      if (precedingChar !== undefined && !WHITESPACE.test(precedingChar)) {
        return undefined;
      }
      // Avoid matching URLs or directory paths: the character after `/` must be a letter or be
      // absent (cursor immediately after `/`). A `/` followed by a digit or punctuation is
      // unlikely to be a skill mention.
      const nextChar = text[i + 1];
      if (nextChar !== undefined && !/[a-zA-Z]/.test(nextChar)) {
        return undefined;
      }
      return { start: i, query: text.slice(i + 1, cursor) };
    }
  }
  return undefined;
}

/** One row the skill finder can show: a skill's name, namespace, and description. */
export interface SkillFinderMatch {
  skill: SkillEntry;
}

/** The result of splicing a selected skill into the prompt text: the full new text and where
 * the cursor should land afterward. */
export type SkillInsertion = FinderSelection;

/** Replaces the `/<query>` mention spanning `[mentionStart, cursor)` in `text` with `/` followed
 * by `skill.name` and a trailing space. */
export function buildSkillInsertion(
  text: string,
  mentionStart: number,
  cursor: number,
  skill: SkillEntry
): FinderSelection {
  const insertion = `/${skill.name} `;
  return {
    text: text.slice(0, mentionStart) + insertion + text.slice(cursor),
    cursor: mentionStart + insertion.length,
  };
}

/** Renders the skill name in the finder row's left slot (reuse `.file-finder-row-file` for the
 * name, right-aligned `.skill-finder-row-desc` for the description). */
export function splitSkillDisplay(skill: SkillEntry): { name: string; desc: string } {
  return { name: skill.name, desc: skill.description };
}
