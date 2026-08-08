// © Copyright 2026 Aaron Kimball
import { type JSX, type KeyboardEvent, useEffect, useRef, useState } from 'react';

import IconButton from 'webview/components/IconButton';

/** Placeholder text shown in place of an untitled root session's title -- rendered in italic,
 * dimmed styling (see `.title-placeholder` in `media/main.css`). Exported so `App.tsx` can reuse
 * it when deciding `titleIsPlaceholder`. */
export const SESSION_TITLE_PLACEHOLDER = 'Klorb agent session';

export interface PanelHeaderProps {
  title: string;
  titleIsPlaceholder: boolean;
  editable: boolean;
  rawTitle: string | null;
  onNewSession(): void;
  onBrowseSessions(): void;
  onRenameSession(title: string | null): void;
}

/**
 * The panel's top row: the active session's title (see `App.tsx`'s `sessionTitleDisplay()`) on
 * the left, and two icon buttons on the right -- a "New session" speech-bubble-plus icon (clears
 * the history array and closes the previous session server-side, see `App.tsx`'s
 * `newSession()`) and a "Session history" stopwatch icon (fetches this workspace's saved
 * sessions and shows them in a native VS Code QuickPick, see `klorb.browseSessions` in
 * extension.ts) -- see docs/specs/session-persistence.md. Both buttons carry a `title` attribute
 * so VS Code's webview host renders its native tooltip on hover.
 *
 * When `editable`, double-clicking the title swaps it for a plain `<input>` seeded from
 * `rawTitle`; Enter or losing focus commits the rename (posted as a `renameSession` message by
 * the caller's `onRenameSession`), Escape cancels without committing. Committing an
 * empty/whitespace-only value sends `null`, which the caller reflects back as
 * `SESSION_TITLE_PLACEHOLDER`.
 */
export default function PanelHeader({
  title,
  titleIsPlaceholder,
  editable,
  rawTitle,
  onNewSession,
  onBrowseSessions,
  onRenameSession,
}: PanelHeaderProps): JSX.Element {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  // Set just before a keyboard-driven close (Enter/Escape) so the blur event that close
  // triggers (removing the focused <input> from the DOM) doesn't also fire `handleBlur`'s own
  // commit -- otherwise Enter would commit the rename twice.
  const suppressBlurRef = useRef(false);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  function startEditing(): void {
    if (!editable) {
      return;
    }
    setDraft(rawTitle ?? '');
    setEditing(true);
  }

  function commitAndClose(): void {
    setEditing(false);
    const trimmed = draft.trim();
    if (trimmed !== (rawTitle ?? '')) {
      onRenameSession(trimmed === '' ? null : trimmed);
    }
  }

  function handleBlur(): void {
    if (suppressBlurRef.current) {
      suppressBlurRef.current = false;
      return;
    }
    commitAndClose();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (event.key === 'Enter') {
      suppressBlurRef.current = true;
      commitAndClose();
    } else if (event.key === 'Escape') {
      suppressBlurRef.current = true;
      setEditing(false);
    }
  }

  return (
    <div className="panel-header">
      {editing ? (
        <input
          ref={inputRef}
          className="title title-input"
          aria-label="Session title"
          placeholder={SESSION_TITLE_PLACEHOLDER}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={handleBlur}
          onKeyDown={handleKeyDown}
        />
      ) : (
        <div
          className={titleIsPlaceholder ? 'title title-placeholder' : 'title'}
          onDoubleClick={startEditing}>
          {title}
        </div>
      )}
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
