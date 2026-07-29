// © Copyright 2026 Aaron Kimball
import * as vscode from 'vscode';

import type { EditorIntegration } from 'host/editorIntegration';
import {
  errorMessage,
  type AcpConnection,
  type LogFn,
  type SessionInfo,
  type SessionUpdateListener,
} from 'host/features/acp';
import type { SessionControls } from 'host/features/sessionControls';
import {
  parseWebviewMessage,
  type HostMessage,
  type PermissionAskMessage,
  type QuestionAskMessage,
  type SessionReplayEntry,
  type TaskListUpdateMessage,
  type ToolCallLimitAskMessage,
  type ToolCallStartedMessage,
  type ToolCallUpdatedMessage,
} from 'shared/webviewMessages';

/**
 * Backs the "Klorb session" side panel: a scrolling history of prompts, streamed thinking,
 * streamed markdown responses, and tool-call chips above a multi-line prompt input (see
 * src/webview/App.tsx, mounted by src/webview/main.tsx and bundled to out/webview/main.js).
 * The webview and the host exchange the typed messages defined in
 * src/shared/webviewMessages.ts: the webview posts user intent (`submitPrompt`, `cancelTurn`,
 * `openLocation`, `openDiff`, `permissionDecision`, `questionAnswer`), and this provider drives
 * the shared `AcpConnection` and posts turn lifecycle + streamed text + tool-call updates back.
 * As the connection's `SessionUpdateListener`, it forwards `agent_message_chunk`/
 * `agent_thought_chunk` text and `tool_call`/`tool_call_update` updates into the panel, posts
 * `permissionAsk`/`questionAsk` messages (guarding that an ask arriving while the view is hidden
 * surfaces a VS Code notification rather than sitting invisible), and routes `openLocation`/
 * `openDiff` to the shared `EditorIntegration`. `resolveWebviewView()` also re-posts any
 * interaction still awaiting an answer -- the live `KlorbAcpClient` behind `AcpConnection`
 * outlives a webview reload, so its pending interaction just needs to be shown again to the
 * fresh webview instance. A `webviewError` message (the webview's `ErrorBoundary` reporting an
 * uncaught render error) is logged via `_log`, so a webview-side crash lands in the same "Klorb"
 * output channel as everything else this extension logs, rather than only being visible in the
 * webview's own (easy-to-miss) JS console.
 */
export class KlorbSessionViewProvider implements vscode.WebviewViewProvider, SessionUpdateListener {
  public static readonly viewType = 'klorb.sessionView';

  private _view: vscode.WebviewView | undefined;
  private _connection: AcpConnection | undefined;
  private _sessionControls: SessionControls | undefined;

  public constructor(
    private readonly _extensionUri: vscode.Uri,
    private readonly _editorIntegration: EditorIntegration,
    private readonly _log: LogFn = (message: string) => console.error(message)
  ) {}

  /** Wires the connection this provider drives. Set once during activation — the provider
   * and connection reference each other (the provider is the connection's listener), so one
   * side has to be attached after construction. */
  public setConnection(connection: AcpConnection): void {
    this._connection = connection;
  }

  /** Wires the `SessionControls` this provider routes mode/title/usage pushes into, and whose
   * status snapshots it posts to the webview as `statusUpdate` -- set once during activation,
   * after the connection (which `SessionControls` itself wraps), for the same construction-
   * order reason as `setConnection()`. */
  public setSessionControls(sessionControls: SessionControls): void {
    this._sessionControls = sessionControls;
  }

  public resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ): void {
    this._view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this._extensionUri],
    };
    webviewView.webview.html = this._getHtml(webviewView.webview);
    webviewView.webview.onDidReceiveMessage((message: unknown) => {
      this._handleMessage(message).catch((err: unknown) => {
        // _handleMessage's own case bodies (_runTurn, EditorIntegration calls, ...) already
        // catch what they can meaningfully report; this is the backstop for anything that
        // still throws past them, so it lands in the "Klorb" output channel instead of an
        // anonymous unhandled-rejection warning with no klorb-specific context at all.
        this._log(`klorb: error handling webview message: ${errorMessage(err)}`);
      });
    });
    this._connection?.client?.repostPendingInteraction();
    this._sessionControls?.postSnapshot();
  }

  public onAgentText(text: string): void {
    this.postHostMessage({ type: 'agentChunk', text });
  }

  public onThoughtText(text: string): void {
    this.postHostMessage({ type: 'thoughtChunk', text });
  }

  public onToolCallStarted(message: ToolCallStartedMessage): void {
    this.postHostMessage(message);
  }

  public onToolCallUpdated(message: ToolCallUpdatedMessage): void {
    if (message.diff !== undefined) {
      this._editorIntegration.recordDiff(message.callId, {
        oldText: message.diff.oldText,
        newText: message.diff.newText,
      });
    }
    this.postHostMessage(message);
  }

  public onSessionInfo(info: SessionInfo): void {
    this._sessionControls?.applySessionInfo(info);
  }

  public onModeChanged(modeId: string): void {
    this._sessionControls?.applyModeChanged(modeId);
  }

  public onSessionTitleChanged(title: string | null): void {
    this._sessionControls?.applySessionTitleChanged(title);
  }

  public onUsageUpdate(usedTokens: number, maxTokens: number | null, outputTokens: number): void {
    this._sessionControls?.applyUsageUpdate(usedTokens, maxTokens, outputTokens);
  }

  public onTaskListUpdate(message: TaskListUpdateMessage): void {
    this.postHostMessage(message);
  }

  public onMessageQueued(text: string): void {
    this.postHostMessage({ type: 'messageQueued', text });
  }

  public onQueuedMessageSent(text: string): void {
    this.postHostMessage({ type: 'queuedMessageSent', text });
  }

  public onSessionReplay(entries: SessionReplayEntry[]): void {
    this.postHostMessage({ type: 'sessionReplay', entries });
  }

  public onSessionReset(): void {
    this.postHostMessage({ type: 'sessionReset' });
  }

  /** Posts a typed host→webview message. A no-op when the view hasn't been resolved yet. */
  public postHostMessage(message: HostMessage): void {
    void this._view?.webview.postMessage(message);
  }

  /** Posts a `permissionAsk` to the panel; if the Klorb view is hidden when it arrives, also
   * shows a VS Code notification with a "Show Klorb" action so an approval waiting on the user
   * can't sit invisible forever (see docs/specs/vscode-plugin.md's approval panel section). */
  public postPermissionAsk(message: PermissionAskMessage): void {
    this.postHostMessage(message);
    this._notifyIfHidden('Klorb needs your approval');
  }

  /** Posts a `questionAsk` to the panel; if the Klorb view is hidden when it arrives, also shows
   * a VS Code notification, mirroring `postPermissionAsk()`. */
  public postQuestionAsk(message: QuestionAskMessage): void {
    this.postHostMessage(message);
    this._notifyIfHidden('Klorb has a question for you');
  }

  /** Posts a `toolCallLimitAsk` to the panel; if the Klorb view is hidden when it arrives, also
   * shows a VS Code notification, mirroring `postPermissionAsk()`. */
  public postToolCallLimitAsk(message: ToolCallLimitAskMessage): void {
    this.postHostMessage(message);
    this._notifyIfHidden('Klorb needs your decision: Tool call limit reached');
  }

  /** Shows a "Show Klorb" notification if the view is currently hidden -- shared by
   * `postPermissionAsk()`/`postQuestionAsk()`/`postToolCallLimitAsk()` so no kind of
   * interaction can sit invisible forever while the auxiliary bar is closed. */
  private _notifyIfHidden(title: string): void {
    if (this._view !== undefined && !this._view.visible) {
      void vscode.window.showInformationMessage(title, 'Show Klorb').then((choice) => {
        if (choice === 'Show Klorb') {
          void vscode.commands.executeCommand('klorb.sessionView.focus');
        }
      });
    }
  }

  private async _handleMessage(message: unknown): Promise<void> {
    const parsed = parseWebviewMessage(message);
    if (parsed === undefined) {
      return;
    }
    switch (parsed.type) {
      case 'submitPrompt':
        await this._runTurn(parsed.text);
        break;
      case 'enqueueMessage':
        await this._enqueueMessage(parsed.text);
        break;
      case 'cancelTurn':
        this._connection?.cancel();
        break;
      case 'restartServer':
        await vscode.commands.executeCommand('klorb.restartServer');
        break;
      case 'openLocation':
        await this._editorIntegration.openLocation(parsed.path, parsed.line);
        break;
      case 'openDiff':
        await this._editorIntegration.openDiff(parsed.callId, parsed.path);
        break;
      case 'permissionDecision':
        this._connection?.client?.resolvePermissionDecision(
          parsed.requestId,
          'cancelled' in parsed
            ? { cancelled: true }
            : { optionId: parsed.optionId, otherText: parsed.otherText }
        );
        break;
      case 'questionAnswer':
        this._connection?.client?.resolveQuestionAnswer(
          parsed.requestId,
          'cancelled' in parsed
            ? { cancelled: true }
            : 'otherText' in parsed
              ? { otherText: parsed.otherText }
              : { selectedOptionIndex: parsed.selectedOptionIndex }
        );
        break;
      case 'toolCallLimitDecision':
        this._connection?.client?.resolveToolCallLimitDecision(
          parsed.requestId,
          'approved' in parsed ? { approved: true } : { cancelled: true }
        );
        break;
      case 'pickModel':
        await vscode.commands.executeCommand('klorb.selectModel');
        break;
      case 'pickThinking':
        await vscode.commands.executeCommand('klorb.setThinking');
        break;
      case 'cyclePermissionMode':
        await vscode.commands.executeCommand('klorb.cyclePermissionMode');
        break;
      case 'setPermissionMode':
        await vscode.commands.executeCommand('klorb.setPermissionMode');
        break;
      case 'showSessionStats':
        await vscode.commands.executeCommand('klorb.showSessionStats');
        break;
      case 'newSession':
        await vscode.commands.executeCommand('klorb.newSession');
        break;
      case 'listRecentSessions':
        await vscode.commands.executeCommand('klorb.browseSessions');
        break;
      case 'reloadSkills':
        await vscode.commands.executeCommand('klorb.reloadSkills');
        break;
      case 'webviewError':
        this._log(
          `klorb: webview crashed: ${parsed.message}${parsed.stack !== undefined ? `\n${parsed.stack}` : ''}`
        );
        break;
    }
  }

  private async _runTurn(text: string): Promise<void> {
    const connection = this._connection;
    if (connection === undefined || !connection.isReady) {
      this.postHostMessage({
        type: 'turnError',
        message:
          'klorb server connection is not ready — check the klorb.serverPath setting and ' +
          'run "Klorb: Restart Server".',
      });
      return;
    }
    const turnGeneration = connection.turnGeneration;
    this.postHostMessage({ type: 'turnStarted' });
    try {
      const stopReason = await connection.prompt(text);
      // A `newSession()`/`loadSession()` call while this turn was in flight interrupts it
      // (`AcpConnection._interruptInFlightTurn()`) and bumps `turnGeneration` -- when that's
      // what settled this `prompt()`, the result belongs to a session this provider has
      // already moved on from, so it must not post a `turnEnded` over the new session's view.
      if (connection.turnGeneration !== turnGeneration) {
        return;
      }
      this.postHostMessage({ type: 'turnEnded', stopReason });
    } catch (err) {
      if (connection.turnGeneration !== turnGeneration) {
        return;
      }
      // A rejection that also left the connection not-ready means the `klorb server` child
      // itself was lost (crashed, or killed/restarted) out from under this turn, not an
      // ordinary turn failure -- surface a distinct entry with a "Restart Server" action
      // instead of a plain error the user has no obvious next step for.
      if (!connection.isReady) {
        this.postHostMessage({ type: 'serverLost', message: errorMessage(err) });
      } else {
        this.postHostMessage({ type: 'turnError', message: errorMessage(err) });
      }
    }
  }

  /** Queues `text` into the currently in-flight turn (`_klorb/enqueueMessage`) -- called for a
   * `submitPrompt` the webview posted while a turn was already running and the connected
   * server advertised the capability (see `PromptInput`'s `enqueueMessageCapable` prop). A
   * capability-absent or not-ready connection surfaces a `turnError` rather than silently
   * dropping the message -- the webview only posts this when it believes the capability is
   * present, so reaching here otherwise means the connection state changed underneath it. */
  private async _enqueueMessage(text: string): Promise<void> {
    const connection = this._connection;
    if (connection === undefined || !connection.isReady || !connection.enqueueMessageCapable) {
      this.postHostMessage({
        type: 'turnError',
        message: 'klorb server does not support queuing a message into the running turn.',
      });
      return;
    }
    try {
      await connection.enqueueMessage(text);
    } catch (err) {
      this.postHostMessage({ type: 'turnError', message: errorMessage(err) });
    }
  }

  /**
   * Regenerates the webview's HTML document (with a cache-busting query string on the script
   * URI) so a fresh `out/webview/main.js` build is picked up without reloading VS Code itself.
   * This only re-renders the webview: it re-invokes `_getHtml()` on the already-running
   * extension host, so it does not pick up a change to this file or extension.ts itself — that
   * requires a full "Developer: Reload Window" (or restarting VS Code) so the extension host
   * re-`require`s the updated `out/*.js`.
   */
  public restart(): void {
    if (this._view === undefined) {
      return;
    }
    this._view.webview.html = this._getHtml(this._view.webview);
  }

  private _getHtml(webview: vscode.Webview): string {
    const scriptUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this._extensionUri, 'out', 'webview', 'main.js')
    );
    const styleUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this._extensionUri, 'media', 'main.css')
    );
    // Copied from @vscode/codicons at build time (npm run copy:codicons, chained into
    // `compile`/`compile:prod`), not vendored/committed -- see docs/specs/vscode-plugin.md's
    // "Component library" section.
    const codiconUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this._extensionUri, 'out', 'media', 'codicon.css')
    );
    const nonce = getNonce();
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy"
    content="default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}'; connect-src ${webview.cspSource}; font-src ${webview.cspSource};">
  <link id="vscode-codicon-stylesheet" rel="stylesheet" href="${codiconUri}">
  <link rel="stylesheet" href="${styleUri}">
  <title>Klorb session</title>
</head>
<body>
  <div id="root"></div>
  <script nonce="${nonce}" src="${scriptUri}?v=${Date.now()}"></script>
</body>
</html>`;
  }
}

function getNonce(): string {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let nonce = '';
  for (let i = 0; i < 32; i++) {
    nonce += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return nonce;
}
