/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import type { SkillEntry } from 'shared/webviewMessages';
import { useSkillFinder } from 'webview/features/skillFinder';

const SKILLS: SkillEntry[] = [
  { namespace: 'workspace', name: 'code-review', description: 'Review code for issues' },
  {
    namespace: 'internal',
    name: 'debug-with-evidence',
    description: 'Debug by gathering evidence',
  },
  { namespace: 'workspace', name: 'write-plan', description: 'Write an implementation plan' },
];

afterEach(cleanup);

describe('useSkillFinder', () => {
  it('is closed until a / mention is synced', () => {
    const { result } = renderHook(() => useSkillFinder(SKILLS));
    expect(result.current.isOpen).toBe(false);
  });

  it('opens with all skills when / is typed at the start', () => {
    const { result } = renderHook(() => useSkillFinder(SKILLS));

    act(() => result.current.sync('/', 1));

    expect(result.current.isOpen).toBe(true);
    expect(result.current.matches).toHaveLength(3);
    expect(result.current.activeIndex).toBe(0);
  });

  it('opens with all skills when / is typed after whitespace', () => {
    const { result } = renderHook(() => useSkillFinder(SKILLS));

    act(() => result.current.sync('please /', 8));

    expect(result.current.isOpen).toBe(true);
    expect(result.current.matches).toHaveLength(3);
  });

  it('fuzzy filters skills as the user types a query', () => {
    const { result } = renderHook(() => useSkillFinder(SKILLS));

    act(() => result.current.sync('/code', 5));

    expect(result.current.isOpen).toBe(true);
    expect(result.current.matches).toHaveLength(1);
    expect(result.current.matches[0]!.skill.name).toBe('code-review');
  });

  it('dismisses when no skills match the query', () => {
    const { result } = renderHook(() => useSkillFinder(SKILLS));

    act(() => result.current.sync('/zzzzzznomatch', 14));

    expect(result.current.isOpen).toBe(false);
  });

  it('select() replaces /query with /name and a trailing space, then closes', () => {
    const { result } = renderHook(() => useSkillFinder(SKILLS));
    act(() => result.current.sync('see /code here', 9));
    expect(result.current.isOpen).toBe(true);

    let selection: ReturnType<typeof result.current.select>;
    act(() => {
      selection = result.current.select('see /code here', 0);
    });
    expect(selection!).toBeDefined();
    expect(selection!.text).toBe('see /code-review  here');
    expect(selection!.cursor).toBe(4 + '/code-review '.length);
    expect(result.current.isOpen).toBe(false);
  });

  it('select() returns undefined when there are no matches', () => {
    const { result } = renderHook(() => useSkillFinder(SKILLS));

    expect(result.current.select('no mention', 0)).toBeUndefined();
  });

  it('moveActive wraps around both ends of the match list', () => {
    const { result } = renderHook(() => useSkillFinder(SKILLS));
    act(() => result.current.sync('/', 1));
    const count = result.current.matches.length;

    act(() => result.current.moveActive(-1));
    expect(result.current.activeIndex).toBe(count - 1);

    act(() => result.current.moveActive(1));
    expect(result.current.activeIndex).toBe(0);
  });

  it('dismiss() closes the popup without forgetting the mention', () => {
    const { result } = renderHook(() => useSkillFinder(SKILLS));
    act(() => result.current.sync('/code', 5));

    act(() => result.current.dismiss());
    expect(result.current.isOpen).toBe(false);

    // Typing more within the same mention keeps it dismissed...
    act(() => result.current.sync('/code2', 6));
    expect(result.current.isOpen).toBe(false);
  });

  it('a new / mention reopens the finder after a dismiss', () => {
    const { result } = renderHook(() => useSkillFinder(SKILLS));
    act(() => result.current.sync('/code', 5));
    act(() => result.current.dismiss());

    act(() => result.current.sync('/code second /debug', 19));

    expect(result.current.isOpen).toBe(true);
    expect(result.current.matches).toHaveLength(1);
    expect(result.current.matches[0]!.skill.name).toBe('debug-with-evidence');
  });

  it('does not open for / in the middle of a word', () => {
    const { result } = renderHook(() => useSkillFinder(SKILLS));

    act(() => result.current.sync('foo/bar', 7));

    expect(result.current.isOpen).toBe(false);
  });

  it('does not open for / followed by a digit', () => {
    const { result } = renderHook(() => useSkillFinder(SKILLS));

    act(() => result.current.sync('/123', 4));

    expect(result.current.isOpen).toBe(false);
  });
});
