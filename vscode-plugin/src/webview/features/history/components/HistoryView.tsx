// © Copyright 2026 Aaron Kimball
import type { JSX, RefObject } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkFrontmatter from 'remark-frontmatter';
import remarkGfm from 'remark-gfm';

import AttachmentThumbnail from 'webview/components/AttachmentThumbnail';

import type { HistoryEntry } from '../historyModel';
import { renderYamlFrontmatter } from '../renderYamlFrontmatter';

import BashToolCallChip from './BashToolCallChip';
import MentionHighlightedText from './MentionHighlightedText';
import SessionStatsCard from './SessionStatsCard';
import ToolCallChip from './ToolCallChip';

const REMARK_REHYPE_OPTIONS = { handlers: { yaml: renderYamlFrontmatter } };

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
          {entry.images !== undefined && entry.images.length > 0 ? (
            <div className="prompt-attachment-tray">
              {entry.images.map((image, imageIndex) => (
                <AttachmentThumbnail key={imageIndex} image={image} />
              ))}
            </div>
          ) : null}
          <MentionHighlightedText text={entry.text} />
        </div>
      );
    case 'queuedMessage':
      return (
        <div className="bubble bubble-prompt bubble-queued" key={index}>
          <div className="queued-prompt-header">Queued message</div>
          <MentionHighlightedText text={entry.text} />
        </div>
      );
    case 'response':
      return (
        <div className="entry entry-response" key={index}>
          <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkFrontmatter]}
            remarkRehypeOptions={REMARK_REHYPE_OPTIONS}>
            {entry.text}
          </ReactMarkdown>
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
      if (entry.bashMeta !== undefined) {
        return (
          <BashToolCallChip entry={entry} onToggleExpanded={onToggleToolCallExpanded} key={index} />
        );
      }
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
