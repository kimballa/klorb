// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import {
  findMentionSpans,
  stripTrailingMentionPunctuation,
  unescapeMentionFilename,
} from 'webview/features/fileFinder';

describe('unescapeMentionFilename', () => {
  it('leaves a filename with no escapes unchanged', () => {
    expect(unescapeMentionFilename('foo.txt')).toBe('foo.txt');
  });

  it('resolves an escaped space', () => {
    expect(unescapeMentionFilename('foo\\ bar.txt')).toBe('foo bar.txt');
  });

  it('resolves an escaped backslash', () => {
    expect(unescapeMentionFilename('foo\\\\bar.txt')).toBe('foo\\bar.txt');
  });

  it('resolves an escaped double quote', () => {
    expect(unescapeMentionFilename('foo\\"bar.txt')).toBe('foo"bar.txt');
  });

  it('keeps a backslash before a non-special character literal', () => {
    expect(unescapeMentionFilename('abc\\def')).toBe('abc\\def');
  });
});

describe('stripTrailingMentionPunctuation', () => {
  it('leaves a filename with no trailing punctuation unchanged', () => {
    expect(stripTrailingMentionPunctuation('foo.txt')).toBe('foo.txt');
  });

  it.each([
    ['period', 'foo.txt.'],
    ['comma', 'foo.txt,'],
    ['single quote', "foo.txt'"],
    ['closing paren', 'foo.txt)'],
    ['closing square bracket', 'foo.txt]'],
    ['closing curly brace', 'foo.txt}'],
    ['exclamation point', 'foo.txt!'],
    ['question mark', 'foo.txt?'],
    ['colon', 'foo.txt:'],
    ['semicolon', 'foo.txt;'],
  ])('strips a trailing %s', (_label, raw) => {
    expect(stripTrailingMentionPunctuation(raw)).toBe('foo.txt');
  });

  it('strips multiple trailing punctuation characters', () => {
    expect(stripTrailingMentionPunctuation('foo.txt!)')).toBe('foo.txt');
  });

  it('keeps an escaped trailing punctuation character literal', () => {
    expect(stripTrailingMentionPunctuation('foo\\!')).toBe('foo\\!');
  });

  it('never strips down to an empty string', () => {
    expect(stripTrailingMentionPunctuation('.')).toBe('.');
    expect(stripTrailingMentionPunctuation('!')).toBe('!');
  });
});

describe('findMentionSpans', () => {
  it('finds a simple unquoted mention', () => {
    const spans = findMentionSpans('check @foo.txt please');
    expect(spans).toHaveLength(1);
    expect(spans[0]).toEqual({ start: 6, end: 14, filename: 'foo.txt' });
  });

  it('does not treat an email-like @ as a mention', () => {
    expect(findMentionSpans('email user@example.com')).toHaveLength(0);
  });

  it('finds multiple mentions in order', () => {
    const spans = findMentionSpans('@a.txt and @b.txt');
    expect(spans.map((span) => span.filename)).toEqual(['a.txt', 'b.txt']);
  });

  it('resolves an escaped space', () => {
    const spans = findMentionSpans('see @foo\\ bar.txt');
    expect(spans[0]?.filename).toBe('foo bar.txt');
  });

  it('resolves a quoted mention verbatim, including internal spaces', () => {
    const spans = findMentionSpans('see @"foo bar.txt" now');
    expect(spans).toHaveLength(1);
    expect(spans[0]?.filename).toBe('foo bar.txt');
    // The highlighted span includes the surrounding quotes -- the mention's actual syntax.
    expect(spans[0]).toEqual({ start: 4, end: 18, filename: 'foo bar.txt' });
  });

  describe('trailing punctuation woven into a sentence', () => {
    it.each([
      ['period', 'see @foo.txt.'],
      ['comma', 'check @foo.txt, thanks'],
      ['single quote', "check @foo.txt' thanks"],
      ['double quote', 'check @foo.txt" thanks'],
      ['closing paren', 'see (result @foo.txt) now'],
      ['closing square bracket', 'see [item @foo.txt] now'],
      ['closing curly brace', 'see {value @foo.txt} now'],
      ['exclamation point', 'wow @foo.txt! amazing'],
      ['question mark', 'did you read @foo.txt? really'],
      ['colon', 'attached: @foo.txt: see above'],
      ['semicolon', 'see @foo.txt; then continue'],
    ])('extracts the filename before the trailing %s', (_label, text) => {
      const spans = findMentionSpans(text);
      expect(spans).toHaveLength(1);
      expect(spans[0]?.filename).toBe('foo.txt');
    });

    it('resolves a mention immediately preceding an unrelated sentence-quote', () => {
      const spans = findMentionSpans('"hello @foo.txt"');
      expect(spans).toHaveLength(1);
      expect(spans[0]?.filename).toBe('foo.txt');
    });

    it('resolves a mention followed by a quote with no whitespace, then more prose', () => {
      const spans = findMentionSpans('hello @foo.txt"followed by a quote out of context.');
      expect(spans).toHaveLength(1);
      expect(spans[0]?.filename).toBe('foo.txt');
    });

    it('retains trailing punctuation verbatim inside a quoted mention', () => {
      const spans = findMentionSpans('see @"foo.txt." now');
      expect(spans).toHaveLength(1);
      expect(spans[0]?.filename).toBe('foo.txt.');
    });
  });
});
