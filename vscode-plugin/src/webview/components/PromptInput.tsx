// © Copyright 2026 Aaron Kimball
import type { VscodeTextarea } from '@vscode-elements/elements';
import {
  type JSX,
  type SyntheticEvent,
  type KeyboardEvent,
  forwardRef,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';

import { classifyEnterKey } from 'webview/keyHandling';

/** Imperative handle exposed via `ref`, so `App` can reclaim focus after a turn ends or an
 * interaction panel resolves (see `docs/specs/vscode-plugin.md`'s input-discipline sweep). */
export interface PromptInputHandle {
  focus(): void;
}

interface PromptInputProps {
  /** True while a prompt turn is running: disables the input unless `enqueueMessageCapable`,
   * in which case it stays enabled so the user can queue a message into the running turn. */
  inFlight: boolean;
  /** True while an `ApprovalPanel` (or other interaction-area panel) is active: visually mutes
   * the already-disabled input row, mirroring the TUI's interaction-mode treatment. */
  muted?: boolean;
  /** Whether the connected server advertised `_klorb/enqueueMessage`: when true, the input
   * stays enabled during a turn and a mid-turn submit calls `onSubmit` the same as an
   * idle-turn one (the caller distinguishes by its own `inFlight` state) instead of the input
   * simply being disabled. */
  enqueueMessageCapable?: boolean;
  onSubmit(text: string): void;
  onCancel(): void;
  onCyclePermissionMode(): void;
}

/** Reads the current text out of the event's target element. The target is the
 * `<vscode-textarea>` custom element, whose `value` property mirrors its inner textarea. */
function targetValue(event: SyntheticEvent | KeyboardEvent<HTMLElement>): string {
  const value = (event.target as { value?: unknown }).value;
  return typeof value === 'string' ? value : '';
}

/**
 * The multi-line prompt input row: Enter submits, Shift/Ctrl+Enter inserts a newline
 * (`classifyEnterKey`). While a turn is in flight, the textarea is disabled and a Stop button
 * (or Escape with focus anywhere in the row) cancels the turn -- unless `enqueueMessageCapable`,
 * in which case the textarea stays enabled (with both Send and Stop available) so a mid-turn
 * submit queues into the running turn instead. Exposes an imperative `focus()` via `ref` (see
 * `PromptInputHandle`) so `App` can reclaim focus after a turn ends or an interaction resolves.
 */
const PromptInput = forwardRef<PromptInputHandle, PromptInputProps>(function PromptInput(
  {
    inFlight,
    muted = false,
    enqueueMessageCapable = false,
    onSubmit,
    onCancel,
    onCyclePermissionMode,
  },
  ref
): JSX.Element {
  const [draft, setDraft] = useState('');
  const textareaRef = useRef<VscodeTextarea>(null);
  const disabled = inFlight && !enqueueMessageCapable;

  useImperativeHandle(ref, () => ({
    focus: () => textareaRef.current?.focus(),
  }));

  function submit(): void {
    const text = draft.trim();
    if (text.length === 0 || disabled) {
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
    if (event.key === 'Tab' && event.shiftKey) {
      // Claims Shift+Tab for the permission-mode cycle (mirroring the TUI's own Shift+Tab)
      // instead of letting it fall through to the browser's default tab-order navigation.
      event.preventDefault();
      onCyclePermissionMode();
      return;
    }
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
        disabled={disabled}
        onInput={(event: SyntheticEvent) => setDraft(targetValue(event))}
      />
      {!inFlight || enqueueMessageCapable ? (
        <vscode-button id="submit-button" onClick={() => submit()}>
          Send
        </vscode-button>
      ) : null}
      {inFlight ? (
        <vscode-button id="stop-button" onClick={() => onCancel()}>
          Stop
        </vscode-button>
      ) : null}
    </div>
  );
});

export default PromptInput;
