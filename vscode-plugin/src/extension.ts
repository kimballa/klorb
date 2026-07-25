// © Copyright 2026 Aaron Kimball
import * as os from 'os';
import * as vscode from 'vscode';

import { AcpConnection, errorMessage } from './acpConnection';
import { KlorbServerProcess, type KlorbServerOptions } from './klorbServerProcess';
import { KlorbSessionViewProvider } from './klorbSessionViewProvider';

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
  const provider = new KlorbSessionViewProvider(context.extensionUri);
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
    }),
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
    }),
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
    }),
  );
}

export function deactivate(): void {}
