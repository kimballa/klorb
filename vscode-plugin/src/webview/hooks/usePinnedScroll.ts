// © Copyright 2026 Aaron Kimball
import { type RefObject, useCallback, useEffect, useRef } from 'react';

/** Whether a scrolling container showing `scrollTop`/`scrollHeight`/`clientHeight` is close
 * enough to its bottom edge to count as "pinned" -- duplicated from `webview/features/history/
 * historyModel.ts`'s own `isScrollPinnedToBottom` rather than imported, since that module lives
 * in the `history` feature and this hook is a cross-feature primitive (used by both the root
 * history view and the subagents panel's transcript view) that must not import from either of
 * its own consumers. */
function isScrollPinnedToBottom(
  scrollTop: number,
  scrollHeight: number,
  clientHeight: number,
  thresholdPx = 24
): boolean {
  return scrollHeight - scrollTop - clientHeight <= thresholdPx;
}

export interface PinnedScroll<T extends HTMLElement> {
  /** Attach to the scrolling container's `ref`. */
  containerRef: RefObject<T | null>;
  /** Scrolls the container to its last child, but only if the reader hadn't already scrolled
   * away from the bottom -- call this from the caller's own `useEffect` keyed on whatever content
   * change should follow the bottom (e.g. `[entries]`), so a reader who scrolled up mid-turn to
   * reread earlier output isn't yanked back down by new content. Stable across renders
   * (`useCallback` with no deps, since it only reads refs), so including it in a caller's own
   * effect dependency array never itself triggers that effect. */
  scrollToBottomIfPinned(): void;
}

/**
 * Tracks whether a scrolling container is pinned to its bottom edge, and exposes a stable
 * `scrollToBottomIfPinned()` the owner calls from its own content-change effect -- the "follow
 * new content to the bottom unless the reader scrolled away" behavior `App.tsx`'s root
 * `#history` view and the subagents panel's `#subagent-history` view both need (mirroring the
 * TUI's `_history_pinned_to_bottom`/`_subagent_history_pinned_to_bottom`), factored out once a
 * second call site needed the exact same ref/listener/threshold logic.
 */
export default function usePinnedScroll<T extends HTMLElement = HTMLDivElement>(): PinnedScroll<T> {
  const containerRef = useRef<T>(null);
  const pinnedRef = useRef(true);

  useEffect(() => {
    const container = containerRef.current;
    if (container === null) {
      return undefined;
    }
    function onScroll(): void {
      if (container === null) {
        return;
      }
      pinnedRef.current = isScrollPinnedToBottom(
        container.scrollTop,
        container.scrollHeight,
        container.clientHeight
      );
    }
    container.addEventListener('scroll', onScroll);
    return () => container.removeEventListener('scroll', onScroll);
  }, []);

  const scrollToBottomIfPinned = useCallback(() => {
    if (pinnedRef.current) {
      containerRef.current?.lastElementChild?.scrollIntoView({ block: 'end' });
    }
  }, []);

  return { containerRef, scrollToBottomIfPinned };
}
