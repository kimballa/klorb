// © Copyright 2026 Aaron Kimball
import * as vscode from 'vscode';

import { errorMessage, type AcpConnection } from './acpConnection';
import type { SessionUpdateListener } from './klorbAcpClient';
import { parseWebviewMessage, type HostMessage } from './shared/webviewMessages';

/**
 * Backs the "Klorb session" side panel: a scrolling history of prompts, streamed thinking,
 * and streamed markdown responses above a multi-line prompt input (see src/webview/App.tsx,
 * mounted by src/webview/main.tsx and bundled to out/webview/main.js). The webview and the
 * host exchange the typed messages defined in src/shared/webviewMessages.ts: the webview
 * posts user intent (`submitPrompt`, `cancelTurn`), and this provider drives the shared
 * `AcpConnection` and posts turn lifecycle + streamed text back. As the connection's
 * `SessionUpdateListener`, it forwards `agent_message_chunk`/`agent_thought_chunk` text into
 * the panel.
 */
export class KlorbSessionViewProvider
  implements vscode.WebviewViewProvider, SessionUpdateListener
{
  public static readonly viewType = 'klorb.sessionView';

  private _view: vscode.WebviewView | undefined;
  private _connection: AcpConnection | undefined;

  public constructor(private readonly _extensionUri: vscode.Uri) {}

  /** Wires the connection this provider drives. Set once during activation — the provider
   * and connection reference each other (the provider is the connection's listener), so one
   * side has to be attached after construction. */
  public setConnection(connection: AcpConnection): void {
    this._connection = connection;
  }

  public resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken,
  ): void {
    this._view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this._extensionUri],
    };
    webviewView.webview.html = this._getHtml(webviewView.webview);
    webviewView.webview.onDidReceiveMessage((message: unknown) => {
      void this._handleMessage(message);
    });
  }

  public onAgentText(text: string): void {
    this.postHostMessage({ type: 'agentChunk', text });
  }

  public onThoughtText(text: string): void {
    this.postHostMessage({ type: 'thoughtChunk', text });
  }

  /** Posts a typed host→webview message. A no-op when the view hasn't been resolved yet. */
  public postHostMessage(message: HostMessage): void {
    void this._view?.webview.postMessage(message);
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
      case 'cancelTurn':
        this._connection?.cancel();
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
    this.postHostMessage({ type: 'turnStarted' });
    try {
      const stopReason = await connection.prompt(text);
      this.postHostMessage({ type: 'turnEnded', stopReason });
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
      vscode.Uri.joinPath(this._extensionUri, 'out', 'webview', 'main.js'),
    );
    const styleUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this._extensionUri, 'media', 'main.css'),
    );
    const nonce = getNonce();
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy"
    content="default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';">
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
