// © Copyright 2026 Aaron Kimball
import * as os from 'os';

import * as vscode from 'vscode';

import { EditorIntegration, type EditorIntegrationVsCode } from 'host/editorIntegration';
import { AcpConnection, errorMessage } from 'host/features/acp';
import { KlorbServerProcess, type KlorbServerOptions } from 'host/klorbServerProcess';
import { KlorbSessionViewProvider } from 'host/klorbSessionViewProvider';

/** The real `vscode`-backed `EditorIntegrationVsCode` -- the one place `EditorIntegration`'s
 * VS Code calls are actually made, so `editorIntegration.ts` itself never needs a real `vscode`
 * value import (see that module's own doc comment). */
function realEditorIntegrationVsCode(): EditorIntegrationVsCode {
  return {
    fileUri: (path: string) => vscode.Uri.file(path),
    parseUri: (value: string) => vscode.Uri.parse(value),
    openTextDocument: (uri) => vscode.workspace.openTextDocument(uri),
    showTextDocument: (document) => vscode.window.showTextDocument(document),
    revealLine: (editor, line) => {
      const position = new vscode.Position(Math.max(0, line - 1), 0);
      editor.selection = new vscode.Selection(position, position);
      editor.revealRange(new vscode.Range(position, position));
    },
    showWarningMessage: (message) => {
      void vscode.window.showWarningMessage(message);
    },
    registerDiffContentProvider: (scheme, provider) =>
      vscode.workspace.registerTextDocumentContentProvider(scheme, provider),
    openDiffEditor: (oldUri, newUri, title) =>
      vscode.commands.executeCommand('vscode.diff', oldUri, newUri, title),
  };
}

function readServerOptions(): KlorbServerOptions {
  const config = vscode.workspace.getConfiguration('klorb');
  const command = config.get<string>('serverPath', 'klorb');
  const apiKey = config.get<string>('openRouterApiKey', '');
  const configPath = config.get<string>('configPath', '');
  const env: NodeJS.ProcessEnv = { ...process.env };
  if (apiKey.length > 0) {
    env.OPENROUTER_API_KEY = apiKey;
  }
  return { command, env, configPath };
}

/** The session's working directory: the first workspace folder, or the home directory when
 * no folder is open (ACP requires an absolute cwd for `session/new`). */
function sessionCwd(): string {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? os.homedir();
}

export function activate(context: vscode.ExtensionContext): void {
  const serverProcess = new KlorbServerProcess();
  const editorIntegration = new EditorIntegration(realEditorIntegrationVsCode());
  context.subscriptions.push(editorIntegration);
  const provider = new KlorbSessionViewProvider(context.extensionUri, editorIntegration);
  const connection = new AcpConnection(serverProcess, provider);
  provider.setConnection(connection);
  context.subscriptions.push({ dispose: () => connection.stop() });

  const startConnection = (): void => {
    void connection.start(readServerOptions(), sessionCwd()).catch((err: unknown) => {
      const message = errorMessage(err);
      void vscode.window.showErrorMessage(`Klorb: ${message}`);
      provider.postHostMessage({ type: 'turnError', message });
    });
  };
  startConnection();

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(KlorbSessionViewProvider.viewType, provider, {
      webviewOptions: { retainContextWhenHidden: true },
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('klorb.restartSession', () => {
      provider.restart();
      void connection
        .newSession(sessionCwd())
        .then(() => provider.postHostMessage({ type: 'sessionReset' }))
        .catch((err: unknown) => {
          void vscode.window.showErrorMessage(`Klorb: ${errorMessage(err)}`);
        });
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('klorb.restartServer', () => {
      void connection
        .start(readServerOptions(), sessionCwd())
        .then(() => {
          provider.postHostMessage({ type: 'sessionReset' });
          vscode.window.showInformationMessage('Klorb server restarted.');
        })
        .catch((err: unknown) => {
          const message = errorMessage(err);
          void vscode.window.showErrorMessage(`Klorb: ${message}`);
          provider.postHostMessage({ type: 'turnError', message });
        });
    })
  );
}

export function deactivate(): void {}
