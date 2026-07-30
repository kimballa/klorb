// © Copyright 2026 Aaron Kimball
import type { JSX } from 'react';

import IconButton from 'webview/components/IconButton';

export interface PanelHeaderProps {
  title: string;
  onNewSession(): void;
  onBrowseSessions(): void;
}

/**
 * The panel's top row: the active session's title (see `App.tsx`'s `sessionTitleText()`) on the
 * left, and two icon buttons on the right -- a "New session" speech-bubble-plus icon (clears the
 * history array and closes the previous session server-side, see `App.tsx`'s `newSession()`) and
 * a "Session history" stopwatch icon (fetches this workspace's saved sessions and shows them in
 * a native VS Code QuickPick, see `klorb.browseSessions` in extension.ts) -- see
 * docs/specs/session-persistence.md. Both buttons carry a `title` attribute so VS Code's webview
 * host renders its native tooltip on hover.
 */
export default function PanelHeader({
  title,
  onNewSession,
  onBrowseSessions,
}: PanelHeaderProps): JSX.Element {
  return (
    <div className="panel-header">
      <div className="title">{title}</div>
      <div className="panel-header-actions">
        <IconButton title="Session history" onClick={onBrowseSessions}>
          <vscode-icon name="history" />
        </IconButton>
        <IconButton title="New session" onClick={onNewSession}>
          <vscode-icon name="chat-sparkle" />
        </IconButton>
      </div>
    </div>
  );
}
