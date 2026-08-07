// © Copyright 2026 Aaron Kimball
import { errorMessage, type AcpConnection, type LogFn } from 'host/features/acp';
import {
  parseSubagentTranscriptResult,
  parseSubagentTreeResult,
  type SubagentNodeInfo,
  type SubagentTranscriptUpdateMessage,
} from 'shared/webviewMessages';

/** How often to re-fetch the subagent tree (`_klorb/subagentTree`) while the panel is visible --
 * a session tree changes only on `CreateSubagent`/`WaitForSubagent`/`MessageSubagent` calls or a
 * subagent's turn finishing in the background, none of which push a notification of their own
 * (see docs/specs/subagents.md's "Out of scope" section on waking an idle creator), so polling is
 * this feature's only option -- mirrors the TUI's own `_tick_subagents_panel` refresh, just over
 * the wire instead of in-process. */
const TREE_POLL_INTERVAL_MS = 2000;

/** How often to re-fetch the selected subagent's transcript (`_klorb/subagentTranscript`) while
 * one is selected -- a faster interval than the tree poll since a running subagent's transcript
 * is the thing actually on screen; mirrors the TUI's `_PANEL_TICK_INTERVAL_SECONDS` (0.6s)
 * closely enough without adding wire chatter for a background webview tab. */
const TRANSCRIPT_POLL_INTERVAL_MS = 1000;

export type SubagentTreeListener = (nodes: SubagentNodeInfo[]) => void;
export type SubagentTranscriptListener = (
  update: Omit<SubagentTranscriptUpdateMessage, 'type'>
) => void;

/**
 * Polls `_klorb/subagentTree`/`_klorb/subagentTranscript` on behalf of the webview's subagents
 * panel -- the webview never calls an ACP ext method directly (see
 * docs/adrs/vscode-webview-stays-acp-ignorant-behind-typed-messages.md), so this class is the
 * "keep pulling and push what changed" counterpart to how the server side pushes plan/task
 * updates unprompted. Two independent timers: the tree poll runs whenever the panel is visible
 * (`setPanelVisible`), the transcript poll additionally requires a non-root selection
 * (`selectSubagent`) -- both are no-ops (never start a timer at all) when the connected server
 * didn't advertise `_klorb/subagentTree` (`AcpConnection.subagentsCapable`), so an older server
 * isn't hit with a stream of `method not found` errors every interval.
 */
export class SubagentPoller {
  private readonly _connection: AcpConnection;
  private readonly _onTree: SubagentTreeListener;
  private readonly _onTranscript: SubagentTranscriptListener;
  private readonly _log: LogFn;
  private _treeTimer: ReturnType<typeof setInterval> | undefined;
  private _transcriptTimer: ReturnType<typeof setInterval> | undefined;
  private _panelVisible = false;
  private _selectedSubagentId: string | undefined;

  public constructor(
    connection: AcpConnection,
    onTree: SubagentTreeListener,
    onTranscript: SubagentTranscriptListener,
    log: LogFn
  ) {
    this._connection = connection;
    this._onTree = onTree;
    this._onTranscript = onTranscript;
    this._log = log;
  }

  /** The webview's subagents panel became visible or hidden -- starts/stops the tree poll (and,
   * since a transcript poll only ever runs alongside a visible panel, the transcript poll too). */
  public setPanelVisible(visible: boolean): void {
    this._panelVisible = visible;
    this._syncTreeTimer();
    this._syncTranscriptTimer();
  }

  /** The webview selected a row: `sessionId: null` selects the root (stopping the transcript
   * poll entirely), a real id starts polling that subagent's transcript. */
  public selectSubagent(sessionId: string | null): void {
    this._selectedSubagentId = sessionId ?? undefined;
    this._syncTranscriptTimer();
    if (
      this._selectedSubagentId !== undefined &&
      this._panelVisible &&
      this._connection.subagentsCapable
    ) {
      // Polls immediately rather than waiting for the first interval tick, so the transcript
      // view doesn't sit empty for up to TRANSCRIPT_POLL_INTERVAL_MS after a fresh selection.
      void this._pollTranscript();
    }
  }

  /** Cancels the selected subagent's turn (`_klorb/subagentCancel`). */
  public async cancelSubagent(sessionId: string): Promise<void> {
    await this._connection.extMethod('_klorb/subagentCancel', { subagentId: sessionId });
  }

  /** Sends `text` directly to `sessionId` (`_klorb/subagentPrompt`), bypassing the parent agent
   * entirely -- see docs/specs/vscode-plugin.md's "Subagents panel" section. Polls the transcript
   * immediately afterward, rather than waiting up to `TRANSCRIPT_POLL_INTERVAL_MS`, so the sent
   * message shows up without a visible delay -- mirrors `selectSubagent`'s own immediate poll. */
  public async sendPrompt(sessionId: string, text: string): Promise<void> {
    await this._connection.extMethod('_klorb/subagentPrompt', { subagentId: sessionId, text });
    if (this._selectedSubagentId === sessionId) {
      void this._pollTranscript();
    }
  }

  /** Re-evaluates both timers against `_connection.subagentsCapable`'s current value, without
   * changing this poller's own tracked visibility/selection state -- call once the connection's
   * `initialize()` handshake resolves. `setPanelVisible(true)` can arrive (from the webview's own
   * mount-effect resync, restoring a persisted `subagentsPanelVisible: true`) before that
   * handshake finishes, in which case `_syncTreeTimer()` sees `subagentsCapable` still `false`
   * and never starts the timer; nothing else re-checks it afterwards, so without this call the
   * poller stays silently idle -- panel flagged open, nothing polling -- until the user manually
   * toggles the panel closed and back open. */
  public resync(): void {
    this._syncTreeTimer();
    this._syncTranscriptTimer();
  }

  /** Stops both timers -- called when the connection itself is torn down. */
  public dispose(): void {
    this._panelVisible = false;
    this._selectedSubagentId = undefined;
    this._syncTreeTimer();
    this._syncTranscriptTimer();
  }

  private _syncTreeTimer(): void {
    const shouldPoll = this._panelVisible && this._connection.subagentsCapable;
    if (shouldPoll && this._treeTimer === undefined) {
      void this._pollTree();
      this._treeTimer = setInterval(() => void this._pollTree(), TREE_POLL_INTERVAL_MS);
    } else if (!shouldPoll && this._treeTimer !== undefined) {
      clearInterval(this._treeTimer);
      this._treeTimer = undefined;
    }
  }

  private _syncTranscriptTimer(): void {
    const shouldPoll =
      this._panelVisible &&
      this._connection.subagentsCapable &&
      this._selectedSubagentId !== undefined;
    if (shouldPoll && this._transcriptTimer === undefined) {
      this._transcriptTimer = setInterval(
        () => void this._pollTranscript(),
        TRANSCRIPT_POLL_INTERVAL_MS
      );
    } else if (!shouldPoll && this._transcriptTimer !== undefined) {
      clearInterval(this._transcriptTimer);
      this._transcriptTimer = undefined;
    }
  }

  private async _pollTree(): Promise<void> {
    try {
      const result = await this._connection.extMethod('_klorb/subagentTree', {});
      const nodes = parseSubagentTreeResult(result);
      if (nodes === undefined) {
        this._log(`klorb: malformed _klorb/subagentTree result: ${JSON.stringify(result)}`);
        return;
      }
      this._onTree(nodes);
    } catch (err) {
      this._log(`klorb: subagent tree poll failed: ${errorMessage(err)}`);
    }
  }

  private async _pollTranscript(): Promise<void> {
    const subagentId = this._selectedSubagentId;
    if (subagentId === undefined) {
      return;
    }
    try {
      const result = await this._connection.extMethod('_klorb/subagentTranscript', {
        subagentId,
      });
      // The selection may have moved on while this call was in flight -- drop a stale reply
      // rather than rendering it against the now-wrong transcript view.
      if (this._selectedSubagentId !== subagentId) {
        return;
      }
      const parsed = parseSubagentTranscriptResult(result);
      if (parsed === undefined) {
        this._log(`klorb: malformed _klorb/subagentTranscript result: ${JSON.stringify(result)}`);
        return;
      }
      this._onTranscript({ sessionId: subagentId, ...parsed });
    } catch (err) {
      this._log(`klorb: subagent transcript poll failed: ${errorMessage(err)}`);
    }
  }
}
