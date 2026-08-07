// © Copyright 2026 Aaron Kimball
import { type PreparedText, prepare, layout } from '@chenglou/pretext';
import type { VscodeTextarea } from '@vscode-elements/elements';
import {
  type ClipboardEvent,
  type DragEvent,
  type JSX,
  type SyntheticEvent,
  type KeyboardEvent,
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';

import { readImageDimensions } from 'shared/imageDimensions';
import { IMAGE_FILE_MIME_TYPES } from 'shared/imageFileTypes';
import type { ImageAttachment, SkillEntry } from 'shared/webviewMessages';
import AttachmentThumbnail from 'webview/components/AttachmentThumbnail';
import { StopMediaIcon } from 'webview/components/klorbIcons';
import { FileFinderPanel, useFileFinder } from 'webview/features/fileFinder';
import { SkillFinderPanel, useSkillFinder } from 'webview/features/skillFinder';
import { classifyEnterKey } from 'webview/keyHandling';

const MIN_ROWS = 1;
const MAX_ROWS = 10;

/** MIME types offered for drag-drop/paste/file-picker attachment -- the union of every
 * packaged vision model's `vision_details.supported_mime_types` (see docs/specs/vision-image-
 * input.md), not any one vendor's own list; the server re-validates and transcodes for the
 * actual active model regardless. */
export const ACCEPTED_IMAGE_MIME_TYPES = IMAGE_FILE_MIME_TYPES;

/** Client-side raw (pre-encode) byte ceiling for a single dropped/pasted/picked image,
 * independent of the server-side `tools.images.maxBytesRaw` ceiling -- rejects an oversized
 * attachment before it's ever base64-encoded and posted through `vscode.postMessage`. */
export const MAX_ATTACHMENT_RAW_BYTES = 25 * 1024 * 1024;

/** Imperative handle exposed via `ref`, so `App` can reclaim focus after a turn ends or an
 * interaction panel resolves (see `docs/specs/vscode-plugin.md`'s input-discipline sweep), and
 * so the status row's "Attach Image…" file-picker flow (`App`'s `imageAttached` handler) can
 * add its result to the pending attachment tray the same way a drag-drop/paste does. */
export interface PromptInputHandle {
  focus(): void;
  addAttachment(image: ImageAttachment): void;
}

/** Reads `blob` into a `data:` URL and splits it into `{mimeType, dataBase64}` -- the shape
 * every attachment source (drop, paste, file-picker) converges on before it's added to the
 * pending tray. */
function readAttachment(blob: Blob): Promise<{ mimeType: string; dataBase64: string }> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error('failed to read attachment'));
    reader.onload = () => {
      const result = typeof reader.result === 'string' ? reader.result : '';
      const commaIndex = result.indexOf(',');
      if (commaIndex === -1) {
        reject(new Error('failed to read attachment'));
        return;
      }
      const mimeMatch = /^data:([^;]+);base64$/.exec(result.slice(0, commaIndex));
      resolve({
        mimeType: mimeMatch?.[1] ?? blob.type,
        dataBase64: result.slice(commaIndex + 1),
      });
    };
    reader.readAsDataURL(blob);
  });
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
  /** The workspace's file list (see `App`'s `workspaceFiles` state), backing the `@`-mention
   * file finder (`webview/features/fileFinder`). Empty until the host's `workspaceFiles`
   * message arrives, or when no workspace folder is open. */
  workspaceFiles?: string[];
  /** The discoverable skill catalog (see `App`'s `skills` state), backing the `/`-mention
   * skill finder (`webview/features/skillFinder`). Empty when no skills are discoverable. */
  skills?: SkillEntry[];
  /** Past prompt texts for up/down-arrow recall, oldest-first. Pushed by the host on view
   * resolve and after each submitted prompt. Empty when no prompts have been submitted yet. */
  promptHistory?: string[];
  /** Whether the currently active model supports image input (`StatusSnapshot.
   * activeModelVision`) -- gates the drag/paste/file-picker attach affordance entirely; `false`
   * or not-yet-known both hide it, since attaching against a non-vision model can only fail
   * server-side. */
  imagesCapable?: boolean;
  /** Identifies which session `draft`/`attachments` belong to -- `'root'` for the root session,
   * a subagent's own session id otherwise. Switching this value swaps the visible draft text for
   * the one last typed for that session (or empty, the first time), rather than carrying stale
   * text across a selection change -- see docs/specs/vscode-plugin.md's "Subagents panel"
   * section. Attachments are intentionally not carried across a switch. Defaults to `'root'` for
   * a caller that doesn't multiplex sessions. */
  sessionKey?: string;
  onSubmit(text: string, images?: ImageAttachment[]): void;
  onCancel(): void;
  onCyclePermissionMode(): void;
}

/** Reads the current text out of the event's target element. The target is the
 * `<vscode-textarea>` custom element, whose `value` property mirrors its inner textarea. */
function targetValue(event: SyntheticEvent | KeyboardEvent<HTMLElement>): string {
  const value = (event.target as { value?: unknown }).value;
  return typeof value === 'string' ? value : '';
}

/** Reads the caret offset out of the event's target element -- `wrappedElement.selectionStart`
 * for a real `<vscode-textarea>`, falling back to the element's own `selectionStart` (what a
 * plain mocked element exposes in tests) and finally to end-of-text when neither is available. */
function cursorPosition(event: SyntheticEvent | KeyboardEvent<HTMLElement>): number {
  const target = event.target as {
    wrappedElement?: { selectionStart?: number | null };
    selectionStart?: number | null;
    value?: unknown;
  };
  const selectionStart = target.wrappedElement?.selectionStart ?? target.selectionStart;
  if (typeof selectionStart === 'number') {
    return selectionStart;
  }
  const value = typeof target.value === 'string' ? target.value : '';
  return value.length;
}

/** Navigation keys that move the caret without an accompanying `input` event -- a keyup on one
 * of these re-syncs the file finder against the caret's new position. */
const CARET_MOVE_KEYS = new Set(['ArrowLeft', 'ArrowRight', 'Home', 'End']);

/**
 * The multi-line prompt input row: Enter submits, Shift/Ctrl+Enter inserts a newline
 * (`classifyEnterKey`). While a turn is in flight, the textarea is disabled and a Stop button
 * (or Escape with focus anywhere in the row) cancels the turn -- unless `enqueueMessageCapable`,
 * in which case the textarea stays enabled (with both Send and Stop available) so a mid-turn
 * submit queues into the running turn instead. Exposes an imperative `focus()` via `ref` (see
 * `PromptInputHandle`) so `App` can reclaim focus after a turn ends or an interaction resolves.
 * Also drives the `@`-mention file finder (`useFileFinder`/`FileFinderPanel`): while it's open,
 * ArrowUp/ArrowDown/Enter/Tab/Escape are claimed for finder navigation instead of their usual
 * textarea behavior.
 */
const PromptInput = forwardRef<PromptInputHandle, PromptInputProps>(function PromptInput(
  {
    inFlight,
    muted = false,
    enqueueMessageCapable = false,
    workspaceFiles = [],
    skills = [],
    promptHistory = [],
    imagesCapable = false,
    sessionKey = 'root',
    onSubmit,
    onCancel,
    onCyclePermissionMode,
  },
  ref
): JSX.Element {
  const [draft, setDraft] = useState('');
  const [rows, setRows] = useState(MIN_ROWS);
  const [attachments, setAttachments] = useState<ImageAttachment[]>([]);
  const [attachError, setAttachError] = useState<string | undefined>(undefined);
  // `null` = not browsing history; a number = index into `promptHistory` (0 = oldest).
  const draftBeforeHistoryRef = useRef('');
  // Tracks browsing state and current index synchronously (refs update immediately, state is
  // batched) so that `recallHistory` reads the latest index even when called multiple times
  // in the same event loop tick without an intervening React render.
  const historyBrowsingRef = useRef(false);
  const historyIndexRef = useRef<number | null>(null);
  const textareaRef = useRef<VscodeTextarea>(null);
  const preparedRef = useRef<PreparedText | null>(null);
  const trailingNewlinesRef = useRef(0);
  const metricsRef = useRef<{ font: string; lineHeight: number } | null>(null);
  const disabled = inFlight && !enqueueMessageCapable;
  const finder = useFileFinder(workspaceFiles);
  const skillFinder = useSkillFinder(skills);
  // Per-session draft persistence: swaps `draft` when `sessionKey` changes, stashing the
  // outgoing session's current text first -- so switching the subagents-panel selection away and
  // back restores whatever was being typed, rather than losing it or leaking it into the wrong
  // session's box. Deliberately keyed only on `sessionKey`, not `draft` itself (which would fire
  // this effect on every keystroke); the outgoing draft is read from `draft` via closure at the
  // moment the key actually changes.
  const draftsBySessionRef = useRef<Record<string, string>>({});
  const prevSessionKeyRef = useRef(sessionKey);
  useEffect(() => {
    if (prevSessionKeyRef.current === sessionKey) {
      return;
    }
    draftsBySessionRef.current[prevSessionKeyRef.current] = draft;
    prevSessionKeyRef.current = sessionKey;
    setTextareaValue(draftsBySessionRef.current[sessionKey] ?? '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionKey]);

  useImperativeHandle(ref, () => ({
    focus: () => textareaRef.current?.focus(),
    addAttachment: (image: ImageAttachment) => setAttachments((prev) => [...prev, image]),
  }));

  /** Reads `blob` and adds it to the pending attachment tray, rejecting it (with an inline
   * error instead of a silent drop) if it's over `MAX_ATTACHMENT_RAW_BYTES` or not a
   * recognized image type. Shared by the drop/paste handlers below. */
  async function addAttachmentFromBlob(blob: Blob, name?: string): Promise<void> {
    if (!ACCEPTED_IMAGE_MIME_TYPES.includes(blob.type)) {
      return;
    }
    if (blob.size > MAX_ATTACHMENT_RAW_BYTES) {
      setAttachError(
        `${name ?? 'Pasted image'} is too large (max ${Math.floor(MAX_ATTACHMENT_RAW_BYTES / (1024 * 1024))}MB).`
      );
      return;
    }
    try {
      const [{ mimeType, dataBase64 }, buffer] = await Promise.all([
        readAttachment(blob),
        blob.arrayBuffer(),
      ]);
      const dimensions = readImageDimensions(new Uint8Array(buffer));
      setAttachError(undefined);
      setAttachments((prev) => [...prev, { mimeType, dataBase64, name, ...dimensions }]);
    } catch {
      setAttachError(`Failed to read ${name ?? 'pasted image'}.`);
    }
  }

  function removeAttachment(index: number): void {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  }

  function handleDragOver(event: DragEvent<HTMLElement>): void {
    if (!imagesCapable || !event.dataTransfer.types.includes('Files')) {
      return;
    }
    event.preventDefault();
  }

  function handleDrop(event: DragEvent<HTMLElement>): void {
    if (!imagesCapable) {
      return;
    }
    const files = Array.from(event.dataTransfer.files).filter((file) =>
      ACCEPTED_IMAGE_MIME_TYPES.includes(file.type)
    );
    if (files.length === 0) {
      return;
    }
    event.preventDefault();
    for (const file of files) {
      void addAttachmentFromBlob(file, file.name);
    }
  }

  function handlePaste(event: ClipboardEvent<HTMLElement>): void {
    if (!imagesCapable) {
      return;
    }
    const items = Array.from(event.clipboardData.items).filter((item) => item.kind === 'file');
    const imageFiles = items
      .map((item) => item.getAsFile())
      .filter(
        (file): file is File => file !== null && ACCEPTED_IMAGE_MIME_TYPES.includes(file.type)
      );
    if (imageFiles.length === 0) {
      return;
    }
    event.preventDefault();
    for (const file of imageFiles) {
      void addAttachmentFromBlob(file);
    }
  }

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

    historyBrowsingRef.current = false;
    historyIndexRef.current = null;
    preparedRef.current = null;
    const images = attachments.length > 0 ? attachments : undefined;
    setAttachments([]);
    setAttachError(undefined);
    onSubmit(text, images);
  }

  /** Splices `finder`'s match at `index` (default: its active row) into the textarea, both the
   * underlying DOM element (so the caret lands in the right place immediately) and `draft`
   * state (so React's own render stays in sync) -- mirrors `submit()`'s "write the DOM element
   * directly, then mirror into state" approach. */
  function applyFinderSelection(index?: number): void {
    const selection = finder.select(draft, index);
    if (selection === undefined) {
      return;
    }
    const el = textareaRef.current;
    if (el !== null) {
      el.value = selection.text;
      const wrapped = el.wrappedElement;
      if (wrapped) {
        wrapped.selectionStart = selection.cursor;
        wrapped.selectionEnd = selection.cursor;
      }
    }
    setDraft(selection.text);
    computeRows(selection.text);
    el?.focus();
  }

  function applySkillFinderSelection(index?: number): void {
    const selection = skillFinder.select(draft, index);
    if (selection === undefined) {
      return;
    }
    const el = textareaRef.current;
    if (el !== null) {
      el.value = selection.text;
      const wrapped = el.wrappedElement;
      if (wrapped) {
        wrapped.selectionStart = selection.cursor;
        wrapped.selectionEnd = selection.cursor;
      }
    }
    setDraft(selection.text);
    computeRows(selection.text);
    el?.focus();
  }

  /** Writes `text` into the textarea (both DOM and React state) and recomputes rows. Shared by
   * history recall and (indirectly) `applyFinderSelection`. */
  function setTextareaValue(text: string): void {
    // Temporarily clear the browsing flag so `detachFromHistory()` (called from the `onInput`
    // handler that may fire from the DOM value change below) is a no-op -- the flag is restored
    // by the caller (`recallHistory`) immediately after this returns.
    historyBrowsingRef.current = false;
    const el = textareaRef.current;
    if (el !== null) {
      el.value = text;
      const wrapped = el.wrappedElement;
      if (wrapped) {
        wrapped.selectionStart = text.length;
        wrapped.selectionEnd = text.length;
      }
    }
    setDraft(text);
    computeRows(text);
  }

  /** Steps through prompt history: `direction` is -1 for older, +1 for newer. Entering from
   * a fresh draft stashes it in `draftBeforeHistoryRef`; stepping past the most recent entry
   * restores the stash and exits history browse mode. */
  function recallHistory(direction: number): void {
    if (promptHistory.length === 0) {
      return;
    }
    let nextIndex: number;
    if (historyIndexRef.current === null) {
      // Entering history browse from a fresh draft.
      if (direction < 0) {
        draftBeforeHistoryRef.current = textareaRef.current?.value ?? draft;
        nextIndex = promptHistory.length - 1;
      } else {
        return;
      }
    } else {
      nextIndex = historyIndexRef.current + direction;
      if (nextIndex >= promptHistory.length) {
        // Past the most recent: restore draft and exit browse.
        historyBrowsingRef.current = false;
        historyIndexRef.current = null;

        setTextareaValue(draftBeforeHistoryRef.current);
        return;
      }
    }
    if (nextIndex < 0) {
      return;
    }
    historyIndexRef.current = nextIndex;
    setTextareaValue(promptHistory[nextIndex]!);
    historyBrowsingRef.current = true;
  }

  /** Detaches from history browse mode (if active) and stashes nothing -- called when the user
   * types while browsing, abandoning the recall. */
  function detachFromHistory(): void {
    if (historyBrowsingRef.current) {
      historyBrowsingRef.current = false;
      historyIndexRef.current = null;
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLElement>): void {
    if (finder.isOpen) {
      if (event.key === 'Escape') {
        event.preventDefault();
        finder.dismiss();
        return;
      }
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        finder.moveActive(1);
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        finder.moveActive(-1);
        return;
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault();
        applyFinderSelection();
        return;
      }
    }
    if (skillFinder.isOpen) {
      if (event.key === 'Escape') {
        event.preventDefault();
        skillFinder.dismiss();
        return;
      }
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        skillFinder.moveActive(1);
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        skillFinder.moveActive(-1);
        return;
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault();
        applySkillFinderSelection();
        return;
      }
    }
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
    if (event.key === 'ArrowUp' && (targetValue(event) === '' || historyBrowsingRef.current)) {
      event.preventDefault();
      recallHistory(-1);
      return;
    }
    if (event.key === 'ArrowDown' && historyBrowsingRef.current) {
      event.preventDefault();
      recallHistory(1);
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
    <div className="prompt-input-wrapper" onDragOver={handleDragOver} onDrop={handleDrop}>
      {attachError !== undefined ? <div className="attachment-error">{attachError}</div> : null}
      {attachments.length > 0 ? (
        <div className="attachment-tray">
          {attachments.map((attachment, index) => (
            <AttachmentThumbnail
              key={`${attachment.name ?? 'pasted'}-${index}`}
              image={attachment}
              onRemove={() => removeAttachment(index)}
            />
          ))}
        </div>
      ) : null}
      <div className={`input-row${muted ? ' input-row-muted' : ''}`} onKeyDown={handleKeyDown}>
        {finder.isOpen ? (
          <FileFinderPanel
            matches={finder.matches}
            activeIndex={finder.activeIndex}
            onHover={finder.setActiveIndex}
            onSelect={applyFinderSelection}
          />
        ) : null}
        {skillFinder.isOpen ? (
          <SkillFinderPanel
            matches={skillFinder.matches}
            activeIndex={skillFinder.activeIndex}
            onHover={skillFinder.setActiveIndex}
            onSelect={applySkillFinderSelection}
          />
        ) : null}
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
            finder.sync(value, cursorPosition(event));
            skillFinder.sync(value, cursorPosition(event));
            detachFromHistory();
          }}
          onKeyUp={(event: KeyboardEvent<HTMLElement>) => {
            if (CARET_MOVE_KEYS.has(event.key)) {
              finder.sync(draft, cursorPosition(event));
              skillFinder.sync(draft, cursorPosition(event));
            }
          }}
          onClick={(event: SyntheticEvent) => {
            finder.sync(draft, cursorPosition(event));
            skillFinder.sync(draft, cursorPosition(event));
          }}
          onPaste={handlePaste}
        />
        {!inFlight || enqueueMessageCapable ? (
          <vscode-button
            id="submit-button"
            iconOnly
            title="Send"
            aria-label="Send"
            disabled={draft.trim().length === 0}
            onClick={() => submit()}>
            <vscode-icon name="send" />
          </vscode-button>
        ) : null}
        {inFlight ? (
          <vscode-button
            id="stop-button"
            iconOnly
            title="Stop"
            aria-label="Stop"
            onClick={() => onCancel()}>
            <StopMediaIcon />
          </vscode-button>
        ) : null}
      </div>
    </div>
  );
});

export default PromptInput;
