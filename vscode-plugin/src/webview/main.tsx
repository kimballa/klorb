// © Copyright 2026 Aaron Kimball
import { createRoot } from 'react-dom/client';

import { App, type ChatEntry, type VsCodeApi } from './App';

declare function acquireVsCodeApi(): VsCodeApi;

interface SessionState {
  entries: ChatEntry[];
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
