// © Copyright 2026 Aaron Kimball
// Importing these modules registers the <vscode-badge>/<vscode-button>/<vscode-context-menu>/
// <vscode-context-menu-item>/<vscode-icon>/<vscode-progress-ring>/<vscode-textarea>/
// <vscode-textfield> custom elements with the browser; the components themselves are rendered
// from App.tsx/PromptInput.tsx/ToolCallChip.tsx/ApprovalPanel.tsx/QuestionPanel.tsx/
// StatusMenu.tsx/TaskPanel.tsx.
import '@vscode-elements/elements/dist/vscode-badge/index.js';
import '@vscode-elements/elements/dist/vscode-button/index.js';
import '@vscode-elements/elements/dist/vscode-context-menu/index.js';
import '@vscode-elements/elements/dist/vscode-context-menu-item/index.js';
import '@vscode-elements/elements/dist/vscode-icon/index.js';
import '@vscode-elements/elements/dist/vscode-progress-ring/index.js';
import '@vscode-elements/elements/dist/vscode-textarea/index.js';
import '@vscode-elements/elements/dist/vscode-textfield/index.js';
import { createRoot } from 'react-dom/client';

import App from 'webview/App';
import ErrorBoundary from 'webview/components/ErrorBoundary';
import type { VsCodeApi } from 'webview/components/VsCodeApiProvider';
import { readPersistedState } from 'webview/features/sessionState';

declare function acquireVsCodeApi(): VsCodeApi;

function main(): void {
  // acquireVsCodeApi() throws if called more than once per page load, so the single call
  // result is threaded through rather than each function calling it for itself.
  const vscode = acquireVsCodeApi();
  const state = readPersistedState(vscode);

  const container = document.getElementById('root');
  if (container === null) {
    throw new Error('#root element is missing from the webview HTML shell');
  }
  createRoot(container).render(
    <ErrorBoundary vscode={vscode}>
      <App
        vscode={vscode}
        initialEntries={state.entries}
        initialPendingInteraction={state.pendingInteraction}
        initialStatus={state.status}
        initialTaskList={state.taskList}
        initialTaskPanelVisible={state.taskPanelVisible}
        initialSubagentsPanelVisible={state.subagentsPanelVisible}
        initialSelectedSubagentId={state.selectedSubagentId}
      />
    </ErrorBoundary>
  );
}

main();
