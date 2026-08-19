// © Copyright 2026 Aaron Kimball
import { createRoot } from 'react-dom/client';

import App from 'webview/App';
import ErrorBoundary from 'webview/components/ErrorBoundary';
import type { VsCodeApi } from 'webview/components/VsCodeApiProvider';
import { readPersistedState } from 'webview/features/sessionState';
import 'webview/registerCustomElements';

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
