// © Copyright 2026 Aaron Kimball
import { type JSX, useEffect, useRef, useState } from 'react';

import { parseHostMessage, type PermissionAskMessage } from 'shared/webviewMessages';
import ApprovalPanel, { type ApprovalDecision } from 'webview/components/ApprovalPanel';
import PromptInput from 'webview/components/PromptInput';
import VsCodeApiProvider, { type VsCodeApi } from 'webview/components/VsCodeApiProvider';
import {
  HistoryView,
  appendInteraction,
  appendPrompt,
  applyExpandAllToolCalls,
  applyHostMessage,
  applyPendingAsk,
  applyToolCallExpandedToggle,
  applyTurnFlag,
  type HistoryEntry,
} from 'webview/features/history';

interface AppProps {
  vscode: VsCodeApi;
  initialEntries: HistoryEntry[];
  initialPendingAsk?: PermissionAskMessage;
}

/**
 * The panel's layout shell, top to bottom: the history scroll, an interaction area (mounts
 * `ApprovalPanel` while a permission ask is outstanding; a question panel mounts there in a
 * later increment), the prompt input row, and a placeholder status row. All history/turn/
 * pending-ask state lives here; the pure transition logic is in `webview/features/history`'s
 * `historyModel.ts`. Wraps its content in `<VsCodeApiProvider>` so any descendant can reach the
 * `vscode` object via `useVsCodeApi()` instead of it being threaded through as an explicit prop
 * down every intermediate component.
 */
export default function App({ vscode, initialEntries, initialPendingAsk }: AppProps): JSX.Element {
  const [entries, setEntries] = useState<HistoryEntry[]>(initialEntries);
  const [inFlight, setInFlight] = useState(false);
  const [expandAllToolCalls, setExpandAllToolCalls] = useState(false);
  const [pendingAsk, setPendingAsk] = useState<PermissionAskMessage | undefined>(initialPendingAsk);
  const historyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    vscode.setState({ entries, pendingAsk });
    historyRef.current?.lastElementChild?.scrollIntoView({ block: 'end' });
  }, [entries, pendingAsk, vscode]);

  useEffect(() => {
    function onMessage(event: MessageEvent<unknown>): void {
      const message = parseHostMessage(event.data);
      if (message === undefined) {
        return;
      }
      setEntries((prev) => applyHostMessage(prev, message, expandAllToolCalls));
      setInFlight((prev) => applyTurnFlag(prev, message));
      setPendingAsk((prev) => applyPendingAsk(prev, message));
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [expandAllToolCalls]);

  function submit(text: string): void {
    setEntries((prev) => appendPrompt(prev, text));
    // Raised optimistically; the host's turnStarted/turnError follow-up confirms or clears it.
    setInFlight(true);
    vscode.postMessage({ type: 'submitPrompt', text });
  }

  function cancel(): void {
    vscode.postMessage({ type: 'cancelTurn' });
  }

  function toggleExpandAllToolCalls(): void {
    setExpandAllToolCalls((prev) => {
      const next = !prev;
      setEntries((prevEntries) => applyExpandAllToolCalls(prevEntries, next));
      return next;
    });
  }

  function toggleToolCallExpanded(callId: string): void {
    setEntries((prev) => applyToolCallExpandedToggle(prev, callId));
  }

  function handleApprovalDecision(decision: ApprovalDecision): void {
    if (pendingAsk === undefined) {
      return;
    }
    const decisionName =
      'cancelled' in decision
        ? 'Deny'
        : (pendingAsk.options.find((option) => option.id === decision.optionId)?.name ??
          decision.optionId);
    setEntries((prev) => appendInteraction(prev, pendingAsk, decisionName));
    setPendingAsk(undefined);
    vscode.postMessage(
      'cancelled' in decision
        ? { type: 'permissionDecision', requestId: pendingAsk.requestId, cancelled: true }
        : {
            type: 'permissionDecision',
            requestId: pendingAsk.requestId,
            optionId: decision.optionId,
            otherText: decision.otherText,
          }
    );
  }

  return (
    <VsCodeApiProvider vscode={vscode}>
      <div className="title">Klorb session</div>
      <HistoryView
        entries={entries}
        historyRef={historyRef}
        expandAllToolCalls={expandAllToolCalls}
        onToggleExpandAllToolCalls={toggleExpandAllToolCalls}
        onToggleToolCallExpanded={toggleToolCallExpanded}
      />
      <div id="interaction-area">
        {pendingAsk !== undefined ? (
          <ApprovalPanel ask={pendingAsk} onDecision={handleApprovalDecision} />
        ) : null}
      </div>
      <PromptInput
        inFlight={inFlight}
        muted={pendingAsk !== undefined}
        onSubmit={submit}
        onCancel={cancel}
      />
      <div id="status-row"></div>
    </VsCodeApiProvider>
  );
}
