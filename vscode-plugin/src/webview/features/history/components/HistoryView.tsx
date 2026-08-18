// © Copyright 2026 Aaron Kimball
import { Fragment, type JSX, memo, type Ref } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkFrontmatter from 'remark-frontmatter';
import remarkGfm from 'remark-gfm';

import AttachmentThumbnail from 'webview/components/AttachmentThumbnail';

import type { HistoryEntry } from '../historyModel';
import {
  type ParsedSystemInterjection,
  parseSkillActivationIdentity,
  parseSystemInterjections,
} from '../parseSystemInterjections';
import { renderYamlFrontmatter } from '../renderYamlFrontmatter';

import BashToolCallChip from './BashToolCallChip';
import MentionHighlightedText from './MentionHighlightedText';
import SessionStatsCard from './SessionStatsCard';
import ToolCallChip from './ToolCallChip';

const REMARK_REHYPE_OPTIONS = { handlers: { yaml: renderYamlFrontmatter } };

export interface HistoryViewProps {
  entries: HistoryEntry[];
  /** Ref to the scrolling container, so the owner can keep the newest entry in view. */
  historyRef: Ref<HTMLDivElement>;
  /** True if thinking blocks should be expanded by default. */
  allThinkingExpanded: boolean;
  onToggleToolCallExpanded(callId: string): void;
  /** Restarts the `klorb server` child process -- wired to a `'serverError'` entry's "Restart
   * Server" action (see `docs/specs/vscode-plugin.md`'s interrupt-polish section). */
  onRestartServer(): void;
}

/** Props for one <Entry/>. */
interface EntryProps {
  /** The actual history entry information to render. */
  entry: HistoryEntry;
  /** sorted index in the history feed, lowest-numbers first. */
  index: number;
  /** True if thinking block(s) in this entry, if any, should be auto-expanded. */
  allThinkingExpanded: boolean;
  onToggleToolCallExpanded: (callId: string) => void;
  onRestartServer: () => void;
}

/** Props for one <SystemInterjection/> within an Entry. */
interface SystemInterjectionProps {
  interjection: ParsedSystemInterjection;
}

function SystemInterjection(props: SystemInterjectionProps): JSX.Element {
  const { interjection } = props;
  if (interjection.subject === 'UserSkillActivation') {
    const identity = parseSkillActivationIdentity(interjection.body);
    const label =
      identity === undefined
        ? 'Activated skill'
        : `Activated skill: ${identity.namespace}/${identity.name}`;
    return <div className="entry entry-notice">{label}</div>;
  }
  const title = `System interjection (${interjection.subject})`;
  return (
    <details className="entry entry-system-interjection">
      <summary>{title}</summary>
      <div className="interjection-text">{interjection.body}</div>
    </details>
  );
}

/** Renders one history entry, dispatching on `entry.kind`; memoized so appending a streaming
 * chunk to the trailing entry doesn't re-render every other entry. */
const Entry = memo(function Entry({
  entry,
  index,
  allThinkingExpanded,
  onToggleToolCallExpanded,
  onRestartServer,
}: EntryProps): JSX.Element {
  switch (entry.kind) {
    case 'prompt': {
      const parsed = parseSystemInterjections(entry.text);
      const interjectionElements = parsed.interjections.map((interjection, i) => (
        <SystemInterjection key={`${index}-si-${i}`} interjection={interjection} />
      ));
      return (
        <Fragment key={index}>
          {interjectionElements}
          <div className="bubble bubble-prompt">
            {entry.images !== undefined && entry.images.length > 0 ? (
              <div className="prompt-attachment-tray">
                {entry.images.map((image, imageIndex) => (
                  <AttachmentThumbnail key={imageIndex} image={image} />
                ))}
              </div>
            ) : null}
            <MentionHighlightedText text={parsed.remainingText} />
          </div>
        </Fragment>
      );
    }
    case 'queuedMessage': {
      const parsed = parseSystemInterjections(entry.text);
      const interjectionElements = parsed.interjections.map((interjection, i) => (
        <SystemInterjection key={`${index}-si-${i}`} interjection={interjection} />
      ));
      return (
        <Fragment key={index}>
          {interjectionElements}
          <div className="bubble bubble-prompt bubble-queued">
            <div className="queued-prompt-header">Queued message</div>
            <MentionHighlightedText text={parsed.remainingText} />
          </div>
        </Fragment>
      );
    }
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
        <details
          className="entry entry-thinking"
          key={index}
          open={allThinkingExpanded ? true : undefined}>
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
});

/** The append-only history scroll: prompts as right-aligned bubbles, responses as rendered
 * markdown, thinking as a collapsed-by-default disclosure that streams while open, and tool
 * calls as `ToolCallChip`s. `historyRef` points at the scrolling entries container so the
 * owner's scroll-into-view logic is unaffected. */
export default function HistoryView({
  entries,
  historyRef,
  allThinkingExpanded,
  onToggleToolCallExpanded,
  onRestartServer,
}: HistoryViewProps): JSX.Element {
  return (
    <div id="history" ref={historyRef}>
      {entries.map((entry, index) => (
        <Entry
          key={index}
          entry={entry}
          index={index}
          allThinkingExpanded={allThinkingExpanded}
          onToggleToolCallExpanded={onToggleToolCallExpanded}
          onRestartServer={onRestartServer}
        />
      ))}
    </div>
  );
}
