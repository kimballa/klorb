/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { useFileFinder, type FileFinderSelection } from 'webview/features/fileFinder';

const FILES = ['src/App.tsx', 'src/AppStyles.ts', 'README.md', 'docs/specs/vscode-plugin.md'];

afterEach(cleanup);

describe('useFileFinder', () => {
  it('is closed until a mention is synced', () => {
    const { result } = renderHook(() => useFileFinder(FILES));
    expect(result.current.isOpen).toBe(false);
  });

  it('opens with fuzzy matches once an @-mention is synced', () => {
    const { result } = renderHook(() => useFileFinder(FILES));

    act(() => result.current.sync('see @App', 8));

    expect(result.current.isOpen).toBe(true);
    expect(result.current.matches).toContainEqual({ path: 'src/App.tsx', isDir: false });
    expect(result.current.activeIndex).toBe(0);
  });

  it('shows directories ahead of files, alphabetically, when the query is empty right after @', () => {
    const { result } = renderHook(() => useFileFinder(FILES));

    act(() => result.current.sync('see @', 5));

    expect(result.current.isOpen).toBe(true);
    expect(result.current.matches).toEqual([
      { path: 'docs', isDir: true },
      { path: 'docs/specs', isDir: true },
      { path: 'src', isDir: true },
      { path: 'README.md', isDir: false },
      { path: 'docs/specs/vscode-plugin.md', isDir: false },
      { path: 'src/App.tsx', isDir: false },
      { path: 'src/AppStyles.ts', isDir: false },
    ]);
  });

  it('dismisses when further typing rules out every file', () => {
    const { result } = renderHook(() => useFileFinder(FILES));

    act(() => result.current.sync('@App', 4));
    expect(result.current.isOpen).toBe(true);

    act(() => result.current.sync('@Appzzzzznomatch', 16));

    expect(result.current.isOpen).toBe(false);
  });

  it('moveActive wraps around both ends of the match list', () => {
    const { result } = renderHook(() => useFileFinder(FILES));
    act(() => result.current.sync('@', 1));
    const count = result.current.matches.length;

    act(() => result.current.moveActive(-1));
    expect(result.current.activeIndex).toBe(count - 1);

    act(() => result.current.moveActive(1));
    expect(result.current.activeIndex).toBe(0);
  });

  it('dismiss() closes the popup without forgetting the mention', () => {
    const { result } = renderHook(() => useFileFinder(FILES));
    act(() => result.current.sync('@App', 4));

    act(() => result.current.dismiss());
    expect(result.current.isOpen).toBe(false);

    // Typing more within the same mention keeps it dismissed...
    act(() => result.current.sync('@App2', 5));
    expect(result.current.isOpen).toBe(false);
  });

  it('a new @ mention reopens the finder after a dismiss', () => {
    const { result } = renderHook(() => useFileFinder(FILES));
    act(() => result.current.sync('@App', 4));
    act(() => result.current.dismiss());

    act(() => result.current.sync('@App second @Rea', 16));

    expect(result.current.isOpen).toBe(true);
    expect(result.current.matches).toContainEqual({ path: 'README.md', isDir: false });
  });

  it('select() splices the chosen path into the text and closes the finder', () => {
    const { result } = renderHook(() => useFileFinder(FILES));
    act(() => result.current.sync('check @App please', 10));

    let selection: FileFinderSelection | undefined;
    act(() => {
      selection = result.current.select('check @App please', 0);
    });

    expect(selection).toEqual({
      text: 'check @src/App.tsx  please',
      cursor: 'check @src/App.tsx '.length,
    });
    expect(result.current.isOpen).toBe(false);
  });

  it('select() returns undefined when there is no active mention', () => {
    const { result } = renderHook(() => useFileFinder(FILES));

    const selection = result.current.select('no mention', 0);

    expect(selection).toBeUndefined();
  });

  it('ranks a directory above an equally-scored file via the score bump', () => {
    const { result } = renderHook(() => useFileFinder(['srcstuff.py', 'src/file.py']));

    act(() => result.current.sync('@src', 4));

    expect(result.current.matches[0]).toEqual({ path: 'src', isDir: true });
  });

  it('select() on a directory narrows the query into that subtree and keeps the finder open', () => {
    const { result } = renderHook(() => useFileFinder(['src/main.ts']));
    act(() => result.current.sync('@src', 4));
    expect(result.current.matches[0]).toEqual({ path: 'src', isDir: true });

    let selection: FileFinderSelection | undefined;
    act(() => {
      selection = result.current.select('@src', 0);
    });

    expect(selection).toEqual({ text: '@src/', cursor: '@src/'.length });
    expect(result.current.isOpen).toBe(true);
    expect(result.current.matches).toContainEqual({ path: 'src/main.ts', isDir: false });
  });
});
