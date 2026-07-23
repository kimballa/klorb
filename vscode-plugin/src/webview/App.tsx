// © Copyright 2026 Aaron Kimball
import { type JSX, type KeyboardEvent, useEffect, useRef, useState } from 'react';

import { classifyEnterKey } from './keyHandling';

export interface VsCodeApi {
  postMessage(message: unknown): void;
  setState(state: unknown): void;
  getState(): unknown;
}

/** One chat bubble: `'user'` for what was typed, `'server'` for the klorb server's reply. */
export interface ChatEntry {
  role: 'user' | 'server';
  text: string;
}

interface AppProps {
  vscode: VsCodeApi;
  initialEntries: ChatEntry[];
}

interface ReplyMessage {
  type: 'reply';
  text: string;
}

function isReplyMessage(data: unknown): data is ReplyMessage {
  return (
    typeof data === 'object' &&
    data !== null &&
    (data as { type?: unknown }).type === 'reply' &&
    typeof (data as { text?: unknown }).text === 'string'
  );
}

export function App({ vscode, initialEntries }: AppProps): JSX.Element {
  const [entries, setEntries] = useState<ChatEntry[]>(initialEntries);
  const [draft, setDraft] = useState('');
  const historyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    vscode.setState({ entries });
    historyRef.current?.lastElementChild?.scrollIntoView({ block: 'end' });
  }, [entries, vscode]);

  useEffect(() => {
    function onMessage(event: MessageEvent<unknown>): void {
      const data = event.data;
      if (isReplyMessage(data)) {
        setEntries((prev) => [...prev, { role: 'server', text: data.text }]);
      }
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  function submit(): void {
    const text = draft.trim();
    if (text.length === 0) {
      return;
    }
    setEntries((prev) => [...prev, { role: 'user', text }]);
    setDraft('');
    vscode.postMessage({ type: 'submit', text });
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
          <div className={`bubble bubble-${entry.role}`} key={index}>
            {entry.text}
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
