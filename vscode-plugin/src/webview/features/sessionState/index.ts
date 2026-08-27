// © Copyright 2026 Aaron Kimball
import type { StatusSnapshot } from 'webview/App';
import type { VsCodeApi } from 'webview/components/VsCodeApiProvider';
import {
  ensureEntryId,
  isHistoryEntry,
  type HistoryEntry,
  type PendingInteraction,
  type TaskListSnapshot,
} from 'webview/features/history';

/** The shape `vscode.setState()`/`vscode.getState()` persists across the webview's context
 * teardown/rebuild (see `App.tsx`'s own persistence `useEffect`). `selectedSubagentId` mirrors
 * `SelectSubagentMessage.sessionId` (`null` selects the root session); the subagent tree/
 * transcript/expansion state itself is deliberately *not* persisted -- it's refreshed from a
 * fresh poll within a couple of seconds of `App` re-mounting (see its own mount-resync effect),
 * so persisting a stale snapshot would only risk showing outdated data for that brief window. */
export interface SessionState {
  entries: HistoryEntry[];
  pendingInteraction?: PendingInteraction;
  status?: StatusSnapshot;
  taskList?: TaskListSnapshot;
  taskPanelVisible?: boolean;
  subagentsPanelVisible?: boolean;
  selectedSubagentId?: string | null;
  /** Whether the subagents panel's "Chat Room" row is selected, layered independently of
   * `selectedSubagentId`. */
  chatRoomSelected?: boolean;
}

/**
 * Reads and sanitizes `vscode.getState()` -- unlike the host↔webview message channel
 * (`parseHostMessage`/`parseWebviewMessage`), this persisted state has never been runtime-
 * validated, and it can outlive the extension version that wrote it (observed surviving a
 * `.vsix` reinstall): a stale `entries` array from an incompatible older build can otherwise
 * reach `App` unchecked and crash the whole webview the first time a reducer assumes every
 * entry is an object (e.g. `historyModel.ts`'s `finishStreaming()`, before it grew its own
 * defensive check). Only `entries` gets a real per-item check (`isHistoryEntry()`) since that's
 * the one field a reducer indexes into by shape; the others are trusted as before. Pulled out of
 * `main.tsx` (which self-executes `main()` at module scope, making it untestable in isolation)
 * so this sanitization logic can be unit-tested on its own.
 */
export function readPersistedState(vscode: VsCodeApi): SessionState {
  const raw = vscode.getState();
  if (typeof raw !== 'object' || raw === null) {
    return { entries: [] };
  }
  const state = raw as Partial<SessionState>;
  return {
    ...state,
    entries: Array.isArray(state.entries)
      ? state.entries.filter(isHistoryEntry).map(ensureEntryId)
      : [],
  };
}
