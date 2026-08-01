// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import {
  ancestorDirectories,
  buildDirectoryInsertion,
  buildMentionInsertion,
  detectMentionQuery,
  escapeMentionPath,
  splitFinderPath,
} from 'webview/features/fileFinder';

describe('detectMentionQuery', () => {
  it('finds a mention at the start of the text', () => {
    expect(detectMentionQuery('@foo', 4)).toEqual({ start: 0, query: 'foo' });
  });

  it('finds a mention preceded by whitespace', () => {
    expect(detectMentionQuery('please check @src/App', 21)).toEqual({
      start: 13,
      query: 'src/App',
    });
  });

  it('returns an empty query right after the @ itself', () => {
    expect(detectMentionQuery('hello @', 7)).toEqual({ start: 6, query: '' });
  });

  it('does not trigger on an email-like @ not preceded by whitespace', () => {
    expect(detectMentionQuery('foo@bar', 7)).toBeUndefined();
  });

  it('does not trigger once whitespace separates the @ from the cursor', () => {
    expect(detectMentionQuery('@foo bar', 8)).toBeUndefined();
  });

  it('does not trigger when there is no @ before the cursor', () => {
    expect(detectMentionQuery('no mention here', 16)).toBeUndefined();
  });

  it('finds the mention when the cursor sits mid-query, not just at the end', () => {
    expect(detectMentionQuery('@abcdef rest', 4)).toEqual({ start: 0, query: 'abc' });
  });
});

describe('splitFinderPath', () => {
  it('splits a nested path into a dir part and a slash-prefixed file part', () => {
    expect(splitFinderPath('some/path/to/file.txt')).toEqual({
      dirPart: 'some/path/to',
      filePart: '/file.txt',
    });
  });

  it('leaves a top-level file with no dir part', () => {
    expect(splitFinderPath('README.md')).toEqual({ dirPart: '', filePart: 'README.md' });
  });
});

describe('escapeMentionPath', () => {
  it('escapes spaces with a leading backslash', () => {
    expect(escapeMentionPath('foo bar.txt')).toBe('foo\\ bar.txt');
  });

  it('escapes double quotes with a leading backslash', () => {
    expect(escapeMentionPath('a"b.txt')).toBe('a\\"b.txt');
  });

  it('escapes backslashes first so later escapes are not re-escaped', () => {
    expect(escapeMentionPath('a\\b.txt')).toBe('a\\\\b.txt');
  });

  it('escapes a path combining all three special characters', () => {
    expect(escapeMentionPath('a\\b "c" d.txt')).toBe('a\\\\b\\ \\"c\\"\\ d.txt');
  });
});

describe('buildMentionInsertion', () => {
  it('replaces the @query span with the escaped path plus a trailing space', () => {
    const result = buildMentionInsertion('see @fo for details', 4, 7, 'src/foo bar.ts');
    expect(result.text).toBe('see @src/foo\\ bar.ts  for details');
    expect(result.cursor).toBe(4 + '@src/foo\\ bar.ts '.length);
  });

  it('keeps the @ symbol in the text body', () => {
    const result = buildMentionInsertion('@q', 0, 2, 'a.txt');
    expect(result.text.startsWith('@')).toBe(true);
  });
});

describe('buildDirectoryInsertion', () => {
  it('replaces the @query span with the escaped path plus a trailing slash, no space', () => {
    const result = buildDirectoryInsertion('see @sr for details', 4, 7, 'src');
    expect(result.text).toBe('see @src/ for details');
    expect(result.cursor).toBe(4 + '@src/'.length);
  });
});

describe('ancestorDirectories', () => {
  it('returns no directories for flat paths', () => {
    expect(ancestorDirectories(['a.py', 'b.py'])).toEqual(new Set());
  });

  it('collects every ancestor of a nested path', () => {
    expect(ancestorDirectories(['a/b/c.py'])).toEqual(new Set(['a', 'a/b']));
  });

  it('dedupes shared ancestors across paths', () => {
    expect(ancestorDirectories(['a/b/c.py', 'a/b/d.py', 'a/e.py'])).toEqual(new Set(['a', 'a/b']));
  });
});
