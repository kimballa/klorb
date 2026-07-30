// © Copyright 2026 Aaron Kimball
import type { JSX } from 'react';

/** Right-facing filled triangle for a "start/send" action; codicons has no equivalent play
 * glyph. */
export function PlayMediaIcon(): JSX.Element {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M4 2L14 8L4 14V2Z" />
    </svg>
  );
}

/** Filled square for a "stop/cancel" action; codicons' "stop" glyph is actually an error/circle-X
 * icon, not a square, so this is drawn directly instead of using `vscode-button`'s `icon` prop. */
export function StopMediaIcon(): JSX.Element {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <rect x="3" y="3" width="10" height="10" />
    </svg>
  );
}
