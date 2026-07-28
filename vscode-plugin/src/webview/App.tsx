// © Copyright 2026 Aaron Kimball
import { type JSX, useEffect, useRef, useState } from 'react';

import {
  parseHostMessage,
  type QuestionAskMessage,
  type StatusUpdateMessage,
} from 'shared/webviewMessages';
import ApprovalPanel, { type ApprovalDecision } from 'webview/components/ApprovalPanel';
import PanelHeader from 'webview/components/PanelHeader';
import PromptInput, { type PromptInputHandle } from 'webview/components/PromptInput';
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
  applyTaskListUpdate,
  applyToolCallExpandedToggle,
  applyTurnFlag,
  isScrollPinnedToBottom,
  type HistoryEntry,
  type PendingInteraction,
  type TaskListSnapshot,
} from 'webview/features/history';
import { TaskPanel } from 'webview/features/tasks';

/** The status row's data, without the message envelope's `type` discriminant -- see
 * `shared/webviewMessages.ts`'s `StatusUpdateMessage` for field semantics. */
export type StatusSnapshot = Omit<StatusUpdateMessage, 'type'>;

interface AppProps {
  vscode: VsCodeApi;
  initialEntries: HistoryEntry[];
  initialPendingInteraction?: PendingInteraction;
  initialStatus?: StatusSnapshot;
  initialTaskList?: TaskListSnapshot;
  initialTaskPanelVisible?: boolean;
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
  initialTaskList,
  initialTaskPanelVisible,
}: AppProps): JSX.Element {
  const [entries, setEntries] = useState<HistoryEntry[]>(initialEntries);
  const [inFlight, setInFlight] = useState(false);
  const [expandAllToolCalls, setExpandAllToolCalls] = useState(false);
  const [pendingInteraction, setPendingInteraction] = useState<PendingInteraction | undefined>(
    initialPendingInteraction
  );
  const [status, setStatus] = useState<StatusSnapshot>(initialStatus ?? {});
  const [taskList, setTaskList] = useState<TaskListSnapshot | undefined>(initialTaskList);
  const [taskPanelVisible, setTaskPanelVisible] = useState(initialTaskPanelVisible ?? true);
  const historyRef = useRef<HTMLDivElement>(null);
  const promptInputRef = useRef<PromptInputHandle>(null);
  // Whether the history scroll was at (or near) its bottom edge the last time the user
  // scrolled it -- a ref, not state, since it's read (not rendered) by the autoscroll effect
  // below and must reflect the *latest* scroll position without triggering its own re-render.
  // Mirrors the TUI's `_history_pinned_to_bottom` (see `isScrollPinnedToBottom`).
  const pinnedToBottomRef = useRef(true);

  useEffect(() => {
    vscode.setState({ entries, pendingInteraction, status, taskList, taskPanelVisible });
  }, [entries, pendingInteraction, status, taskList, taskPanelVisible, vscode]);

  useEffect(() => {
    const history = historyRef.current;
    if (history === null) {
      return;
    }
    function onScroll(): void {
      if (history === null) {
        return;
      }
      pinnedToBottomRef.current = isScrollPinnedToBottom(
        history.scrollTop,
        history.scrollHeight,
        history.clientHeight
      );
    }
    history.addEventListener('scroll', onScroll);
    return () => history.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    // Deliberately not keyed on taskList/taskPanelVisible: those live in the same persisted
    // state but don't belong in the history scroll's own trigger set -- a taskListUpdate can
    // arrive several times per turn (once per TodoCreate/TodoUpdate/TodoNext call), and
    // scrolling on every one of those fights the browser's own attempt to keep a focused element
    // elsewhere on the page (e.g. the task panel's own <summary>) in view, which visibly reads as
    // the history freezing until focus moves away. Only follows new content to the bottom while
    // the user hasn't scrolled away from it (see `pinnedToBottomRef`), mirroring the TUI's own
    // `_scroll_if_pinned`.
    if (pinnedToBottomRef.current) {
      historyRef.current?.lastElementChild?.scrollIntoView({ block: 'end' });
    }
  }, [entries, pendingInteraction, status]);

  useEffect(() => {
    // Reclaims focus for the prompt input once a turn is no longer running -- mirrors the
    // TUI's `_finish_turn()`, which always hands focus back to its input box once a turn
    // (successful, errored, or cancelled) is done.
    if (!inFlight) {
      promptInputRef.current?.focus();
    }
  }, [inFlight]);

  useEffect(() => {
    // Reclaims focus once an approval/question panel resolves (pendingInteraction clears),
    // whether or not the turn that raised it is still running -- so the user can keep typing
    // (or queue a message) without an extra click.
    if (pendingInteraction === undefined) {
      promptInputRef.current?.focus();
    }
  }, [pendingInteraction]);

  useEffect(() => {
    function onMessage(event: MessageEvent<unknown>): void {
      const message = parseHostMessage(event.data);
      if (message === undefined) {
        return;
      }
      setEntries((prev) => applyHostMessage(prev, message, expandAllToolCalls));
      setInFlight((prev) => applyTurnFlag(prev, message));
      setPendingInteraction((prev) => applyPendingInteraction(prev, message));
      setTaskList((prev) => applyTaskListUpdate(prev, message));
      if (message.type === 'statusUpdate') {
        // The host always posts the complete currently-known snapshot (never a delta), so a
        // wholesale replace -- not a merge -- is correct here (see `StatusUpdateMessage`'s
        // own doc comment).
        setStatus(message);
      }
      if (message.type === 'toggleTaskPanel') {
        toggleTaskPanelVisible();
      }
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [expandAllToolCalls]);

  function toggleTaskPanelVisible(): void {
    setTaskPanelVisible((prev) => !prev);
  }

  function pickModel(): void {
    vscode.postMessage({ type: 'pickModel' });
  }

  function pickThinking(): void {
    vscode.postMessage({ type: 'pickThinking' });
  }

  function cyclePermissionMode(): void {
    vscode.postMessage({ type: 'cyclePermissionMode' });
  }

  function setPermissionMode(): void {
    vscode.postMessage({ type: 'setPermissionMode' });
  }

  function showSessionStats(): void {
    vscode.postMessage({ type: 'showSessionStats' });
  }

  function newSession(): void {
    vscode.postMessage({ type: 'newSession' });
  }

  /** Fetches this workspace's saved sessions and shows them in a native QuickPick
   * (`klorb.browseSessions`, driven entirely host-side) -- see `PanelHeader`'s stopwatch icon. */
  function browseSessions(): void {
    vscode.postMessage({ type: 'listRecentSessions' });
  }

  function reloadSkills(): void {
    vscode.postMessage({ type: 'reloadSkills' });
  }

  function submit(text: string): void {
    if (inFlight) {
      // Mid-turn submit: queues into the running turn (`_klorb/enqueueMessage`) rather than
      // starting a new one. The history entry itself is created from the host's own
      // `messageQueued` echo (see `historyModel.ts`'s `appendQueuedMessage`), not optimistically
      // here, since the server -- not the webview -- is what actually accepted the message.
      vscode.postMessage({ type: 'enqueueMessage', text });
      return;
    }
    setEntries((prev) => appendPrompt(prev, text));
    // Raised optimistically; the host's turnStarted/turnError follow-up confirms or clears it.
    setInFlight(true);
    vscode.postMessage({ type: 'submitPrompt', text });
  }

  function cancel(): void {
    vscode.postMessage({ type: 'cancelTurn' });
  }

  function restartServer(): void {
    vscode.postMessage({ type: 'restartServer' });
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
      <PanelHeader
        title={sessionTitleText(status)}
        onNewSession={newSession}
        onBrowseSessions={browseSessions}
      />
      {taskPanelVisible ? (
        <TaskPanel taskList={taskList} onToggleVisibility={toggleTaskPanelVisible} />
      ) : null}
      <HistoryView
        entries={entries}
        historyRef={historyRef}
        expandAllToolCalls={expandAllToolCalls}
        onToggleExpandAllToolCalls={toggleExpandAllToolCalls}
        onToggleToolCallExpanded={toggleToolCallExpanded}
        onRestartServer={restartServer}
      />
      <div id="interaction-area">
        {pendingInteraction?.type === 'permissionAsk' ? (
          <ApprovalPanel ask={pendingInteraction} onDecision={handleApprovalDecision} />
        ) : pendingInteraction?.type === 'questionAsk' ? (
          <QuestionPanel ask={pendingInteraction} onAnswer={handleQuestionAnswer} />
        ) : null}
      </div>
      <PromptInput
        ref={promptInputRef}
        inFlight={inFlight}
        muted={pendingInteraction !== undefined}
        enqueueMessageCapable={status.enqueueMessageCapable}
        onSubmit={submit}
        onCancel={cancel}
        onCyclePermissionMode={cyclePermissionMode}
      />
      <StatusRow
        {...status}
        taskPanelVisible={taskPanelVisible}
        onPickModel={pickModel}
        onPickThinking={pickThinking}
        onCyclePermissionMode={cyclePermissionMode}
        onSetPermissionMode={setPermissionMode}
        onShowSessionStats={showSessionStats}
        onNewSession={newSession}
        onReloadSkills={reloadSkills}
        onToggleTaskPanel={toggleTaskPanelVisible}
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
