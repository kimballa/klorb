// © Copyright 2026 Aaron Kimball
import type { JSX, RefObject } from 'react';
import ReactMarkdown from 'react-markdown';

import type { HistoryEntry } from '../historyModel';

import SessionStatsCard from './SessionStatsCard';
import ToolCallChip from './ToolCallChip';

export interface HistoryViewProps {
  entries: HistoryEntry[];
  /** Ref to the scrolling container, so the owner can keep the newest entry in view. */
  historyRef: RefObject<HTMLDivElement | null>;
  onToggleToolCallExpanded(callId: string): void;
  /** Restarts the `klorb server` child process -- wired to a `'serverError'` entry's "Restart
   * Server" action (see `docs/specs/vscode-plugin.md`'s interrupt-polish section). */
  onRestartServer(): void;
}

function renderEntry(
  entry: HistoryEntry,
  index: number,
  onToggleToolCallExpanded: (callId: string) => void,
  onRestartServer: () => void
): JSX.Element {
  switch (entry.kind) {
    case 'prompt':
      return (
        <div className="bubble bubble-prompt" key={index}>
          {entry.text}
        </div>
      );
    case 'queuedMessage':
      return (
        <div className="bubble bubble-prompt bubble-queued" key={index}>
          <div className="queued-prompt-header">Queued message</div>
          {entry.text}
        </div>
      );
    case 'response':
      return (
        <div className="entry entry-response" key={index}>
          <ReactMarkdown>{entry.text}</ReactMarkdown>
        </div>
      );
    case 'thinking':
      return (
        <details className="entry entry-thinking" key={index}>
          <summary>Thinking…</summary>
          <div className="thinking-text">{entry.text}</div>
        </details>
      );
    case 'error':
      return (
        <div className="entry entry-error" key={index}>
          {entry.text}
        </div>
      );
    case 'serverError':
      return (
        <div className="entry entry-error entry-server-error" key={index}>
          <div>{entry.text}</div>
          <vscode-button secondary onClick={onRestartServer}>
            Restart Server
          </vscode-button>
        </div>
      );
    case 'notice':
      return (
        <div className="entry entry-notice" key={index}>
          {entry.text}
        </div>
      );
    case 'interaction':
      return (
        <div className="entry entry-interaction" key={index}>
          {entry.text}
        </div>
      );
    case 'toolCall':
      return <ToolCallChip entry={entry} onToggleExpanded={onToggleToolCallExpanded} key={index} />;
    case 'sessionStats':
      return <SessionStatsCard entry={entry} key={index} />;
  }
}

/** The append-only history scroll: prompts as right-aligned bubbles, responses as rendered
 * markdown, thinking as a collapsed-by-default disclosure that streams while open, and tool
 * calls as `ToolCallChip`s. `historyRef` points at the scrolling entries container so the
 * owner's scroll-into-view logic is unaffected. */
export default function HistoryView({
  entries,
  historyRef,
  onToggleToolCallExpanded,
  onRestartServer,
}: HistoryViewProps): JSX.Element {
  return (
    <div id="history" ref={historyRef}>
      {entries.map((entry, index) =>
        renderEntry(entry, index, onToggleToolCallExpanded, onRestartServer)
      )}
    </div>
  );
}
