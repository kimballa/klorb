// © Copyright 2026 Aaron Kimball
import { Fragment, type JSX } from 'react';

import type { OpenLocationMessage } from 'shared/webviewMessages';
import { findMentionSpans } from 'webview/features/fileFinder';
import useVsCodeApi from 'webview/hooks/useVsCodeApi';

export interface MentionHighlightedTextProps {
  text: string;
}

/** Renders `text` with every `@mention` (`findMentionSpans`) wrapped in a `.mention-chip` span,
 * so a submitted prompt visibly confirms exactly which file references the server resolved --
 * mirroring `klorb.session.mixins.mentions.resolve_at_mentions`'s own parsing precisely, down to
 * trailing-punctuation trimming, rather than a looser approximation. Used for `HistoryView`'s
 * `'prompt'`/`'queuedMessage'` entries. */
export default function MentionHighlightedText({ text }: MentionHighlightedTextProps): JSX.Element {
  const vscode = useVsCodeApi();
  const spans = findMentionSpans(text);
  if (spans.length === 0) {
    return <>{text}</>;
  }
  const pieces: JSX.Element[] = [];
  let cursor = 0;
  spans.forEach((span, index) => {
    if (span.start > cursor) {
      pieces.push(<Fragment key={`text-${index}`}>{text.slice(cursor, span.start)}</Fragment>);
    }
    pieces.push(
      <span
        className="mention-chip"
        key={`mention-${index}`}
        onClick={(): void => {
          const message: OpenLocationMessage = { type: 'openLocation', path: span.filename };
          vscode.postMessage(message);
        }}>
        {text.slice(span.start, span.end)}
      </span>
    );
    cursor = span.end;
  });
  if (cursor < text.length) {
    pieces.push(<Fragment key="text-end">{text.slice(cursor)}</Fragment>);
  }
  return <>{pieces}</>;
}
