/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import type { SubagentNodeInfo } from 'shared/webviewMessages';
import { useChatMentionFinder } from 'webview/features/chatRoom';

const ROOT_NODE: SubagentNodeInfo = {
  id: 'root-1',
  parentId: null,
  address: '1',
  title: null,
  role: 'operator',
  state: null,
  aborted: false,
  model: 'anthropic/claude-sonnet-5',
  thinkingEnabled: false,
  thinkingEffort: 'medium',
  usedTokens: 0,
  maxTokens: 128000,
  outputTokens: 0,
};

const EXPLORER_NODE: SubagentNodeInfo = {
  id: 'subagent-1',
  parentId: 'root-1',
  address: '1.1',
  title: 'find the bug',
  role: 'explorer',
  state: 'running',
  aborted: false,
  model: 'moonshotai/kimi-k2.7-code',
  thinkingEnabled: true,
  thinkingEffort: 'high',
  usedTokens: 500,
  maxTokens: null,
  outputTokens: 50,
};

const NODES = [ROOT_NODE, EXPLORER_NODE];

afterEach(cleanup);

describe('useChatMentionFinder', () => {
  it('is closed until an @ mention is synced', () => {
    const { result } = renderHook(() => useChatMentionFinder(NODES));
    expect(result.current.isOpen).toBe(false);
  });

  it('opens with every live participant when @ is typed at the start', () => {
    const { result } = renderHook(() => useChatMentionFinder(NODES));

    act(() => result.current.sync('@', 1));

    expect(result.current.isOpen).toBe(true);
    expect(result.current.matches).toHaveLength(2);
  });

  it('fuzzy filters participants as the user types a query', () => {
    const { result } = renderHook(() => useChatMentionFinder(NODES));

    act(() => result.current.sync('@expl', 5));

    expect(result.current.isOpen).toBe(true);
    expect(result.current.matches).toHaveLength(1);
    expect(result.current.matches[0]!.nickname).toBe('explorer-1.1');
  });

  it('dismisses when no participants match the query', () => {
    const { result } = renderHook(() => useChatMentionFinder(NODES));

    act(() => result.current.sync('@zzzzzznomatch', 14));

    expect(result.current.isOpen).toBe(false);
  });

  it('select() replaces @query with @nickname and a trailing space, then closes', () => {
    const { result } = renderHook(() => useChatMentionFinder(NODES));
    act(() => result.current.sync('hey @expl there', 9));
    expect(result.current.isOpen).toBe(true);

    let selection: ReturnType<typeof result.current.select>;
    act(() => {
      selection = result.current.select('hey @expl there', 0);
    });
    expect(selection!).toBeDefined();
    expect(selection!.text).toBe('hey @explorer-1.1  there');
    expect(selection!.cursor).toBe(4 + '@explorer-1.1 '.length);
    expect(result.current.isOpen).toBe(false);
  });

  it('select() returns undefined when there are no matches', () => {
    const { result } = renderHook(() => useChatMentionFinder(NODES));

    expect(result.current.select('no mention', 0)).toBeUndefined();
  });

  it('moveActive wraps around both ends of the match list', () => {
    const { result } = renderHook(() => useChatMentionFinder(NODES));
    act(() => result.current.sync('@', 1));
    const count = result.current.matches.length;

    act(() => result.current.moveActive(-1));
    expect(result.current.activeIndex).toBe(count - 1);

    act(() => result.current.moveActive(1));
    expect(result.current.activeIndex).toBe(0);
  });

  it('dismiss() closes the popup without forgetting the mention', () => {
    const { result } = renderHook(() => useChatMentionFinder(NODES));
    act(() => result.current.sync('@expl', 5));

    act(() => result.current.dismiss());
    expect(result.current.isOpen).toBe(false);

    act(() => result.current.sync('@expl2', 6));
    expect(result.current.isOpen).toBe(false);
  });

  it('does not open for @ in the middle of a word', () => {
    const { result } = renderHook(() => useChatMentionFinder(NODES));

    act(() => result.current.sync('foo@bar', 7));

    expect(result.current.isOpen).toBe(false);
  });
});
