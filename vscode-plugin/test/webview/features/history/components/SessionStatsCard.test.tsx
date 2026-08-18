/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { SessionStatsCard, type SessionStatsHistoryEntry } from 'webview/features/history';

afterEach(cleanup);

const ENTRY: SessionStatsHistoryEntry = {
  kind: 'sessionStats',
  id: 'stats-1',
  messageCounts: {
    'User messages': 3,
    'Response messages': 3,
  },
  toolBreakdown: [{ name: 'Bash', succeeded: 3, failed: 1 }],
  tokenUsage: {
    'Input tokens': 22558,
    'Cached tokens': 0,
    'Uncached tokens': 22558,
    'Output tokens': 73,
    'Total tokens': 22631,
    'In+out tokens': 22631,
  },
  cachePercent: 0,
  totalCost: 0.003,
};

describe('SessionStatsCard', () => {
  it('renders message counts with comma-grouped, right-aligned values', () => {
    render(<SessionStatsCard entry={ENTRY} />);
    expect(screen.getByText('User messages')).toBeTruthy();
    expect(screen.getAllByText('3')).toHaveLength(3);
    expect(screen.getAllByText('22,558')).toHaveLength(2);
  });

  it('renders the per-tool breakdown as a table with aligned columns', () => {
    render(<SessionStatsCard entry={ENTRY} />);
    expect(screen.getByText('Per-tool breakdown')).toBeTruthy();
    expect(screen.getByText('Bash')).toBeTruthy();
    expect(screen.getByText('Tool')).toBeTruthy();
    expect(screen.getByText('Succeeded')).toBeTruthy();
    expect(screen.getByText('Failed')).toBeTruthy();
    expect(screen.getByText('Total')).toBeTruthy();
  });

  it('omits the per-tool breakdown section when no tools ran', () => {
    render(<SessionStatsCard entry={{ ...ENTRY, toolBreakdown: [] }} />);
    expect(screen.queryByText('Per-tool breakdown')).toBeNull();
  });

  it('shows the cache percentage only on the Cached tokens row', () => {
    const { container } = render(<SessionStatsCard entry={{ ...ENTRY, cachePercent: 12.5 }} />);
    expect(screen.getByText('(12.5%)')).toBeTruthy();
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const notes = container.querySelectorAll('.session-stats-note:not(.session-stats-tool-note)');
    const nonEmpty = Array.from(notes).filter((note) => note.textContent !== '');
    expect(nonEmpty).toHaveLength(1);
  });

  it('draws a 4-cell rule immediately above the Total tokens row', () => {
    const { container } = render(<SessionStatsCard entry={ENTRY} />);
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const grid = container.querySelector('.session-stats-grid');
    // eslint-disable-next-line testing-library/no-node-access
    const children = Array.from(grid?.children ?? []);
    const firstCellIndex = children.findIndex((child) =>
      child.classList.contains('session-stats-rule-cell')
    );
    expect(firstCellIndex).toBeGreaterThan(-1);
    for (let i = 0; i < 4; i++) {
      expect(children[firstCellIndex + i]?.classList.contains('session-stats-rule-cell')).toBe(
        true
      );
    }
    // The 5th (trailing-spacer) cell of the rule "row" is plain, not a rule-cell -- then the
    // next row's label starts immediately after it.
    expect(children[firstCellIndex + 4]?.classList.contains('session-stats-rule-cell')).toBe(false);
    expect(children[firstCellIndex + 5]?.textContent).toBe('Total tokens');
  });

  it('keeps every row label immediately adjacent to its own value (no grid auto-placement drift)', () => {
    // Regression test: a rule row built from one `grid-column`-spanning div (rather than five
    // individually auto-placed cells) leaves its row's last column free for the *next*
    // auto-placed item to slide into, desyncing every row after it from the grid's five
    // columns -- labels and values end up on unrelated rows instead of side by side.
    const { container } = render(<SessionStatsCard entry={ENTRY} />);
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const grid = container.querySelector('.session-stats-grid');
    // eslint-disable-next-line testing-library/no-node-access
    const children = Array.from(grid?.children ?? []);
    const labelIndex = children.findIndex((child) => child.textContent === 'Total tokens');
    expect(labelIndex).toBeGreaterThan(-1);
    // label, spacer, value: the value cell is two children after the label.
    expect(children[labelIndex + 2]?.textContent).toBe('22,631');

    const inOutIndex = children.findIndex((child) => child.textContent === 'In+out tokens');
    expect(children[inOutIndex + 2]?.textContent).toBe('22,631');

    const costIndex = children.findIndex((child) => child.textContent === 'Cost');
    expect(children[costIndex + 2]?.textContent).toBe('$0.003');
  });

  it('renders the total cost with 3 decimal places', () => {
    render(<SessionStatsCard entry={ENTRY} />);
    expect(screen.getByText('$0.003')).toBeTruthy();
  });
});
