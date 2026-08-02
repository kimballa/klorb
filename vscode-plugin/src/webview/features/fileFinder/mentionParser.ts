// © Copyright 2026 Aaron Kimball

/**
 * Finds `@mention`s in already-submitted prompt text, mirroring
 * `klorb.session.mixins.mentions._AT_MENTION_RE`/`resolve_at_mentions` exactly (same regex, same
 * escape handling, same trailing-punctuation trimming) so the webview can highlight precisely
 * the spans the server would treat as file references, without asking the extension host to
 * re-derive them. Used to render `HistoryView`'s `'prompt'`/`'queuedMessage'` entries; unlike
 * `fileFinderModel.ts`'s interactive finder, this module never looks at the workspace file list,
 * only at the text's own syntax. See docs/specs/at-mention-file-inlining.md.
 */

/** Matches an `@mention`: group 1 captures a double-quoted filename (escapes allowed, contents
 * used verbatim); group 2 captures an unquoted run of non-whitespace, non-quote characters (a
 * backslash-escaped character, including `\ `, counts as one unit). Mirrors
 * `klorb.session.mixins.mentions._AT_MENTION_RE`. */
const MENTION_RE = /(?<!\S)@"((?:[^"\\\n]|\\[\s\S])*?)"|(?<!\S)@((?:[^\s"\\]|\\[\s\S])+)/g;

/** Characters that, as the literal last character of an *unquoted* `@mention`, are trimmed off
 * as trailing sentence punctuation rather than treated as part of the filename -- mirrors
 * `klorb.session.mixins.mentions.TRAILING_MENTION_PUNCTUATION`. A quoted mention (`@"foo."`) is
 * never subject to this: an unescaped `"` can't appear in an unquoted match at all (excluded
 * from `MENTION_RE`'s own character class), so this set only ever needs the characters that
 * *are* part of that class. */
export const TRAILING_MENTION_PUNCTUATION = new Set([
  '.',
  ',',
  "'",
  ')',
  ']',
  '}',
  '!',
  '?',
  ':',
  ';',
]);

/** Resolves `\ `, `\\`, `\"` escape sequences in a raw `@mention` filename; a backslash followed
 * by any other character is kept as-is (both characters are literal). Mirrors
 * `klorb.session.mixins.mentions.unescape_mention_filename`. */
export function unescapeMentionFilename(raw: string): string {
  let out = '';
  let i = 0;
  while (i < raw.length) {
    const ch = raw[i];
    if (ch === '\\' && i + 1 < raw.length) {
      const next = raw[i + 1];
      if (next === ' ') {
        out += ' ';
      } else if (next === '\\') {
        out += '\\';
      } else if (next === '"') {
        out += '"';
      } else {
        out += ch + next;
      }
      i += 2;
    } else {
      out += ch;
      i += 1;
    }
  }
  return out;
}

/** Start index of each token in an unquoted `@mention`'s raw (still-escaped) text: a token is
 * either one ordinary character or a backslash followed by any character -- the same
 * tokenization `MENTION_RE`'s unquoted branch uses. */
function mentionTokenStarts(raw: string): number[] {
  const starts: number[] = [];
  let i = 0;
  while (i < raw.length) {
    starts.push(i);
    i += raw[i] === '\\' && i + 1 < raw.length ? 2 : 1;
  }
  return starts;
}

/** Strips trailing `TRAILING_MENTION_PUNCTUATION` characters off the end of an *unquoted*
 * `@mention`'s raw (still-escaped) text, so sentence punctuation immediately following a mention
 * (`see @foo.txt.`, `(@foo.txt)`) isn't swept into the filename. Stops once the remaining text
 * would become empty or its last token is a backslash-escaped character (an explicit
 * `\!`/`\.`/etc. is kept literal, never stripped). Mirrors
 * `klorb.session.mixins.mentions.strip_trailing_mention_punctuation`. */
export function stripTrailingMentionPunctuation(raw: string): string {
  let text = raw;
  while (true) {
    const starts = mentionTokenStarts(text);
    const tokenStart = starts[starts.length - 1];
    if (tokenStart === undefined || tokenStart === 0) {
      return text;
    }
    const token = text.slice(tokenStart);
    if (token.length !== 1 || !TRAILING_MENTION_PUNCTUATION.has(token)) {
      return text;
    }
    text = text.slice(0, tokenStart);
  }
}

/** One `@mention` found in submitted prompt text: `start`/`end` bound the span to highlight (the
 * `@` through the resolved filename syntax -- trailing sentence punctuation trimmed off an
 * unquoted mention is excluded, but a quoted mention's closing `"` is included), and `filename`
 * is the fully unescaped file this mention resolves to. Mirrors what
 * `klorb.session.mixins.mentions.resolve_at_mentions` extracts server-side, for rendering only --
 * this module never reads the filesystem. */
export interface MentionSpan {
  start: number;
  end: number;
  filename: string;
}

/** Finds every `@mention` in `text`, in the order they appear, exactly matching which spans
 * `klorb.session.mixins.mentions.resolve_at_mentions` would treat as file references
 * server-side. */
export function findMentionSpans(text: string): MentionSpan[] {
  const spans: MentionSpan[] = [];
  for (const match of text.matchAll(MENTION_RE)) {
    // `matchAll` always sets `index` for a global-regex match; the lib type just doesn't narrow
    // it, since a plain (non-global) `RegExpMatchArray.index` can legitimately be absent.
    const start = match.index ?? 0;
    if (match[1] !== undefined) {
      spans.push({
        start,
        end: start + match[0].length,
        filename: unescapeMentionFilename(match[1]),
      });
      continue;
    }
    const raw = match[2] ?? '';
    const stripped = stripTrailingMentionPunctuation(raw);
    spans.push({
      start,
      end: start + 1 + stripped.length,
      filename: unescapeMentionFilename(stripped),
    });
  }
  return spans;
}
