/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render } from '@testing-library/react';
import ReactMarkdown from 'react-markdown';
import { VirtuosoMockContext } from 'react-virtuoso';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Mock } from 'vitest';

import { HistoryView } from 'webview/features/history';
import type { HistoryEntry } from 'webview/features/history';

vi.mock('react-markdown', () => ({
  default: vi.fn((props: { children: string }) => <div>{props.children}</div>),
}));

afterEach(cleanup);

function markdownCallsFor(text: string): number {
  return (ReactMarkdown as unknown as Mock).mock.calls.filter(
    (call) => (call[0] as { children: string }).children === text
  ).length;
}

function makeEntries(count: number): HistoryEntry[] {
  return Array.from({ length: count }, (_, i) => ({
    kind: 'response',
    text: `Entry ${i}`,
    streaming: false,
    id: `entry-${i}`,
  }));
}

describe('HistoryView entry memoization', () => {
  it('does not re-render an earlier response entry when a later entry streams a new chunk', () => {
    const entryA: HistoryEntry = { kind: 'response', text: 'Entry A', streaming: false, id: 'a' };
    const entryB: HistoryEntry = { kind: 'response', text: 'Entry B', streaming: true, id: 'b' };
    const onToggleToolCallExpanded = vi.fn();
    const onRestartServer = vi.fn();
    const onAtBottomStateChange = vi.fn();

    const { rerender } = render(
      <VirtuosoMockContext.Provider value={{ viewportHeight: 100000, itemHeight: 30 }}>
        <HistoryView
          entries={[entryA, entryB]}
          historyRef={null}
          allThinkingExpanded={false}
          onToggleToolCallExpanded={onToggleToolCallExpanded}
          onRestartServer={onRestartServer}
          onAtBottomStateChange={onAtBottomStateChange}
        />
      </VirtuosoMockContext.Provider>
    );
    expect(markdownCallsFor('Entry A')).toBe(1);

    const streamedEntryB: HistoryEntry = { ...entryB, text: 'Entry B chunk' };
    rerender(
      <VirtuosoMockContext.Provider value={{ viewportHeight: 100000, itemHeight: 30 }}>
        <HistoryView
          entries={[entryA, streamedEntryB]}
          historyRef={null}
          allThinkingExpanded={false}
          onToggleToolCallExpanded={onToggleToolCallExpanded}
          onRestartServer={onRestartServer}
          onAtBottomStateChange={onAtBottomStateChange}
        />
      </VirtuosoMockContext.Provider>
    );

    expect(markdownCallsFor('Entry A')).toBe(1);
    expect(markdownCallsFor('Entry B chunk')).toBe(1);
  });
});

describe('HistoryView windowing', () => {
  it('keeps the rendered DOM node count bounded for a long entries list', () => {
    const entries = makeEntries(500);

    const { container } = render(
      <VirtuosoMockContext.Provider value={{ viewportHeight: 300, itemHeight: 30 }}>
        <HistoryView
          entries={entries}
          historyRef={null}
          allThinkingExpanded={false}
          onToggleToolCallExpanded={vi.fn()}
          onRestartServer={vi.fn()}
          onAtBottomStateChange={vi.fn()}
        />
      </VirtuosoMockContext.Provider>
    );

    // No Testing Library query exists for "how many windowed item wrappers are mounted" --
    // that's exactly the DOM-node-count fact this test verifies.
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const renderedIndices = container.querySelectorAll('.history-item');
    expect(renderedIndices.length).toBeGreaterThan(0);
    // A 300px viewport of 30px-tall rows fits ~10 at a time; well under the full 500-entry list.
    expect(renderedIndices.length).toBeLessThan(50);
  });

  it('mounts earlier content once the reader scrolls to it', () => {
    const entries = makeEntries(500);

    const { container } = render(
      <VirtuosoMockContext.Provider value={{ viewportHeight: 300, itemHeight: 30 }}>
        <HistoryView
          entries={entries}
          historyRef={null}
          allThinkingExpanded={false}
          onToggleToolCallExpanded={vi.fn()}
          onRestartServer={vi.fn()}
          onAtBottomStateChange={vi.fn()}
        />
      </VirtuosoMockContext.Provider>
    );

    expect(container.textContent).toContain('Entry 0');
    expect(container.textContent).not.toContain('Entry 300');

    // No Testing Library query reaches react-virtuoso's own scroller div by role/text.
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const scroller = container.querySelector('[data-testid="virtuoso-scroller"]');
    expect(scroller).not.toBeNull();
    Object.defineProperty(scroller, 'scrollTop', {
      value: 9000,
      writable: true,
      configurable: true,
    });
    fireEvent.scroll(scroller as Element);

    expect(container.textContent).toContain('Entry 300');
    expect(container.textContent).not.toContain('Entry 0');
  });
});
