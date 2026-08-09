// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import { parseSkillActivationIdentity, parseSystemInterjections } from 'webview/features/history';

describe('parseSystemInterjections', () => {
  it('returns unchanged text when no interjections are present', () => {
    const result = parseSystemInterjections('Hello, world!');
    expect(result).toEqual({ interjections: [], remainingText: 'Hello, world!' });
  });

  it('extracts a single leading interjection and strips it from the text', () => {
    const text =
      '<SystemInterjection subject="metadata">\nsome metadata\n</SystemInterjection>\n\nActual user message';
    const result = parseSystemInterjections(text);
    expect(result.interjections).toEqual([{ subject: 'metadata', body: 'some metadata\n' }]);
    expect(result.remainingText).toBe('Actual user message');
  });

  it('extracts multiple stacked interjections', () => {
    const text = [
      '<SystemInterjection subject="first">',
      'first body',
      '</SystemInterjection>',
      '<SystemInterjection subject="second">',
      'second body',
      '</SystemInterjection>',
      '',
      'The real message',
    ].join('\n');
    const result = parseSystemInterjections(text);
    expect(result.interjections).toEqual([
      { subject: 'first', body: 'first body\n' },
      { subject: 'second', body: 'second body\n' },
    ]);
    expect(result.remainingText).toBe('The real message');
  });

  it('strips leading whitespace before the real message', () => {
    const text = '<SystemInterjection subject="x">\nbody\n</SystemInterjection>\n\n\n   Real text';
    const result = parseSystemInterjections(text);
    expect(result.interjections).toHaveLength(1);
    expect(result.remainingText).toBe('Real text');
  });

  it('returns empty remaining text when the message is all interjections', () => {
    const text = '<SystemInterjection subject="a">\nfoo\n</SystemInterjection>';
    const result = parseSystemInterjections(text);
    expect(result.interjections).toEqual([{ subject: 'a', body: 'foo\n' }]);
    expect(result.remainingText).toBe('');
  });

  it('does not extract interjections that are not at the start', () => {
    const text = 'Some text\n<SystemInterjection subject="x">\ninner\n</SystemInterjection>';
    const result = parseSystemInterjections(text);
    expect(result.interjections).toEqual([]);
    expect(result.remainingText).toBe(text);
  });

  it('handles an interjection with an empty body', () => {
    const text = '<SystemInterjection subject="empty">\n</SystemInterjection>\nreal';
    const result = parseSystemInterjections(text);
    expect(result.interjections).toEqual([{ subject: 'empty', body: '' }]);
    expect(result.remainingText).toBe('real');
  });

  it('handles subjects with hyphens and mixed case', () => {
    const text =
      '<SystemInterjection subject="ProjectGuidance">\ncontent here\n</SystemInterjection>\nmsg';
    const result = parseSystemInterjections(text);
    expect(result.interjections[0]?.subject).toBe('ProjectGuidance');
    expect(result.remainingText).toBe('msg');
  });

  it('breaks on a missing closing tag and does not consume the malformed block', () => {
    const text = '<SystemInterjection subject="x">\nno close tag here';
    const result = parseSystemInterjections(text);
    expect(result.interjections).toEqual([]);
    expect(result.remainingText).toBe(text);
  });

  it('returns original text when input is empty', () => {
    const result = parseSystemInterjections('');
    expect(result).toEqual({ interjections: [], remainingText: '' });
  });
});

describe('parseSkillActivationIdentity', () => {
  it('extracts namespace/name from a UserSkillActivation payload', () => {
    const body =
      'The user has invoked skill do-thing. Read the skill JSON that follows plus the ' +
      "user's prompt, then apply this skill:\n" +
      '{"namespace": "workspace", "name": "do-thing", "content": "steps", "files": [], "tokens": 3}';
    expect(parseSkillActivationIdentity(body)).toEqual({
      namespace: 'workspace',
      name: 'do-thing',
    });
  });

  it('returns undefined when the body has no JSON payload', () => {
    expect(parseSkillActivationIdentity('no json here')).toBeUndefined();
  });

  it('returns undefined when the JSON payload is malformed', () => {
    expect(parseSkillActivationIdentity('prefix {not valid json')).toBeUndefined();
  });

  it('returns undefined when namespace/name are missing from the payload', () => {
    expect(parseSkillActivationIdentity('{"content": "steps"}')).toBeUndefined();
  });
});
