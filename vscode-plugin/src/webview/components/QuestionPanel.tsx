// © Copyright 2026 Aaron Kimball
import { type JSX, type KeyboardEvent, useEffect, useRef, useState } from 'react';

import type { QuestionAskMessage } from 'shared/webviewMessages';

/** The user's answer, handed up to the panel's owner (`App`) to post as a `questionAnswer`
 * webview message and record a compact history entry. */
export type QuestionPanelAnswer =
  { selectedOptionIndex: number } | { otherText: string } | { cancelled: true };

interface QuestionPanelProps {
  ask: QuestionAskMessage;
  onAnswer(answer: QuestionPanelAnswer): void;
}

/**
 * The question panel mounted in the interaction area, directly above the prompt input, while a
 * `questionAsk` is outstanding -- the tall-narrow equivalent of the TUI's
 * `AskUserQuestionsPanel`. Renders the question's header chip, a "Question N of M" caption, an
 * option list (label bold, description dim), and an "Other…" free-text row. Escape cancels the
 * whole batch, mirroring the TUI's Escape semantics -- unlike a permission ask, cancelling here
 * stops every remaining question in the batch server-side, not just this one, which the hint
 * line below the options calls out. The panel grabs focus on mount (and again each time a new
 * question in the batch replaces it) so that Escape reaches `handleKeyDown` even before the user
 * has clicked into an option or the "Other…" field -- a plain `onKeyDown` only fires for keys
 * pressed while focus is somewhere inside this subtree.
 */
export default function QuestionPanel({ ask, onAnswer }: QuestionPanelProps): JSX.Element {
  const [otherText, setOtherText] = useState('');
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    panelRef.current?.focus();
  }, [ask.requestId]);

  function handleKeyDown(event: KeyboardEvent<HTMLElement>): void {
    if (event.key === 'Escape') {
      onAnswer({ cancelled: true });
    }
  }

  function submitOther(): void {
    const text = otherText.trim();
    if (text.length === 0) {
      return;
    }
    onAnswer({ otherText: text });
  }

  function handleOtherKeyDown(event: KeyboardEvent<HTMLElement>): void {
    if (event.key === 'Enter') {
      submitOther();
    }
  }

  return (
    <div className="question-panel" tabIndex={-1} ref={panelRef} onKeyDown={handleKeyDown}>
      <div className="question-header">
        {ask.header}
        <span className="question-item-count">{`Question ${ask.index + 1} of ${ask.total}`}</span>
      </div>
      <div className="question-text">{ask.question}</div>
      {ask.options.length > 0 ? (
        <div className="question-options">
          {ask.options.map((option, index) => (
            <vscode-button
              key={`${index}-${option.label}`}
              secondary
              className="question-option"
              onClick={() => onAnswer({ selectedOptionIndex: index })}>
              <span className="question-option-label">{option.label}</span>
              {option.description !== undefined ? (
                <span className="question-option-description">{option.description}</span>
              ) : null}
            </vscode-button>
          ))}
        </div>
      ) : null}
      <details className="question-other" open={ask.options.length === 0}>
        <summary>Other…</summary>
        <div className="question-other-row">
          <vscode-textfield
            placeholder="Type your answer…"
            value={otherText}
            onInput={(event) => {
              const value = (event.target as { value?: unknown }).value;
              setOtherText(typeof value === 'string' ? value : '');
            }}
            onKeyDown={handleOtherKeyDown}
          />
          <vscode-button secondary onClick={submitOther}>
            Send
          </vscode-button>
        </div>
      </details>
      <div className="question-hint">Esc dismisses remaining questions</div>
    </div>
  );
}
