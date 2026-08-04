// © Copyright 2026 Aaron Kimball
import { type RefCallback, useCallback, useRef } from 'react';

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
  /** Callback ref for the scrolling container -- attach to the consuming element's `ref` prop.
   * Re-attaches the internal scroll listener whenever the DOM node changes (mount, unmount,
   * remount), so the pinned-to-bottom tracking survives the container being replaced. */
  containerRef: RefCallback<T>;
  /** Scrolls the container to its last child, but only if the reader hadn't already scrolled
   * away from the bottom -- call this from the caller's own `useEffect` keyed on whatever content
   * change should follow the bottom (e.g. `[entries]`), so a reader who scrolled up mid-turn to
   * reread earlier output isn't yanked back down by new content. Stable across renders
   * (`useCallback` with no deps, since it only reads refs), so including it in a caller's own
   * effect dependency array never itself triggers that effect. */
  scrollToBottomIfPinned(): void;
  /** Scrolls the container to its last child unconditionally and resets pin state to `true` --
   * use when the view identity changes (e.g. switching between root and subagent transcripts)
   * and the reader should always land at the bottom regardless of prior scroll position.
   * Stable across renders for the same reason as `scrollToBottomIfPinned`. */
  scrollToBottom(): void;
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
  const pinnedRef = useRef(true);
  // Tracks the node the scroll listener is currently attached to, so the callback ref can
  // detach from the old node before attaching to a new one.
  const attachedNodeRef = useRef<T | null>(null);

  // Plain function (not an arrow/closure) so the stable callback ref below always reads
  // current values through refs rather than capturing stale closured values.
  function onScroll(): void {
    const node = attachedNodeRef.current;
    if (node === null) {
      return;
    }
    pinnedRef.current = isScrollPinnedToBottom(
      node.scrollTop,
      node.scrollHeight,
      node.clientHeight
    );
  }

  const containerRef = useCallback((node: T | null) => {
    if (attachedNodeRef.current) {
      attachedNodeRef.current.removeEventListener('scroll', onScroll);
    }
    attachedNodeRef.current = node;
    if (node !== null) {
      pinnedRef.current = isScrollPinnedToBottom(
        node.scrollTop,
        node.scrollHeight,
        node.clientHeight
      );
      node.addEventListener('scroll', onScroll);
    }
  }, []);

  const scrollToBottomIfPinned = useCallback(() => {
    const node = attachedNodeRef.current;
    if (pinnedRef.current) {
      node?.lastElementChild?.scrollIntoView({ block: 'end' });
    }
  }, []);

  const scrollToBottom = useCallback(() => {
    const node = attachedNodeRef.current;
    node?.lastElementChild?.scrollIntoView({ block: 'end' });
    pinnedRef.current = true;
  }, []);

  return { containerRef, scrollToBottomIfPinned, scrollToBottom };
}
