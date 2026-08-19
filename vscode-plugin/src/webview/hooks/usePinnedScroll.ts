// © Copyright 2026 Aaron Kimball
import { type Ref, useCallback, useRef } from 'react';
import type { VirtuosoHandle } from 'react-virtuoso';

export interface PinnedScroll {
  /** Ref for the windowed list. */
  virtuosoRef: Ref<VirtuosoHandle>;
  /** Pass straight through to `HistoryView`'s `onAtBottomStateChange` prop, so this hook's
   * pinned state tracks the windowed list's own bottom-edge detection. */
  handleAtBottomStateChange(atBottom: boolean): void;
  /** Scrolls to the last item, but only if the reader hadn't already scrolled away from the
   * bottom. */
  scrollToBottomIfPinned(): void;
  /** Scrolls to the last item unconditionally and resets pin state to `true`. */
  scrollToBottom(): void;
}

/**
 * Tracks whether the windowed history list is pinned to its bottom edge, and exposes a stable
 * `scrollToBottomIfPinned()` the owner calls from its own content-change effect.
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
