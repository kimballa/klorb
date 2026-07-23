// © Copyright 2026 Aaron Kimball
import * as vscode from 'vscode';

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

export function activate(context: vscode.ExtensionContext): void {
  const server = new KlorbServerProcess();
  server.start(readServerOptions());
  context.subscriptions.push({ dispose: () => server.stop() });

  const provider = new KlorbSessionViewProvider(context.extensionUri, server);

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(KlorbSessionViewProvider.viewType, provider, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('klorb.restartSession', () => {
      provider.restart();
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('klorb.restartServer', () => {
      server.start(readServerOptions());
      vscode.window.showInformationMessage('Klorb server restarted.');
    }),
  );
}

export function deactivate(): void {}
