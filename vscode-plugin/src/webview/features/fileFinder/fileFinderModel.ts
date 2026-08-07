// © Copyright 2026 Aaron Kimball

import { TRAILING_MENTION_PUNCTUATION } from './mentionParser';

/** Where the currently-active `@`-mention starts (the index of `@` itself in the full text) and
 * what's been typed after it up to the cursor -- the file finder's search query. */
export interface MentionContext {
  start: number;
  query: string;
}

const WHITESPACE = /\s/;

/**
 * Finds the `@`-mention (if any) the cursor currently sits inside of: scans backward from
 * `cursor` for the nearest `@` not separated from it by whitespace, itself preceded by either
 * the start of the text or whitespace (so an email-like `foo@bar` mid-word doesn't trigger).
 * Returns `undefined` when the cursor isn't inside such a mention.
 */
export function detectMentionQuery(text: string, cursor: number): MentionContext | undefined {
  for (let i = cursor - 1; i >= 0; i--) {
    const ch = text[i];
    if (ch === undefined || WHITESPACE.test(ch)) {
      return undefined;
    }
    if (ch === '@') {
      const precedingChar = i === 0 ? undefined : text[i - 1];
      if (precedingChar !== undefined && !WHITESPACE.test(precedingChar)) {
        return undefined;
      }
      return { start: i, query: text.slice(i + 1, cursor) };
    }
  }
  return undefined;
}

/** A workspace-relative path split into a truncatable directory part and a fixed, always-fully-
 * visible file part (with its own leading `/` whenever a directory is present). */
export interface FinderPathParts {
  dirPart: string;
  filePart: string;
}

/** Splits `relPath` for the file finder row layout: the directory part is rendered with CSS
 * ellipsis truncation (from the *front*, via `.file-finder-row-dir`'s `direction: rtl` rule --
 * see `media/main.css` -- so the segment closest to the filename stays visible), the file part
 * never truncates, so a deeply nested path reads as ".../path/to/file.txt" instead of
 * overflowing or wrapping the row. */
export function splitFinderPath(relPath: string): FinderPathParts {
  const idx = relPath.lastIndexOf('/');
  return idx === -1
    ? { dirPart: '', filePart: relPath }
    : { dirPart: relPath.slice(0, idx), filePart: relPath.slice(idx) };
}

/** One row the file finder can show: a workspace-relative path plus whether it names a
 * directory rather than a file. A file row is a leaf mention target (`buildMentionInsertion`);
 * a directory row instead narrows the active query into that subtree when selected
 * (`buildDirectoryInsertion`), since a bare directory isn't a valid `@mention` target. */
export interface FinderMatch {
  path: string;
  isDir: boolean;
}

/** Every directory (as a POSIX-style, workspace-relative string) that contains, directly or
 * transitively, one of `files` -- the finder's directory candidates, since the workspace file
 * list itself only ever lists files. */
export function ancestorDirectories(files: string[]): Set<string> {
  const directories = new Set<string>();
  for (const file of files) {
    const parts = file.split('/').slice(0, -1);
    for (let depth = 1; depth <= parts.length; depth++) {
      directories.add(parts.slice(0, depth).join('/'));
    }
  }
  return directories;
}

/** Number of `/` separators in `path` -- how many levels below the workspace root it sits, used
 * to bias directory ranking toward the search root (see `useFileFinder`'s `DIRECTORY_DEPTH_PENALTY`). */
export function pathDepth(path: string): number {
  return (path.match(/\//g) ?? []).length;
}

/** `query` split at its last `/` into a literal directory prefix (including the trailing `/`,
 * or `''` if `query` has no `/`) and the remaining fuzzy-match fragment -- e.g. `'klorb/sr'`
 * splits into `{dirPrefix: 'klorb/', fragment: 'sr'}`, and a query fresh off selecting a
 * directory match (which always ends in `/`, see `buildDirectoryInsertion`) splits into a
 * prefix and an empty fragment. */
export interface QueryDirectorySplit {
  dirPrefix: string;
  fragment: string;
}

export function splitQueryDirectory(query: string): QueryDirectorySplit {
  const idx = query.lastIndexOf('/');
  return idx === -1
    ? { dirPrefix: '', fragment: query }
    : { dirPrefix: query.slice(0, idx + 1), fragment: query.slice(idx + 1) };
}

/** Escapes a path for insertion into the prompt's plain-text body: backslash first (so the
 * escapes this function itself introduces aren't re-escaped by the later replacements), then
 * double quotes, then spaces -- e.g. `@foo\ bar.txt`, `@a\"b.txt`, `@a\\b.txt`. */
export function escapeMentionPath(relPath: string): string {
  return relPath.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/ /g, '\\ ');
}

/** Escapes a path for insertion inside an `@"..."` quoted mention: backslash first, then double
 * quotes -- unlike `escapeMentionPath`, spaces need no escaping inside the quotes. */
function escapeQuotedMentionPath(relPath: string): string {
  return relPath.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

/** Whether `relPath` needs the quoted `@"..."` mention form to round-trip through
 * `strip_trailing_mention_punctuation` unchanged -- true when it ends in a character from
 * `TRAILING_MENTION_PUNCTUATION`. */
function needsQuotedMention(relPath: string): boolean {
  return TRAILING_MENTION_PUNCTUATION.has(relPath.slice(-1));
}

/** The result of splicing a selected finder match into the prompt's text: the full new text, and
 * where the cursor should land afterward. */
export interface FinderSelection {
  text: string;
  cursor: number;
}

/** Replaces the `@query` mention spanning `[mentionStart, cursor)` in `text` with `@` followed
 * by `relPath`'s escaped form and a trailing space, so the user can keep typing immediately.
 * Uses the quoted form (`@"relPath" `) when `relPath` itself ends in a
 * `TRAILING_MENTION_PUNCTUATION` character (`needsQuotedMention`), since the unquoted form would
 * otherwise have that trailing character trimmed back off when the prompt is later resolved. */
export function buildMentionInsertion(
  text: string,
  mentionStart: number,
  cursor: number,
  relPath: string
): FinderSelection {
  const insertion = needsQuotedMention(relPath)
    ? `@"${escapeQuotedMentionPath(relPath)}" `
    : `@${escapeMentionPath(relPath)} `;
  return {
    text: text.slice(0, mentionStart) + insertion + text.slice(cursor),
    cursor: mentionStart + insertion.length,
  };
}

/** Replaces the `@query` mention spanning `[mentionStart, cursor)` in `text` with `@` followed
 * by `relPath`'s escaped form and a trailing `/` -- no trailing space, unlike
 * `buildMentionInsertion` -- so the mention stays open and the user keeps narrowing into that
 * directory instead of completing the mention. */
export function buildDirectoryInsertion(
  text: string,
  mentionStart: number,
  cursor: number,
  relPath: string
): FinderSelection {
  const insertion = `@${escapeMentionPath(relPath)}/`;
  return {
    text: text.slice(0, mentionStart) + insertion + text.slice(cursor),
    cursor: mentionStart + insertion.length,
  };
}
