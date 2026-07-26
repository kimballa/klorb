// © Copyright 2026 Aaron Kimball
import type { VscodeTextarea } from '@vscode-elements/elements';
import { type JSX, type SyntheticEvent, type KeyboardEvent, useRef, useState } from 'react';

import { classifyEnterKey } from 'webview/keyHandling';

interface PromptInputProps {
  /** True while a prompt turn is running: the input is disabled and Stop replaces Send. */
  inFlight: boolean;
  /** True while an `ApprovalPanel` (or other interaction-area panel) is active: visually mutes
   * the already-disabled input row, mirroring the TUI's interaction-mode treatment. */
  muted?: boolean;
  onSubmit(text: string): void;
  onCancel(): void;
}

/** Reads the current text out of the event's target element. The target is the
 * `<vscode-textarea>` custom element, whose `value` property mirrors its inner textarea. */
function targetValue(event: SyntheticEvent | KeyboardEvent<HTMLElement>): string {
  const value = (event.target as { value?: unknown }).value;
  return typeof value === 'string' ? value : '';
}

/**
 * The multi-line prompt input row: Enter submits, Shift/Ctrl+Enter inserts a newline
 * (`classifyEnterKey`), and while a turn is in flight the textarea is disabled and a Stop
 * button (or Escape with focus anywhere in the row) cancels the turn.
 */
export default function PromptInput({
  inFlight,
  muted = false,
  onSubmit,
  onCancel,
}: PromptInputProps): JSX.Element {
  const [draft, setDraft] = useState('');
  const textareaRef = useRef<VscodeTextarea>(null);

  function submit(): void {
    const text = draft.trim();
    if (text.length === 0 || inFlight) {
      return;
    }
    // Clearing the underlying element directly, not just React's `value` prop, guarantees the
    // textarea is empty before it's disabled below, regardless of how the custom element
    // reconciles a prop update against its own internal state.
    if (textareaRef.current !== null) {
      textareaRef.current.value = '';
    }
    setDraft('');
    onSubmit(text);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLElement>): void {
    if (event.key === 'Escape') {
      if (inFlight) {
        onCancel();
      }
      return;
    }
    if (event.key !== 'Enter') {
      return;
    }
    if (classifyEnterKey(event.shiftKey, event.ctrlKey) === 'submit') {
      event.preventDefault();
      submit();
    }
  }

  return (
    <div className={`input-row${muted ? ' input-row-muted' : ''}`} onKeyDown={handleKeyDown}>
      <vscode-textarea
        ref={textareaRef}
        id="prompt-input"
        rows={2}
        placeholder="Message Klorb... (Enter to send, Shift+Enter for a newline)"
        value={draft}
        disabled={inFlight}
        onInput={(event: SyntheticEvent) => setDraft(targetValue(event))}
      />
      {inFlight ? (
        <vscode-button id="stop-button" onClick={() => onCancel()}>
          Stop
        </vscode-button>
      ) : (
        <vscode-button id="submit-button" onClick={() => submit()}>
          Send
        </vscode-button>
      )}
    </div>
  );
}
