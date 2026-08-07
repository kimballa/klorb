// © Copyright 2026 Aaron Kimball
import { type JSX, useCallback, useEffect, useRef, useState } from 'react';

import {
  parseHostMessage,
  type ImageAttachment,
  type QuestionAskMessage,
  type SkillEntry,
  type StatusUpdateMessage,
  type SubagentNodeInfo,
} from 'shared/webviewMessages';
import ApprovalPanel, { type ApprovalDecision } from 'webview/components/ApprovalPanel';
import PanelHeader from 'webview/components/PanelHeader';
import PromptInput, { type PromptInputHandle } from 'webview/components/PromptInput';
import QuestionPanel, { type QuestionPanelAnswer } from 'webview/components/QuestionPanel';
import StatusRow from 'webview/components/StatusRow';
import ToolCallLimitPanel, {
  type ToolCallLimitDecision,
} from 'webview/components/ToolCallLimitPanel';
import VsCodeApiProvider, { type VsCodeApi } from 'webview/components/VsCodeApiProvider';
import {
  HistoryView,
  appendInteraction,
  appendPrompt,
  appendQuestionInteraction,
  appendToolCallLimitInteraction,
  applyHostMessage,
  applyPendingInteraction,
  applyTaskListUpdate,
  applyToolCallExpandedToggle,
  applyTurnFlag,
  type HistoryEntry,
  type PendingInteraction,
  type TaskListSnapshot,
} from 'webview/features/history';
import {
  SubagentsPanel,
  SubagentTranscriptView,
  applySubagentTranscriptUpdate,
  applySubagentTreeUpdate,
  findRootNode,
  subagentTranscriptEntries,
  type SubagentTranscriptSnapshot,
} from 'webview/features/subagents';
import { TaskPanel } from 'webview/features/tasks';
import usePinnedScroll from 'webview/hooks/usePinnedScroll';

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
  initialSubagentsPanelVisible?: boolean;
  initialSelectedSubagentId?: string | null;
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
  initialSubagentsPanelVisible,
  initialSelectedSubagentId,
}: AppProps): JSX.Element {
  const [entries, setEntries] = useState<HistoryEntry[]>(initialEntries);
  const [inFlight, setInFlight] = useState(false);
  const [pendingInteraction, setPendingInteraction] = useState<PendingInteraction | undefined>(
    initialPendingInteraction
  );
  const [status, setStatus] = useState<StatusSnapshot>(initialStatus ?? {});
  // Not persisted via `vscode.setState()`: the host re-pushes the current workspace file list
  // every time the view resolves (see `KlorbSessionViewProvider._postWorkspaceFiles()`), so a
  // fresh value always follows shortly after a webview reload rather than risking a stale one.
  const [workspaceFiles, setWorkspaceFiles] = useState<string[]>([]);
  // Re-pushed by the host on resolve and after a reload; not persisted via `vscode.setState()`
  // for the same reason as `workspaceFiles`.
  const [skills, setSkills] = useState<SkillEntry[]>([]);
  // Re-pushed by the host on resolve and after each submitted prompt; not persisted via
  // `vscode.setState()` for the same reason as `workspaceFiles`.
  const [promptHistory, setPromptHistory] = useState<string[]>([]);
  const [taskList, setTaskList] = useState<TaskListSnapshot | undefined>(initialTaskList);
  const [taskPanelVisible, setTaskPanelVisible] = useState(initialTaskPanelVisible ?? true);
  // The subagent tree/transcript/expansion state below is deliberately not persisted -- see
  // `SessionState`'s own doc comment (`webview/features/sessionState`).
  const [subagentNodes, setSubagentNodes] = useState<SubagentNodeInfo[]>([]);
  const [subagentTranscript, setSubagentTranscript] = useState<
    SubagentTranscriptSnapshot | undefined
  >(undefined);
  const [subagentExpandedCallIds, setSubagentExpandedCallIds] = useState<ReadonlySet<string>>(
    new Set()
  );
  const [subagentInterruptPending, setSubagentInterruptPending] = useState<string | undefined>(
    undefined
  );
  const [subagentsPanelVisible, setSubagentsPanelVisible] = useState(
    initialSubagentsPanelVisible ?? false
  );
  const [selectedSubagentId, setSelectedSubagentId] = useState<string | null>(
    initialSelectedSubagentId ?? null
  );
  /** True if all thinking blocks should be auto-expanded. */
  const [allThinkingExpanded, setAllThinkingExpanded] = useState<boolean>(false);
  const promptInputRef = useRef<PromptInputHandle>(null);
  // Follows new content to the bottom only while the user hasn't scrolled away from it, mirroring
  // the TUI's own `_scroll_if_pinned`/`_subagent_history_pinned_to_bottom` -- two independent
  // instances, one per scrolling transcript view (root and subagent), since a reader can scroll
  // either one away from its own bottom independently of the other.
  const {
    containerRef: historyRef,
    scrollToBottomIfPinned: scrollHistoryIfPinned,
    scrollToBottom: scrollHistoryToBottom,
  } = usePinnedScroll<HTMLDivElement>();

  /** Flips the subagents panel's shown/hidden state and tells the host, so its `SubagentPoller`
   * starts/stops the tree poll accordingly -- unlike `toggleTaskPanelVisible()` further down,
   * this state has a host-side effect (the task panel has none), hence the `postMessage`
   * alongside the flip. `useCallback`'d (stable identity, `vscode` never actually changes) so it
   * can safely be referenced from the mount-once message-listener effect further down. */
  const toggleSubagentsPanelVisible = useCallback((): void => {
    setSubagentsPanelVisible((prev) => {
      const next = !prev;
      vscode.postMessage({ type: 'setSubagentsPanelVisible', visible: next });
      return next;
    });
  }, [vscode]);

  /** Selects a row in the subagents panel (`null` selects the root session), clearing whatever
   * transcript/interrupt state belonged to the previous selection and telling the host so its
   * `SubagentPoller` starts polling the new selection's transcript (or stops entirely for
   * `null`) -- see docs/specs/vscode-plugin.md's "Subagents panel" section. `useCallback`'d for
   * the same reason as `toggleSubagentsPanelVisible` above. */
  const selectSubagentRow = useCallback(
    (sessionId: string | null): void => {
      setSelectedSubagentId(sessionId);
      setSubagentTranscript(undefined);
      setSubagentInterruptPending(undefined);
      vscode.postMessage({ type: 'selectSubagent', sessionId });
    },
    [vscode]
  );

  /** Opens the subagents panel if it isn't already shown -- called when a `CreateSubagent` tool
   * call starts, so a session's first subagent surfaces the panel automatically instead of
   * requiring the user to notice and open it manually. A no-op once the panel is already visible,
   * unlike `toggleSubagentsPanelVisible`, which would incorrectly close it on a second call. */
  const showSubagentsPanel = useCallback((): void => {
    setSubagentsPanelVisible((prev) => {
      if (prev) {
        return prev;
      }
      vscode.postMessage({ type: 'setSubagentsPanelVisible', visible: true });
      return true;
    });
  }, [vscode]);

  useEffect(() => {
    vscode.setState({
      entries,
      pendingInteraction,
      status,
      taskList,
      taskPanelVisible,
      subagentsPanelVisible,
      selectedSubagentId,
    });
  }, [
    entries,
    pendingInteraction,
    status,
    taskList,
    taskPanelVisible,
    subagentsPanelVisible,
    selectedSubagentId,
    vscode,
  ]);

  useEffect(() => {
    // Deliberately not keyed on taskList/taskPanelVisible: those live in the same persisted
    // state but don't belong in the history scroll's own trigger set -- a taskListUpdate can
    // arrive several times per turn (once per TodoCreate/TodoUpdate/TodoNext call), and
    // scrolling on every one of those fights the browser's own attempt to keep a focused element
    // elsewhere on the page (e.g. the task panel's own <summary>) in view, which visibly reads as
    // the history freezing until focus moves away.
    scrollHistoryIfPinned();
  }, [entries, pendingInteraction, status, scrollHistoryIfPinned]);

  // Scroll the root history view to its bottom when switching back from a subagent --
  // the HistoryView DOM node remounts fresh (scrollTop = 0) after being unmounted while a
  // subagent was selected, so the content-change effect above doesn't fire and the pinned
  // state is stale.  Keying on `selectedSubagentId` rather than a derived `isSubagentSelected`
  // so the effect fires on the exact render where the selection changes, not on every render
  // where the root happens to be selected.
  useEffect(() => {
    if (selectedSubagentId === null) {
      scrollHistoryToBottom();
    }
  }, [selectedSubagentId, scrollHistoryToBottom]);

  useEffect(() => {
    // Re-syncs the host's `SubagentPoller` to this webview instance's restored panel-visibility/
    // selection state -- the poller's own timers live in the extension host, which can outlive a
    // webview reload (or start fresh alongside one), so this instance's restored state and the
    // poller's live state must be explicitly reconciled once, right after mount, rather than
    // assumed to already agree.
    vscode.postMessage({
      type: 'setSubagentsPanelVisible',
      visible: initialSubagentsPanelVisible ?? false,
    });
    if ((initialSelectedSubagentId ?? null) !== null) {
      vscode.postMessage({
        type: 'selectSubagent',
        sessionId: initialSelectedSubagentId ?? null,
      });
    }
    // Pulls the current prompt history from the host -- the host also pushes it on view resolve,
    // but that push may arrive before this listener is registered; this pull is the reliable path.
    vscode.postMessage({ type: 'requestPromptHistory' });
  }, [initialSubagentsPanelVisible, initialSelectedSubagentId, vscode]);

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
      setEntries((prev) => applyHostMessage(prev, message));
      setInFlight((prev) => applyTurnFlag(prev, message));
      setPendingInteraction((prev) => applyPendingInteraction(prev, message));
      setTaskList((prev) => applyTaskListUpdate(prev, message));
      setSubagentNodes((prev) => applySubagentTreeUpdate(prev, message));
      setSubagentTranscript((prev) => applySubagentTranscriptUpdate(prev, message));
      if (message.type === 'subagentTranscriptUpdate' && message.state === 'finished') {
        // Clears the optimistic "Sending interrupt…" notice once a poll confirms the
        // interrupted subagent has actually finished -- mirrors the TUI's own
        // `_subagent_interrupt_pending` clearing once `_mount_subagent_status_notice` reaches
        // the finished branch.
        setSubagentInterruptPending((prev) => (prev === message.sessionId ? undefined : prev));
      }
      if (message.type === 'statusUpdate') {
        // The host always posts the complete currently-known snapshot (never a delta), so a
        // wholesale replace -- not a merge -- is correct here (see `StatusUpdateMessage`'s
        // own doc comment).
        setStatus(message);
      }
      if (message.type === 'toggleTaskPanel') {
        toggleTaskPanelVisible();
      }
      if (message.type === 'toggleSubagentsPanel') {
        toggleSubagentsPanelVisible();
      }
      if (message.type === 'toolCallStarted' && message.toolName === 'CreateSubagent') {
        showSubagentsPanel();
      }
      if (message.type === 'sessionReset') {
        // A fresh/loaded session's subagent tree is unrelated to whatever the previous one had
        // -- deselect back to the root and tell the host so its poller stops tracking a
        // subagent id that no longer exists.
        selectSubagentRow(null);
      }
      if (message.type === 'workspaceFiles') {
        setWorkspaceFiles(message.files);
      }
      if (message.type === 'skills') {
        setSkills(message.entries);
      }
      if (message.type === 'promptHistory') {
        setPromptHistory(message.entries);
      }
      if (message.type === 'imageAttached') {
        promptInputRef.current?.addAttachment(message.image);
      }
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [toggleSubagentsPanelVisible, showSubagentsPanel, selectSubagentRow]);

  /** Flips the task panel's shown/hidden state -- unlike `toggleSubagentsPanelVisible`, this
   * state has no host-side effect, so there's no accompanying `postMessage`. `TaskPanel` always
   * renders once `taskPanelVisible` is `true` (a "No tasks available" placeholder before the
   * first plan update, real content after -- see `TaskPanel`'s own doc comment), so this flag
   * alone is an accurate stand-in for what's on screen. */
  function toggleTaskPanelVisible(): void {
    setTaskPanelVisible((prev) => !prev);
  }

  /** Cancels the selected subagent's turn (Stop button while a subagent, not the root, is
   * selected) -- shows "Sending interrupt…" immediately, mirroring the TUI's own optimistic
   * notice, until a later poll confirms the subagent actually finished. */
  function cancelSubagentTurn(): void {
    if (selectedSubagentId === null) {
      return;
    }
    setSubagentInterruptPending(selectedSubagentId);
    vscode.postMessage({ type: 'cancelSubagent', sessionId: selectedSubagentId });
  }

  /** Opens the subagents panel and selects the session an outstanding ask belongs to -- the
   * fallback attention bar's own click handler (shown only while the panel itself is hidden). */
  function openAttentionSubagent(): void {
    if (attentionSessionId === undefined) {
      return;
    }
    toggleSubagentsPanelVisible();
    selectSubagentRow(attentionSessionId);
  }

  function toggleSubagentToolCallExpanded(callId: string): void {
    setSubagentExpandedCallIds((prev) => {
      const next = new Set(prev);
      if (next.has(callId)) {
        next.delete(callId);
      } else {
        next.add(callId);
      }
      return next;
    });
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

  function submit(text: string, images?: ImageAttachment[]): void {
    if (selectedSubagentId !== null) {
      // Addresses the selected subagent directly (`_klorb/subagentPrompt`), bypassing the root
      // session entirely -- the server decides direct-send-vs-enqueue from the subagent's own
      // live state, so there's no `inFlight` gate to check here the way there is for root (see
      // docs/specs/vscode-plugin.md's "Subagents panel" section). The subagent's own transcript
      // poller picks up the sent message; nothing here needs to touch `entries`/`inFlight`.
      vscode.postMessage({
        type: 'submitPrompt',
        text,
        subagentId: selectedSubagentId,
        ...(images ? { images } : {}),
      });
      return;
    }
    if (inFlight) {
      // Mid-turn submit: queues into the running turn (`_klorb/enqueueMessage`) rather than
      // starting a new one. The history entry itself is created from the host's own
      // `messageQueued` echo (see `historyModel.ts`'s `appendQueuedMessage`), not optimistically
      // here, since the server -- not the webview -- is what actually accepted the message.
      // (The host rejects `images` here -- queued messages have no content-block channel.)
      vscode.postMessage({ type: 'enqueueMessage', text, ...(images ? { images } : {}) });
      return;
    }
    setEntries((prev) => appendPrompt(prev, text, images));
    // Raised optimistically; the host's turnStarted/turnError follow-up confirms or clears it.
    setInFlight(true);
    vscode.postMessage({ type: 'submitPrompt', text, ...(images ? { images } : {}) });
  }

  function attachImage(): void {
    vscode.postMessage({ type: 'attachImageFile' });
  }

  function cancel(): void {
    vscode.postMessage({ type: 'cancelTurn' });
  }

  function restartServer(): void {
    vscode.postMessage({ type: 'restartServer' });
  }

  function toggleToolCallExpanded(callId: string): void {
    setEntries((prev) => applyToolCallExpandedToggle(prev, callId));
  }

  function onToggleAllThinkingExpanded() {
    setAllThinkingExpanded((curAllThinkingExpanded) => !curAllThinkingExpanded);
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

  function handleToolCallLimitDecision(decision: ToolCallLimitDecision): void {
    if (pendingInteraction === undefined || pendingInteraction.type !== 'toolCallLimitAsk') {
      return;
    }
    const ask = pendingInteraction;
    const answerText = 'approved' in decision ? 'Continue' : '(denied)';
    setEntries((prev) => appendToolCallLimitInteraction(prev, ask, answerText));
    setPendingInteraction(undefined);
    vscode.postMessage(
      'approved' in decision
        ? { type: 'toolCallLimitDecision', requestId: ask.requestId, approved: true }
        : { type: 'toolCallLimitDecision', requestId: ask.requestId, cancelled: true }
    );
  }

  const isSubagentSelected = selectedSubagentId !== null;
  const selectedSubagentNode = subagentNodes.find((node) => node.id === selectedSubagentId);
  const rootNode = findRootNode(subagentNodes);
  const pendingOriginId =
    pendingInteraction !== undefined && 'originSessionId' in pendingInteraction
      ? (pendingInteraction.originSessionId ?? null)
      : null;
  // An ask raised by a subagent's turn is only shown in the interaction area once that
  // subagent (its `originSessionId`) is the one currently selected -- mirrors the TUI's
  // `SubagentsPanelMixin._await_session_selected` gate; while it isn't, the ask instead shows
  // as an attention marker on its own row (see `attentionSessionId` below).
  const interactionVisible =
    pendingInteraction !== undefined && pendingOriginId === selectedSubagentId;
  const attentionSessionId =
    pendingInteraction !== undefined && !interactionVisible
      ? pendingOriginId === null
        ? rootNode?.id
        : pendingOriginId
      : undefined;
  const activeSubagentTranscript =
    subagentTranscript !== undefined && subagentTranscript.sessionId === selectedSubagentId
      ? subagentTranscript
      : undefined;
  const headerTitle = isSubagentSelected
    ? `${selectedSubagentNode?.address ?? '?'} ${selectedSubagentNode?.title ?? 'New session…'}`
    : sessionTitleText(status);
  // Shown only while the subagents panel itself is hidden -- once it's open, the panel's own
  // blinking "!" row marker already surfaces the same fact, mirroring the TUI's
  // `#subagent-attention-status` fallback (`_update_subagent_attention_status_line`).
  const attentionNode = subagentNodes.find((node) => node.id === attentionSessionId);
  const showAttentionFallback = !subagentsPanelVisible && attentionNode !== undefined;
  const effectiveStatus = selectedStatusOverrides(
    status,
    isSubagentSelected ? selectedSubagentNode : undefined
  );

  return (
    <VsCodeApiProvider vscode={vscode}>
      <PanelHeader
        title={headerTitle}
        onNewSession={newSession}
        onBrowseSessions={browseSessions}
      />
      {subagentsPanelVisible ? (
        <SubagentsPanel
          nodes={subagentNodes}
          selectedSessionId={selectedSubagentId}
          attentionSessionId={attentionSessionId}
          onSelect={selectSubagentRow}
          onToggleVisibility={toggleSubagentsPanelVisible}
        />
      ) : null}
      {taskPanelVisible ? (
        <TaskPanel taskList={taskList} onToggleVisibility={toggleTaskPanelVisible} />
      ) : null}
      {isSubagentSelected ? (
        <SubagentTranscriptView
          key={selectedSubagentId}
          entries={subagentTranscriptEntries(activeSubagentTranscript, subagentExpandedCallIds)}
          state={activeSubagentTranscript?.state}
          aborted={activeSubagentTranscript?.aborted ?? false}
          allThinkingExpanded={allThinkingExpanded}
          interruptPending={subagentInterruptPending === selectedSubagentId}
          onToggleToolCallExpanded={toggleSubagentToolCallExpanded}
        />
      ) : (
        <HistoryView
          entries={entries}
          historyRef={historyRef}
          allThinkingExpanded={allThinkingExpanded}
          onToggleToolCallExpanded={toggleToolCallExpanded}
          onRestartServer={restartServer}
        />
      )}
      <div id="interaction-area">
        {interactionVisible && pendingInteraction?.type === 'permissionAsk' ? (
          <ApprovalPanel ask={pendingInteraction} onDecision={handleApprovalDecision} />
        ) : interactionVisible && pendingInteraction?.type === 'questionAsk' ? (
          <QuestionPanel ask={pendingInteraction} onAnswer={handleQuestionAnswer} />
        ) : interactionVisible && pendingInteraction?.type === 'toolCallLimitAsk' ? (
          <ToolCallLimitPanel ask={pendingInteraction} onDecision={handleToolCallLimitDecision} />
        ) : null}
      </div>
      {showAttentionFallback ? (
        <button type="button" id="subagent-attention-fallback" onClick={openAttentionSubagent}>
          Agent {attentionNode.address} needs your input
        </button>
      ) : null}
      <PromptInput
        ref={promptInputRef}
        inFlight={isSubagentSelected ? activeSubagentTranscript?.state === 'running' : inFlight}
        muted={interactionVisible}
        // While a subagent is selected, `_klorb/subagentPrompt` decides direct-send-vs-enqueue
        // itself from the subagent's own live state (see `submit()`), so the input must stay
        // enabled regardless of that subagent's `inFlight` value above -- forcing
        // `enqueueMessageCapable` true here is what keeps it enabled either way.
        enqueueMessageCapable={isSubagentSelected ? true : status.enqueueMessageCapable}
        sessionKey={selectedSubagentId ?? 'root'}
        workspaceFiles={workspaceFiles}
        skills={skills}
        promptHistory={promptHistory}
        imagesCapable={status.activeModelVision}
        onSubmit={submit}
        onCancel={isSubagentSelected ? cancelSubagentTurn : cancel}
        onCyclePermissionMode={cyclePermissionMode}
      />
      <StatusRow
        {...effectiveStatus}
        taskPanelVisible={taskPanelVisible}
        subagentsPanelVisible={subagentsPanelVisible}
        allThinkingExpanded={allThinkingExpanded}
        interactive={!isSubagentSelected}
        onPickModel={pickModel}
        onPickThinking={pickThinking}
        onCyclePermissionMode={cyclePermissionMode}
        onSetPermissionMode={setPermissionMode}
        onShowSessionStats={showSessionStats}
        onNewSession={newSession}
        onReloadSkills={reloadSkills}
        onToggleTaskPanel={toggleTaskPanelVisible}
        onToggleSubagentsPanel={toggleSubagentsPanelVisible}
        onToggleAllThinkingExpanded={onToggleAllThinkingExpanded}
        onAttachImage={attachImage}
      />
    </VsCodeApiProvider>
  );
}

/** `StatusRow`'s effective data source: `status` verbatim while the root session is selected, or
 * `node`'s own model/thinking/token fields overlaid on top of it while a subagent is selected --
 * a subagent's model/thinking config is fixed at creation and its token tally is its own (see
 * docs/specs/subagents.md's "Subagent session model" section), so the chips/tally must follow
 * `node`, not the root's `status`, once one is selected. `node === undefined` (root selected, or
 * the selected subagent hasn't appeared in a tree snapshot yet) leaves `status` untouched. */
function selectedStatusOverrides(
  status: StatusSnapshot,
  node: SubagentNodeInfo | undefined
): StatusSnapshot {
  if (node === undefined) {
    return status;
  }
  return {
    ...status,
    model: node.model,
    thinkingEnabled: node.thinkingEnabled,
    thinkingEffort: node.thinkingEffort,
    usedTokens: node.usedTokens,
    maxTokens: node.maxTokens,
    outputTokens: node.outputTokens,
  };
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
