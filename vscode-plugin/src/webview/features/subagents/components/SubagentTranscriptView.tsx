// © Copyright 2026 Aaron Kimball
import { type JSX, useEffect, useState } from 'react';

import { HistoryView, type HistoryEntry } from 'webview/features/history';
import usePinnedScroll from 'webview/hooks/usePinnedScroll';

export interface SubagentTranscriptViewProps {
  entries: HistoryEntry[];
  /** The selected subagent's own lifecycle state, or `undefined` while no
   * `subagentTranscriptUpdate` has arrived yet for the current selection (e.g. the poll is still
   * in flight right after selecting a row). */
  state: 'running' | 'finished' | undefined;
  aborted: boolean;
  /** Whether a `cancelSubagent` was just sent for this session and hasn't been confirmed by a
   * poll showing `state !== 'running'` yet -- shows "Sending interrupt…" immediately on the
   * keypress rather than only once the cancellation actually lands, mirroring the TUI's
   * `_note_subagent_interrupt_requested` optimistic notice. */
  interruptPending: boolean;
  allThinkingExpanded: boolean;
  onToggleToolCallExpanded(callId: string): void;
}

const ELLIPSIS_FRAMES = ['', '.', '..', '...'];
const ELLIPSIS_INTERVAL_MS = 400;

/** Cycles through `ELLIPSIS_FRAMES` every `ELLIPSIS_INTERVAL_MS` while `active`; the empty frame
 * otherwise -- the "still working" status line's spinner. */
function useEllipsisPhase(active: boolean): string {
  const [frameIndex, setFrameIndex] = useState(0);
  useEffect(() => {
    if (!active) {
      return;
    }
    const timer = setInterval(
      () => setFrameIndex((prev) => (prev + 1) % ELLIPSIS_FRAMES.length),
      ELLIPSIS_INTERVAL_MS
    );
    return () => clearInterval(timer);
  }, [active]);
  return active ? (ELLIPSIS_FRAMES[frameIndex] ?? '') : '';
}

function statusNoticeText(
  state: 'running' | 'finished' | undefined,
  aborted: boolean,
  interruptPending: boolean,
  workingEllipsis: string
): string {
  if (state === undefined) {
    return 'Loading…';
  }
  if (state === 'running') {
    return interruptPending ? 'Sending interrupt…' : `Subagent is still working${workingEllipsis}`;
  }
  return aborted ? 'Subagent interrupted.' : 'Subagent task complete.';
}

/**
 * The subagents panel's transcript pane (`#subagent-history`) -- reuses `HistoryView` wholesale
 * against `entries` (already converted from `SubagentTranscriptUpdateMessage.entries` by
 * `subagentTranscriptEntries()`) rather than a second render path, so every completeness fix the
 * root history's own renderer has (a `tool_use` message's own commentary, a `thinking` message's
 * `reasoning_details` fallback -- both actually fixed at the source, `klorb.server.
 * update_mapping.build_session_replay`, since this and `sessionReplay` share that one builder)
 * applies here for free. Own pin-to-bottom tracking (`usePinnedScroll`), independent of the root
 * `#history` view's, and a trailing status line reporting one of the four states a subagent's
 * turn can be in -- see docs/specs/vscode-plugin.md's "Subagents panel" section.
 */
export default function SubagentTranscriptView({
  entries,
  state,
  aborted,
  allThinkingExpanded,
  interruptPending,
  onToggleToolCallExpanded,
}: SubagentTranscriptViewProps): JSX.Element {
  const { containerRef, scrollToBottomIfPinned } = usePinnedScroll<HTMLDivElement>();
  const workingEllipsis = useEllipsisPhase(state === 'running' && !interruptPending);

  useEffect(() => {
    scrollToBottomIfPinned();
  }, [entries, state, scrollToBottomIfPinned]);

  return (
    <div id="subagent-history-wrapper">
      <HistoryView
        entries={entries}
        historyRef={containerRef}
        allThinkingExpanded={allThinkingExpanded}
        onToggleToolCallExpanded={onToggleToolCallExpanded}
        onRestartServer={() => {
          /* A subagent's transcript never carries a 'serverError' entry -- its turn always runs
           * against the same live server connection as the root session's, and a lost server
           * fails the root's own turn first. */
        }}
      />
      <div id="subagent-history-status">
        {statusNoticeText(state, aborted, interruptPending, workingEllipsis)}
      </div>
    </div>
  );
}
