// © Copyright 2026 Aaron Kimball
import * as vscode from 'vscode';

import type { KlorbServerProcess } from './klorbServerProcess';

interface SubmitMessage {
  type: 'submit';
  text: string;
}

function isSubmitMessage(message: unknown): message is SubmitMessage {
  return (
    typeof message === 'object' &&
    message !== null &&
    (message as { type?: unknown }).type === 'submit' &&
    typeof (message as { text?: unknown }).text === 'string'
  );
}

/**
 * Backs the "Klorb session" side panel: a scrolling history of chat bubbles above a
 * multi-line prompt textbox (see src/webview/App.tsx, mounted by src/webview/main.tsx and
 * bundled to out/webview/main.js). Submitting the textbox posts a `{type: 'submit', text}`
 * message from the webview to `_handleMessage()`, which forwards `text` to the shared
 * `KlorbServerProcess` as a `greet` command (docs/specs/klorb-server.md) and posts the reply
 * back to the webview as `{type: 'reply', text}`.
 */
export class KlorbSessionViewProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = 'klorb.sessionView';

  private _view: vscode.WebviewView | undefined;

  public constructor(
    private readonly _extensionUri: vscode.Uri,
    private readonly _server: KlorbServerProcess,
  ) {}

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

  private async _handleMessage(message: unknown): Promise<void> {
    if (!isSubmitMessage(message)) {
      return;
    }
    const reply = await this._server.greet(message.text);
    const text =
      typeof reply.message === 'string'
        ? reply.message
        : String(reply.error ?? 'unrecognized reply from klorb server');
    this._view?.webview.postMessage({ type: 'reply', text });
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
