// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import {
  buildSkillInsertion,
  detectSkillMention,
  splitSkillDisplay,
} from 'webview/features/skillFinder';

describe('detectSkillMention', () => {
  it('finds a mention at the start of the text', () => {
    expect(detectSkillMention('/foo', 4)).toEqual({ start: 0, query: 'foo' });
  });

  it('finds a mention preceded by whitespace', () => {
    expect(detectSkillMention('please /src', 11)).toEqual({
      start: 7,
      query: 'src',
    });
  });

  it('returns an empty query right after the / itself', () => {
    expect(detectSkillMention('hello /', 7)).toEqual({ start: 6, query: '' });
  });

  it('does not trigger on a / not preceded by whitespace or start', () => {
    expect(detectSkillMention('foo/bar', 7)).toBeUndefined();
  });

  it('does not trigger once whitespace separates the / from the cursor', () => {
    expect(detectSkillMention('/foo bar', 8)).toBeUndefined();
  });

  it('does not trigger when there is no / before the cursor', () => {
    expect(detectSkillMention('no mention here', 16)).toBeUndefined();
  });

  it('finds the mention when the cursor sits mid-query', () => {
    expect(detectSkillMention('/abcdef rest', 4)).toEqual({ start: 0, query: 'abc' });
  });

  it('does not trigger on / followed by a non-letter', () => {
    expect(detectSkillMention('/123', 4)).toBeUndefined();
    expect(detectSkillMention('/.git', 5)).toBeUndefined();
  });
});

describe('buildSkillInsertion', () => {
  it('replaces the /query span with /name plus a trailing space', () => {
    const skill = { namespace: 'workspace', name: 'code-review', description: 'Reviews code' };
    const result = buildSkillInsertion('see /co for details', 4, 7, skill);
    expect(result.text).toBe('see /code-review  for details');
    expect(result.cursor).toBe(4 + '/code-review '.length);
  });

  it('keeps the / symbol in the text body', () => {
    const skill = { namespace: 'workspace', name: 'foo', description: '' };
    const result = buildSkillInsertion('/q', 0, 2, skill);
    expect(result.text.startsWith('/')).toBe(true);
  });
});

describe('splitSkillDisplay', () => {
  it('returns name and description', () => {
    const skill = { namespace: 'internal', name: 'add-cli-flag', description: 'Add a CLI flag' };
    expect(splitSkillDisplay(skill)).toEqual({
      name: 'add-cli-flag',
      desc: 'Add a CLI flag',
    });
  });

  it('returns empty description when absent', () => {
    const skill = { namespace: 'workspace', name: 'my-skill', description: '' };
    expect(splitSkillDisplay(skill)).toEqual({ name: 'my-skill', desc: '' });
  });
});
