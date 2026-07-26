// © Copyright 2026 Aaron Kimball
import { type JSX, useEffect, useRef, useState } from 'react';

import { parseHostMessage, type QuestionAskMessage } from 'shared/webviewMessages';
import ApprovalPanel, { type ApprovalDecision } from 'webview/components/ApprovalPanel';
import PromptInput from 'webview/components/PromptInput';
import QuestionPanel, { type QuestionPanelAnswer } from 'webview/components/QuestionPanel';
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

interface AppProps {
  vscode: VsCodeApi;
  initialEntries: HistoryEntry[];
  initialPendingInteraction?: PendingInteraction;
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
}: AppProps): JSX.Element {
  const [entries, setEntries] = useState<HistoryEntry[]>(initialEntries);
  const [inFlight, setInFlight] = useState(false);
  const [expandAllToolCalls, setExpandAllToolCalls] = useState(false);
  const [pendingInteraction, setPendingInteraction] = useState<PendingInteraction | undefined>(
    initialPendingInteraction
  );
  const historyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    vscode.setState({ entries, pendingInteraction });
    historyRef.current?.lastElementChild?.scrollIntoView({ block: 'end' });
  }, [entries, pendingInteraction, vscode]);

  useEffect(() => {
    function onMessage(event: MessageEvent<unknown>): void {
      const message = parseHostMessage(event.data);
      if (message === undefined) {
        return;
      }
      setEntries((prev) => applyHostMessage(prev, message, expandAllToolCalls));
      setInFlight((prev) => applyTurnFlag(prev, message));
      setPendingInteraction((prev) => applyPendingInteraction(prev, message));
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
      <div className="title">Klorb session</div>
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
      />
      <div id="status-row"></div>
    </VsCodeApiProvider>
  );
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
