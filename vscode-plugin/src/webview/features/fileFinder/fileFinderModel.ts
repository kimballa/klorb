// © Copyright 2026 Aaron Kimball

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
 * ellipsis truncation, the file part never truncates, so a deeply nested path reads as
 * "some/path/to.../file.txt" instead of overflowing or wrapping the row. */
export function splitFinderPath(relPath: string): FinderPathParts {
  const idx = relPath.lastIndexOf('/');
  return idx === -1
    ? { dirPart: '', filePart: relPath }
    : { dirPart: relPath.slice(0, idx), filePart: relPath.slice(idx) };
}

/** Escapes a path for insertion into the prompt's plain-text body: backslash first (so the
 * escapes this function itself introduces aren't re-escaped by the later replacements), then
 * double quotes, then spaces -- e.g. `@foo\ bar.txt`, `@a\"b.txt`, `@a\\b.txt`. */
export function escapeMentionPath(relPath: string): string {
  return relPath.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/ /g, '\\ ');
}

/** The result of splicing a selected file into the prompt's text: the full new text, and where
 * the cursor should land afterward. */
export interface MentionInsertion {
  text: string;
  cursor: number;
}

/** Replaces the `@query` mention spanning `[mentionStart, cursor)` in `text` with `@` followed
 * by `relPath`'s escaped form and a trailing space, so the user can keep typing immediately. */
export function buildMentionInsertion(
  text: string,
  mentionStart: number,
  cursor: number,
  relPath: string
): MentionInsertion {
  const insertion = `@${escapeMentionPath(relPath)} `;
  return {
    text: text.slice(0, mentionStart) + insertion + text.slice(cursor),
    cursor: mentionStart + insertion.length,
  };
}
