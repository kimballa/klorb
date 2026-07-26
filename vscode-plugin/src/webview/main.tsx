// © Copyright 2026 Aaron Kimball
// Importing these modules registers the <vscode-badge>/<vscode-button>/<vscode-icon>/
// <vscode-progress-ring>/<vscode-textarea>/<vscode-textfield> custom elements with the
// browser; the components themselves are rendered from App.tsx/PromptInput.tsx/
// ToolCallChip.tsx/ApprovalPanel.tsx.
import '@vscode-elements/elements/dist/vscode-badge/index.js';
import '@vscode-elements/elements/dist/vscode-button/index.js';
import '@vscode-elements/elements/dist/vscode-icon/index.js';
import '@vscode-elements/elements/dist/vscode-progress-ring/index.js';
import '@vscode-elements/elements/dist/vscode-textarea/index.js';
import '@vscode-elements/elements/dist/vscode-textfield/index.js';
import { createRoot } from 'react-dom/client';

import type { PermissionAskMessage } from 'shared/webviewMessages';
import App from 'webview/App';
import type { VsCodeApi } from 'webview/components/VsCodeApiProvider';
import type { HistoryEntry } from 'webview/features/history';

declare function acquireVsCodeApi(): VsCodeApi;

interface SessionState {
  entries: HistoryEntry[];
  pendingAsk?: PermissionAskMessage;
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
  createRoot(container).render(
    <App vscode={vscode} initialEntries={state.entries} initialPendingAsk={state.pendingAsk} />
  );
}

main();
