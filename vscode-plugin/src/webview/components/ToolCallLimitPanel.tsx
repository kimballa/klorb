// © Copyright 2026 Aaron Kimball
import { type JSX, type KeyboardEvent, useEffect, useRef } from 'react';

import type { ToolCallLimitAskMessage } from 'shared/webviewMessages';

/** The user's decision, handed up to the panel's owner (`App`) to post as a
 * `toolCallLimitDecision` webview message. */
export type ToolCallLimitDecision = { approved: true } | { cancelled: true };

interface ToolCallLimitPanelProps {
  ask: ToolCallLimitAskMessage;
  onDecision(decision: ToolCallLimitDecision): void;
}

/**
 * The tool-call-limit panel mounted in the interaction area, directly above the prompt input,
 * while a `_klorb/raiseToolCallLimit` ext request is outstanding -- the in-webview equivalent
 * of the TUI's `ToolCallLimitScreen`, replacing the former native VS Code modal dialog.
 * Renders the limit-reached message and two action buttons: "Continue" (doubles the cap) and
 * "Deny" (stops the turn). Escape also denies, matching the permission-ask and question-ask
 * panels' own Escape semantics. The panel grabs focus on mount so that Escape reaches
 * `handleKeyDown` even before the user has clicked a button.
 */
export default function ToolCallLimitPanel({
  ask,
  onDecision,
}: ToolCallLimitPanelProps): JSX.Element {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    panelRef.current?.focus();
  }, [ask.requestId]);

  function handleKeyDown(event: KeyboardEvent<HTMLElement>): void {
    if (event.key === 'Escape') {
      onDecision({ cancelled: true });
    }
  }

  return (
    <div className="tool-call-limit-panel" tabIndex={-1} ref={panelRef} onKeyDown={handleKeyDown}>
      <div className="tool-call-limit-header">Tool call limit reached</div>
      <div className="tool-call-limit-message">{ask.message}</div>
      <div className="tool-call-limit-actions">
        <vscode-button onClick={() => onDecision({ approved: true })}>Continue</vscode-button>
        <vscode-button secondary onClick={() => onDecision({ cancelled: true })}>
          Deny
        </vscode-button>
      </div>
      <div className="tool-call-limit-hint">Esc denies</div>
    </div>
  );
}
