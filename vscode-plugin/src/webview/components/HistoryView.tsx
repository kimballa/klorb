// © Copyright 2026 Aaron Kimball
import type { JSX, RefObject } from 'react';
import ReactMarkdown from 'react-markdown';

import type { HistoryEntry } from '../historyModel';

interface HistoryViewProps {
  entries: HistoryEntry[];
  /** Ref to the scrolling container, so the owner can keep the newest entry in view. */
  historyRef: RefObject<HTMLDivElement | null>;
}

function renderEntry(entry: HistoryEntry, index: number): JSX.Element {
  switch (entry.kind) {
    case 'prompt':
      return (
        <div className="bubble bubble-prompt" key={index}>
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
    case 'notice':
      return (
        <div className="entry entry-notice" key={index}>
          {entry.text}
        </div>
      );
  }
}

/** The append-only history scroll: prompts as right-aligned bubbles, responses as rendered
 * markdown, thinking as a collapsed-by-default disclosure that streams while open. */
export function HistoryView({ entries, historyRef }: HistoryViewProps): JSX.Element {
  // Entries only ever append here, never reorder or remove, so an index key is stable.
  return (
    <div id="history" ref={historyRef}>
      {entries.map(renderEntry)}
    </div>
  );
}
