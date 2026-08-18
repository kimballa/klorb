/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, render } from '@testing-library/react';
import ReactMarkdown from 'react-markdown';
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

describe('HistoryView entry memoization', () => {
  it('does not re-render an earlier response entry when a later entry streams a new chunk', () => {
    const entryA: HistoryEntry = { kind: 'response', text: 'Entry A', streaming: false };
    const entryB: HistoryEntry = { kind: 'response', text: 'Entry B', streaming: true };
    const onToggleToolCallExpanded = vi.fn();
    const onRestartServer = vi.fn();

    const { rerender } = render(
      <HistoryView
        entries={[entryA, entryB]}
        historyRef={null}
        allThinkingExpanded={false}
        onToggleToolCallExpanded={onToggleToolCallExpanded}
        onRestartServer={onRestartServer}
      />
    );
    expect(markdownCallsFor('Entry A')).toBe(1);

    const streamedEntryB: HistoryEntry = { ...entryB, text: 'Entry B chunk' };
    rerender(
      <HistoryView
        entries={[entryA, streamedEntryB]}
        historyRef={null}
        allThinkingExpanded={false}
        onToggleToolCallExpanded={onToggleToolCallExpanded}
        onRestartServer={onRestartServer}
      />
    );

    expect(markdownCallsFor('Entry A')).toBe(1);
    expect(markdownCallsFor('Entry B chunk')).toBe(1);
  });
});
