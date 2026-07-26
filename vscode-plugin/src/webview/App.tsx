// © Copyright 2026 Aaron Kimball
import { type JSX, useEffect, useRef, useState } from 'react';

import {
  parseHostMessage,
  type QuestionAskMessage,
  type StatusUpdateMessage,
} from 'shared/webviewMessages';
import ApprovalPanel, { type ApprovalDecision } from 'webview/components/ApprovalPanel';
import PromptInput from 'webview/components/PromptInput';
import QuestionPanel, { type QuestionPanelAnswer } from 'webview/components/QuestionPanel';
import StatusRow from 'webview/components/StatusRow';
import VsCodeApiProvider, { type VsCodeApi } from 'webview/components/VsCodeApiProvider';
import {
  HistoryView,
  appendInteraction,
  appendPrompt,
  appendQuestionInteraction,
  applyExpandAllToolCalls,
  applyHostMessage,
  applyPendingInteraction,
  applyToolCallExpandedToggle,
  applyTurnFlag,
  type HistoryEntry,
  type PendingInteraction,
} from 'webview/features/history';

/** The status row's data, without the message envelope's `type` discriminant -- see
 * `shared/webviewMessages.ts`'s `StatusUpdateMessage` for field semantics. */
export type StatusSnapshot = Omit<StatusUpdateMessage, 'type'>;

interface AppProps {
  vscode: VsCodeApi;
  initialEntries: HistoryEntry[];
  initialPendingInteraction?: PendingInteraction;
  initialStatus?: StatusSnapshot;
}

/**
 * The panel's layout shell, top to bottom: the history scroll, an interaction area (mounts
 * `ApprovalPanel` while a permission ask is outstanding, or `QuestionPanel` while a
 * `_klorb/askUserQuestions` question is outstanding), the prompt input row, and a placeholder
 * status row. All history/turn/pending-interaction state lives here; the pure transition logic
 * is in `webview/features/history`'s `historyModel.ts`. Wraps its content in
 * `<VsCodeApiProvider>` so any descendant can reach the `vscode` object via `useVsCodeApi()`
 * instead of it being threaded through as an explicit prop down every intermediate component.
 */
export default function App({
  vscode,
  initialEntries,
  initialPendingInteraction,
  initialStatus,
}: AppProps): JSX.Element {
  const [entries, setEntries] = useState<HistoryEntry[]>(initialEntries);
  const [inFlight, setInFlight] = useState(false);
  const [expandAllToolCalls, setExpandAllToolCalls] = useState(false);
  const [pendingInteraction, setPendingInteraction] = useState<PendingInteraction | undefined>(
    initialPendingInteraction
  );
  const [status, setStatus] = useState<StatusSnapshot>(initialStatus ?? {});
  const historyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    vscode.setState({ entries, pendingInteraction, status });
    historyRef.current?.lastElementChild?.scrollIntoView({ block: 'end' });
  }, [entries, pendingInteraction, status, vscode]);

  useEffect(() => {
    function onMessage(event: MessageEvent<unknown>): void {
      const message = parseHostMessage(event.data);
      if (message === undefined) {
        return;
      }
      setEntries((prev) => applyHostMessage(prev, message, expandAllToolCalls));
      setInFlight((prev) => applyTurnFlag(prev, message));
      setPendingInteraction((prev) => applyPendingInteraction(prev, message));
      if (message.type === 'statusUpdate') {
        // The host always posts the complete currently-known snapshot (never a delta), so a
        // wholesale replace -- not a merge -- is correct here (see `StatusUpdateMessage`'s
        // own doc comment).
        setStatus(message);
      }
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [expandAllToolCalls]);

  function pickModel(): void {
    vscode.postMessage({ type: 'pickModel' });
  }

  function pickThinking(): void {
    vscode.postMessage({ type: 'pickThinking' });
  }

  function cyclePermissionMode(): void {
    vscode.postMessage({ type: 'cyclePermissionMode' });
  }

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
    if (pendingInteraction === undefined || pendingInteraction.type !== 'permissionAsk') {
      return;
    }
    const ask = pendingInteraction;
    const decisionName =
      'cancelled' in decision
        ? 'Deny'
        : (ask.options.find((option) => option.id === decision.optionId)?.name ??
          decision.optionId);
    setEntries((prev) => appendInteraction(prev, ask, decisionName));
    setPendingInteraction(undefined);
    vscode.postMessage(
      'cancelled' in decision
        ? { type: 'permissionDecision', requestId: ask.requestId, cancelled: true }
        : {
            type: 'permissionDecision',
            requestId: ask.requestId,
            optionId: decision.optionId,
            otherText: decision.otherText,
          }
    );
  }

  function handleQuestionAnswer(answer: QuestionPanelAnswer): void {
    if (pendingInteraction === undefined || pendingInteraction.type !== 'questionAsk') {
      return;
    }
    const ask = pendingInteraction;
    const answerText = questionAnswerText(ask, answer);
    setEntries((prev) => appendQuestionInteraction(prev, ask, answerText));
    setPendingInteraction(undefined);
    vscode.postMessage(
      'cancelled' in answer
        ? { type: 'questionAnswer', requestId: ask.requestId, cancelled: true }
        : 'otherText' in answer
          ? { type: 'questionAnswer', requestId: ask.requestId, otherText: answer.otherText }
          : {
              type: 'questionAnswer',
              requestId: ask.requestId,
              selectedOptionIndex: answer.selectedOptionIndex,
            }
    );
  }

  return (
    <VsCodeApiProvider vscode={vscode}>
      <div className="title">{sessionTitleText(status)}</div>
      <HistoryView
        entries={entries}
        historyRef={historyRef}
        expandAllToolCalls={expandAllToolCalls}
        onToggleExpandAllToolCalls={toggleExpandAllToolCalls}
        onToggleToolCallExpanded={toggleToolCallExpanded}
      />
      <div id="interaction-area">
        {pendingInteraction?.type === 'permissionAsk' ? (
          <ApprovalPanel ask={pendingInteraction} onDecision={handleApprovalDecision} />
        ) : pendingInteraction?.type === 'questionAsk' ? (
          <QuestionPanel ask={pendingInteraction} onAnswer={handleQuestionAnswer} />
        ) : null}
      </div>
      <PromptInput
        inFlight={inFlight}
        muted={pendingInteraction !== undefined}
        onSubmit={submit}
        onCancel={cancel}
        onCyclePermissionMode={cyclePermissionMode}
      />
      <StatusRow
        {...status}
        onPickModel={pickModel}
        onPickThinking={pickThinking}
        onCyclePermissionMode={cyclePermissionMode}
      />
    </VsCodeApiProvider>
  );
}

/** The panel's top title bar text: the active session's title, `New session…` until one
 * arrives, with an `(Untrusted)` suffix appended whenever `workspaceTrusted === false` (TUI
 * header parity). */
function sessionTitleText(status: StatusSnapshot): string {
  const title = status.sessionTitle ?? 'New session…';
  return status.workspaceTrusted === false ? `${title} (Untrusted)` : title;
}

/** Renders a `QuestionPanelAnswer` as the compact history text `appendQuestionInteraction()`
 * records: the selected option's own `"label: description"`/`"label"` rendering (mirroring the
 * server's own `format_answer`), the typed free text, or `"(cancelled)"`. */
function questionAnswerText(ask: QuestionAskMessage, answer: QuestionPanelAnswer): string {
  if ('cancelled' in answer) {
    return '(cancelled)';
  }
  if ('otherText' in answer) {
    return answer.otherText;
  }
  const option = ask.options[answer.selectedOptionIndex];
  if (option === undefined) {
    return '';
  }
  return option.description !== undefined ? `${option.label}: ${option.description}` : option.label;
}
