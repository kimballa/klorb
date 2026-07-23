// © Copyright 2026 Aaron Kimball
import { type JSX, type KeyboardEvent, useEffect, useRef, useState } from 'react';

import { classifyEnterKey } from './keyHandling';

export interface VsCodeApi {
  setState(state: unknown): void;
  getState(): unknown;
}

interface AppProps {
  vscode: VsCodeApi;
  initialEntries: string[];
}

export function App({ vscode, initialEntries }: AppProps): JSX.Element {
  const [entries, setEntries] = useState<string[]>(initialEntries);
  const [draft, setDraft] = useState('');
  const historyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    vscode.setState({ entries });
    historyRef.current?.lastElementChild?.scrollIntoView({ block: 'end' });
  }, [entries, vscode]);

  function submit(): void {
    const text = draft.trim();
    if (text.length === 0) {
      return;
    }
    setEntries((prev) => [...prev, text]);
    setDraft('');
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key !== 'Enter') {
      return;
    }
    if (classifyEnterKey(event.shiftKey, event.ctrlKey) === 'submit') {
      event.preventDefault();
      submit();
    }
  }

  return (
    <>
      <div className="title">Klorb session</div>
      {/* Entries only ever append here, never reorder or remove, so an index key is stable. */}
      <div id="history" ref={historyRef}>
        {entries.map((entry, index) => (
          <div className="history-entry" key={index}>
            {entry}
          </div>
        ))}
      </div>
      <div className="input-row">
        <textarea
          id="prompt-input"
          rows={2}
          placeholder="Message Klorb... (Enter to send, Shift+Enter for a newline)"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button id="submit-button" onClick={submit}>
          Send
        </button>
      </div>
    </>
  );
}
