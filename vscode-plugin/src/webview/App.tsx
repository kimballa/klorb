// © Copyright 2026 Aaron Kimball
import { type JSX, useEffect, useRef, useState } from 'react';

import { parseHostMessage } from 'shared/webviewMessages';
import PromptInput from 'webview/components/PromptInput';
import VsCodeApiProvider, { type VsCodeApi } from 'webview/components/VsCodeApiProvider';
import {
  HistoryView,
  appendPrompt,
  applyHostMessage,
  applyTurnFlag,
  type HistoryEntry,
} from 'webview/features/history';

interface AppProps {
  vscode: VsCodeApi;
  initialEntries: HistoryEntry[];
}

/**
 * The panel's layout shell, top to bottom: the history scroll, a placeholder interaction
 * area (approval/question panels mount there in later increments), the prompt input row,
 * and a placeholder status row. All history/turn state lives here; the pure transition
 * logic is in `webview/features/history`'s `historyModel.ts`. Wraps its content in
 * `<VsCodeApiProvider>` so any descendant can reach the `vscode` object via `useVsCodeApi()`
 * instead of it being threaded through as an explicit prop down every intermediate component.
 */
export default function App({ vscode, initialEntries }: AppProps): JSX.Element {
  const [entries, setEntries] = useState<HistoryEntry[]>(initialEntries);
  const [inFlight, setInFlight] = useState(false);
  const historyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    vscode.setState({ entries });
    historyRef.current?.lastElementChild?.scrollIntoView({ block: 'end' });
  }, [entries, vscode]);

  useEffect(() => {
    function onMessage(event: MessageEvent<unknown>): void {
      const message = parseHostMessage(event.data);
      if (message === undefined) {
        return;
      }
      setEntries((prev) => applyHostMessage(prev, message));
      setInFlight((prev) => applyTurnFlag(prev, message));
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  function submit(text: string): void {
    setEntries((prev) => appendPrompt(prev, text));
    // Raised optimistically; the host's turnStarted/turnError follow-up confirms or clears it.
    setInFlight(true);
    vscode.postMessage({ type: 'submitPrompt', text });
  }

  function cancel(): void {
    vscode.postMessage({ type: 'cancelTurn' });
  }

  return (
    <VsCodeApiProvider vscode={vscode}>
      <div className="title">Klorb session</div>
      <HistoryView entries={entries} historyRef={historyRef} />
      <div id="interaction-area"></div>
      <PromptInput inFlight={inFlight} onSubmit={submit} onCancel={cancel} />
      <div id="status-row"></div>
    </VsCodeApiProvider>
  );
}
