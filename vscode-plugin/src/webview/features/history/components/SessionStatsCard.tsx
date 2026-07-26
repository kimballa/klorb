// © Copyright 2026 Aaron Kimball
import type { JSX } from 'react';

import type { SessionStatsHistoryEntry } from '../historyModel';

export interface SessionStatsCardProps {
  entry: SessionStatsHistoryEntry;
}

interface StatRowProps {
  label: string;
  value: number;
  note?: string;
  formatValue?: (value: number) => string;
}

/** One label/value/note row -- five plain grid items (not a wrapper element), so they land as
 * direct children of the shared `.session-stats-grid` container and size into its five
 * columns (label, a `1fr` breathing-room spacer, value, note, a trailing `2fr` spacer that
 * keeps the numbers from spreading edge-to-edge) alongside every other row, guaranteeing
 * every section's value column (message counts, token usage, cost) lines up in the same
 * place. The two spacer `<span>`s carry no content -- they exist purely to reserve their grid
 * columns' width. */
function StatRow({ label, value, note, formatValue }: StatRowProps): JSX.Element {
  return (
    <>
      <span className="session-stats-label">{label}</span>
      <span></span>
      <span className="session-stats-value">
        {formatValue ? formatValue(value) : value.toLocaleString()}
      </span>
      <span className="session-stats-note">{note ?? ''}</span>
      <span></span>
    </>
  );
}

/**
 * Renders a "Show Session Stats" result as a history entry: message/tool-call counts, an
 * optional per-tool breakdown, and token usage, all sharing one CSS grid so their value
 * columns align regardless of which section a row belongs to -- a rule between "Output
 * tokens" and "Total tokens" marks where the summary rows start (four individually
 * auto-placed `.session-stats-rule-cell`s, not one `grid-column`-spanning div -- see the
 * inline comment where it's built for why a partial-span item there breaks every row after
 * it), the "Cached tokens" row carries the cache-hit percentage as a dim note column, and the
 * total cost sits in its own spaced-apart row at the end. Mirrors
 * `klorb.session_statistics.SessionStatistics.
 * format_report()`'s TUI layout (see docs/specs/vscode-plugin.md's status row and session
 * controls section), rendered as a real grid instead of preformatted monospace text.
 */
export default function SessionStatsCard({ entry }: SessionStatsCardProps): JSX.Element {
  const tokenRows = Object.entries(entry.tokenUsage);
  return (
    <div className="entry session-stats">
      <div className="session-stats-grid">
        <div className="session-stats-title">Session Statistics</div>
        {Object.entries(entry.messageCounts).map(([label, value]) => (
          <StatRow key={label} label={label} value={value} />
        ))}
        {entry.toolBreakdown.length > 0 ? (
          <>
            <div className="session-stats-subtitle">Per-tool breakdown</div>
            {entry.toolBreakdown.map((tool) => (
              <div className="session-stats-tool-row" key={tool.name}>
                <span className="session-stats-label">{tool.name}</span>
                <span className="session-stats-tool-detail">
                  {tool.succeeded} succeeded, {tool.failed} failed ({tool.succeeded + tool.failed}{' '}
                  total)
                </span>
              </div>
            ))}
          </>
        ) : null}
        <div className="session-stats-title session-stats-section-spaced">Token Usage</div>
        {tokenRows.flatMap(([label, value]) => {
          const row = (
            <StatRow
              key={label}
              label={label}
              value={value}
              note={label === 'Cached tokens' ? `(${entry.cachePercent.toFixed(1)}%)` : undefined}
            />
          );
          if (label !== 'Total tokens') {
            return [row];
          }
          // Four individually auto-placed, bordered cells (not one `grid-column`-spanning
          // div) plus a fifth, unbordered one -- every row in this grid, without exception,
          // must consume exactly five auto-placed cells. A partial-span item here would leave
          // its row's last column free for the *next* auto-placed item (the following row's
          // own first cell) to slide into, which desyncs every row after it from the grid's
          // five columns (see docs/specs/vscode-plugin.md's status row and session controls
          // section for how this actually broke rendering).
          return [
            <span className="session-stats-rule-cell" key={`${label}-rule-1`}></span>,
            <span className="session-stats-rule-cell" key={`${label}-rule-2`}></span>,
            <span className="session-stats-rule-cell" key={`${label}-rule-3`}></span>,
            <span className="session-stats-rule-cell" key={`${label}-rule-4`}></span>,
            <span key={`${label}-rule-5`}></span>,
            row,
          ];
        })}
        <div className="session-stats-spacer" />
        <StatRow label="Cost" value={entry.totalCost} formatValue={(v) => `$${v.toFixed(3)}`} />
      </div>
    </div>
  );
}
