// © Copyright 2026 Aaron Kimball
// Importing these modules registers the <vscode-textarea>/<vscode-button> custom elements
// with the browser; the components themselves are rendered from App.tsx/PromptInput.tsx.
import '@vscode-elements/elements/dist/vscode-button/index.js';
import '@vscode-elements/elements/dist/vscode-textarea/index.js';
import { createRoot } from 'react-dom/client';

import { App, type VsCodeApi } from './App';
import type { HistoryEntry } from './historyModel';

declare function acquireVsCodeApi(): VsCodeApi;

interface SessionState {
  entries: HistoryEntry[];
}

function main(): void {
  // acquireVsCodeApi() throws if called more than once per page load, so the single call
  // result is threaded through rather than each function calling it for itself.
  const vscode = acquireVsCodeApi();
  const state = (vscode.getState() as SessionState | undefined) ?? { entries: [] };

  const container = document.getElementById('root');
  if (container === null) {
    throw new Error('#root element is missing from the webview HTML shell');
  }
  createRoot(container).render(<App vscode={vscode} initialEntries={state.entries} />);
}

main();
