// © Copyright 2026 Aaron Kimball
import * as vscode from 'vscode';

/**
 * Backs the "Klorb session" side panel: a scrolling history of static entries above a
 * multi-line prompt textbox. Today the webview only appends what the user typed to its own
 * history (see src/webview/main.ts, bundled to out/webview/main.js); it does not yet talk to
 * `klorb server` (docs/specs/klorb-server.md) or any other klorb process.
 */
export class KlorbSessionViewProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = 'klorb.sessionView';

  private _view: vscode.WebviewView | undefined;

  public constructor(private readonly _extensionUri: vscode.Uri) {}

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
  <div class="title">Klorb session</div>
  <div id="history"></div>
  <div class="input-row">
    <textarea id="prompt-input" rows="2"
      placeholder="Message Klorb... (Enter to send, Shift+Enter for a newline)"></textarea>
    <button id="submit-button">Send</button>
  </div>
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
