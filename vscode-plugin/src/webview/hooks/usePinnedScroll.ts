// © Copyright 2026 Aaron Kimball
import { type Ref, useCallback, useRef } from 'react';
import type { VirtuosoHandle } from 'react-virtuoso';

export interface PinnedScroll {
  /** Ref for the windowed list -- attach to `HistoryView`'s `historyRef` prop. */
  virtuosoRef: Ref<VirtuosoHandle>;
  /** Pass straight through to `HistoryView`'s `onAtBottomStateChange` prop, so this hook's
   * pinned state tracks the windowed list's own bottom-edge detection. */
  handleAtBottomStateChange(atBottom: boolean): void;
  /** Scrolls to the last item, but only if the reader hadn't already scrolled away from the
   * bottom -- call this from the caller's own `useEffect` keyed on whatever content change
   * should follow the bottom (e.g. `[entries]`), so a reader who scrolled up mid-turn to reread
   * earlier output isn't yanked back down by new content. Stable across renders (`useCallback`
   * with no deps, since it only reads refs), so including it in a caller's own effect dependency
   * array never itself triggers that effect. */
  scrollToBottomIfPinned(): void;
  /** Scrolls to the last item unconditionally and resets pin state to `true` -- use when the
   * view identity changes (e.g. switching between root and subagent transcripts) and the reader
   * should always land at the bottom regardless of prior scroll position. Stable across renders
   * for the same reason as `scrollToBottomIfPinned`. */
  scrollToBottom(): void;
}

/**
 * Tracks whether the windowed history list is pinned to its bottom edge, and exposes a stable
 * `scrollToBottomIfPinned()` the owner calls from its own content-change effect -- the "follow
 * new content to the bottom unless the reader scrolled away" behavior `App.tsx`'s root
 * `#history` view and the subagents panel's `#subagent-history` view both need (mirroring the
 * TUI's `_history_pinned_to_bottom`/`_subagent_history_pinned_to_bottom`), factored out once a
 * second call site needed the exact same ref/pin-state logic. Routes through `react-virtuoso`'s
 * own `atBottomStateChange`/`scrollToIndex` API rather than raw `scrollTop`/`scrollHeight` DOM
 * reads, since `HistoryView` windows its content and the underlying scroll node isn't a plain,
 * fully-populated `<div>` the caller can query directly. `itemCount` (the current
 * `entries.length`) is read through a ref updated on every render rather than threaded into the
 * scroll callbacks' own arguments, so those callbacks can stay stable (`useCallback` with no
 * deps) without forcing the caller to add `entries` to an effect's dependency array it
 * deliberately keeps narrower than that.
 */
export default function usePinnedScroll(itemCount: number): PinnedScroll {
  const pinnedRef = useRef(true);
  const handleRef = useRef<VirtuosoHandle | null>(null);
  const itemCountRef = useRef(itemCount);
  itemCountRef.current = itemCount;

  const virtuosoRef = useCallback((node: VirtuosoHandle | null) => {
    handleRef.current = node;
  }, []);

  const handleAtBottomStateChange = useCallback((atBottom: boolean) => {
    pinnedRef.current = atBottom;
  }, []);

  const scrollToBottomIfPinned = useCallback(() => {
    if (pinnedRef.current && itemCountRef.current > 0) {
      handleRef.current?.scrollToIndex({ index: itemCountRef.current - 1, align: 'end' });
    }
  }, []);

  const scrollToBottom = useCallback(() => {
    if (itemCountRef.current > 0) {
      handleRef.current?.scrollToIndex({ index: itemCountRef.current - 1, align: 'end' });
    }
    pinnedRef.current = true;
  }, []);

  return { virtuosoRef, handleAtBottomStateChange, scrollToBottomIfPinned, scrollToBottom };
}
