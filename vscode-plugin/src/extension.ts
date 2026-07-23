// © Copyright 2026 Aaron Kimball
import * as vscode from 'vscode';

import { KlorbSessionViewProvider } from './klorbSessionViewProvider';

export function activate(context: vscode.ExtensionContext): void {
  const provider = new KlorbSessionViewProvider(context.extensionUri);

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
}

export function deactivate(): void {}
