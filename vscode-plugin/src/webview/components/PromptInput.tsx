// © Copyright 2026 Aaron Kimball
import { type PreparedText, prepare, layout } from '@chenglou/pretext';
import type { VscodeTextarea } from '@vscode-elements/elements';
import {
  type JSX,
  type SyntheticEvent,
  type KeyboardEvent,
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';

import { classifyEnterKey } from 'webview/keyHandling';

const MIN_ROWS = 1;
const MAX_ROWS = 10;

/** Imperative handle exposed via `ref`, so `App` can reclaim focus after a turn ends or an
 * interaction panel resolves (see `docs/specs/vscode-plugin.md`'s input-discipline sweep). */
export interface PromptInputHandle {
  focus(): void;
}

/** Right-facing filled triangle for the Send button; codicons has no equivalent play glyph. */
function PlayIcon(): JSX.Element {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M4 2L14 8L4 14V2Z" />
    </svg>
  );
}

/** Filled square for the Stop button; codicons' "stop" glyph is actually an error/circle-X
 * icon, not a square, so this is drawn directly instead of using `vscode-button`'s `icon` prop. */
function StopIcon(): JSX.Element {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <rect x="3" y="3" width="10" height="10" />
    </svg>
  );
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
  const [rows, setRows] = useState(MIN_ROWS);
  const textareaRef = useRef<VscodeTextarea>(null);
  const preparedRef = useRef<PreparedText | null>(null);
  const trailingNewlinesRef = useRef(0);
  const metricsRef = useRef<{ font: string; lineHeight: number } | null>(null);
  const disabled = inFlight && !enqueueMessageCapable;

  useImperativeHandle(ref, () => ({
    focus: () => textareaRef.current?.focus(),
  }));

  /** Read and cache font metrics from the inner textarea element. */
  function readMetrics(): { font: string; lineHeight: number } | null {
    if (metricsRef.current) return metricsRef.current;
    const wrapped = textareaRef.current?.wrappedElement;
    if (!wrapped) return null;
    const cs = getComputedStyle(wrapped);
    const fontSize = parseFloat(cs.fontSize);
    const lineHeightRaw = cs.lineHeight;
    const lineHeight =
      lineHeightRaw === 'normal' || isNaN(parseFloat(lineHeightRaw))
        ? Math.round(fontSize * 1.2)
        : parseFloat(lineHeightRaw);
    metricsRef.current = {
      font: `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`,
      lineHeight,
    };
    return metricsRef.current;
  }

  /** Recompute the visible row count for the given text using pretext. */
  function computeRows(text: string): void {
    const wrapped = textareaRef.current?.wrappedElement;
    const metrics = readMetrics();
    if (!wrapped || !metrics) return;
    const cs = getComputedStyle(wrapped);
    const maxWidth =
      wrapped.clientWidth - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0);
    if (maxWidth <= 0) return;
    const prepared = prepare(text, metrics.font, { whiteSpace: 'pre-wrap' });
    preparedRef.current = prepared;
    // pretext's layout counts content lines; a trailing newline creates a visual
    // empty line the textarea displays but pretext doesn't measure, so add it.
    const trailingNewlines = text.endsWith('\n') ? 1 : 0;
    trailingNewlinesRef.current = trailingNewlines;
    const { lineCount } = layout(prepared, maxWidth, metrics.lineHeight);
    setRows(Math.max(MIN_ROWS, Math.min(lineCount + trailingNewlines, MAX_ROWS)));
  }

  // Recompute rows when the textarea width changes (e.g. sidebar resize).
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      const metrics = readMetrics();
      const wrapped = textareaRef.current?.wrappedElement;
      if (!metrics || !preparedRef.current || !wrapped) return;
      const cs = getComputedStyle(wrapped);
      const maxWidth =
        wrapped.clientWidth -
        (parseFloat(cs.paddingLeft) || 0) -
        (parseFloat(cs.paddingRight) || 0);
      if (maxWidth <= 0) return;
      const { lineCount } = layout(preparedRef.current, maxWidth, metrics.lineHeight);
      setRows(Math.max(MIN_ROWS, Math.min(lineCount + trailingNewlinesRef.current, MAX_ROWS)));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

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
    setRows(MIN_ROWS);
    preparedRef.current = null;
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
        rows={rows}
        resize="none"
        placeholder="Message Klorb... (Enter to send, Shift+Enter for a newline)"
        value={draft}
        disabled={disabled}
        onInput={(event: SyntheticEvent) => {
          const value = targetValue(event);
          setDraft(value);
          computeRows(value);
        }}
      />
      {!inFlight || enqueueMessageCapable ? (
        <vscode-button
          id="submit-button"
          iconOnly
          title="Send"
          aria-label="Send"
          disabled={draft.trim().length === 0}
          onClick={() => submit()}>
          <PlayIcon />
        </vscode-button>
      ) : null}
      {inFlight ? (
        <vscode-button
          id="stop-button"
          iconOnly
          title="Stop"
          aria-label="Stop"
          onClick={() => onCancel()}>
          <StopIcon />
        </vscode-button>
      ) : null}
    </div>
  );
});

export default PromptInput;
