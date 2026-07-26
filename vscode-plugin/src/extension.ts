// © Copyright 2026 Aaron Kimball
import * as os from 'os';

import * as vscode from 'vscode';

import { ApiKeyManager, type ApiKeyVsCode } from 'host/apiKeyStorage';
import { EditorIntegration, type EditorIntegrationVsCode } from 'host/editorIntegration';
import { AcpConnection, errorMessage } from 'host/features/acp';
import {
  SessionControls,
  WorkspaceTrustBridge,
  cyclePermissionModeCommand,
  reloadSkillsCommand,
  selectModelCommand,
  setThinkingCommand,
  showSessionStatsCommand,
  type CommandsVsCode,
  type PickableItem,
  type WorkspaceTrustVsCode,
} from 'host/features/sessionControls';
import { KlorbServerProcess, type KlorbServerOptions } from 'host/klorbServerProcess';
import { KlorbSessionViewProvider } from 'host/klorbSessionViewProvider';

/** Answers the `_klorb/raiseToolCallLimit` ext method with a native modal warning -- a rare
 * safety interstitial, so it reads with appropriate weight as a blocking VS Code dialog rather
 * than another webview panel. */
async function raiseToolCallLimitModal(message: string): Promise<boolean> {
  const choice = await vscode.window.showWarningMessage(message, { modal: true }, 'Continue');
  return choice === 'Continue';
}

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

/** The real `vscode`-backed `ApiKeyVsCode` (see host/apiKeyStorage.ts's own doc comment). */
function realApiKeyVsCode(context: vscode.ExtensionContext): ApiKeyVsCode {
  return {
    secrets: context.secrets,
    showInputBox: (options) => vscode.window.showInputBox(options),
    showInformationMessage: (message, ...items) =>
      vscode.window.showInformationMessage(message, ...items),
  };
}

/** The real `vscode`-backed `WorkspaceTrustVsCode` (see
 * host/features/sessionControls/workspaceTrustBridge.ts's own doc comment). */
function realWorkspaceTrustVsCode(): WorkspaceTrustVsCode {
  return {
    isTrusted: () => vscode.workspace.isTrusted,
    onDidGrantWorkspaceTrust: (listener) => vscode.workspace.onDidGrantWorkspaceTrust(listener),
    showInformationMessage: (message, ...items) =>
      vscode.window.showInformationMessage(message, ...items),
  };
}

/** The real `vscode`-backed `CommandsVsCode` (see
 * host/features/sessionControls/commands.ts's own doc comment). */
function realCommandsVsCode(provider: KlorbSessionViewProvider): CommandsVsCode {
  return {
    showQuickPick<T>(items: PickableItem<T>[], options: { placeHolder: string }) {
      return vscode.window.showQuickPick(items, options);
    },
    showInformationMessage(message: string) {
      return vscode.window.showInformationMessage(message);
    },
    showErrorMessage(message: string) {
      return vscode.window.showErrorMessage(message);
    },
    postSessionStats(data) {
      provider.postHostMessage({ type: 'sessionStats', ...data });
    },
  };
}

async function readServerOptions(apiKeyManager: ApiKeyManager): Promise<KlorbServerOptions> {
  const config = vscode.workspace.getConfiguration('klorb');
  const command = config.get<string>('serverPath', 'klorb');
  const configPath = config.get<string>('configPath', '');
  const apiKey = await apiKeyManager.resolve();
  const env: NodeJS.ProcessEnv = { ...process.env };
  if (apiKey !== undefined) {
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
  const outputChannel = vscode.window.createOutputChannel('Klorb');
  context.subscriptions.push(outputChannel);
  const log = (message: string): void => outputChannel.appendLine(message);

  const serverProcess = new KlorbServerProcess();
  const editorIntegration = new EditorIntegration(realEditorIntegrationVsCode());
  context.subscriptions.push(editorIntegration);
  const provider = new KlorbSessionViewProvider(context.extensionUri, editorIntegration);
  const apiKeyManager = new ApiKeyManager(realApiKeyVsCode(context));
  const connection = new AcpConnection(
    serverProcess,
    provider,
    log,
    undefined,
    raiseToolCallLimitModal
  );
  const sessionControls = new SessionControls(
    connection,
    (status) => provider.postHostMessage({ type: 'statusUpdate', ...status }),
    log
  );
  provider.setConnection(connection);
  provider.setSessionControls(sessionControls);
  const workspaceTrustBridge = new WorkspaceTrustBridge(
    realWorkspaceTrustVsCode(),
    sessionControls
  );
  context.subscriptions.push({ dispose: () => workspaceTrustBridge.dispose() });
  context.subscriptions.push({ dispose: () => connection.stop() });

  const startConnection = async (): Promise<void> => {
    try {
      await connection.start(await readServerOptions(apiKeyManager), sessionCwd());
      void workspaceTrustBridge.offerIfNeeded();
    } catch (err) {
      const message = errorMessage(err);
      void vscode.window.showErrorMessage(`Klorb: ${message}`);
      provider.postHostMessage({ type: 'turnError', message });
    }
  };
  void startConnection();

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(KlorbSessionViewProvider.viewType, provider, {
      webviewOptions: { retainContextWhenHidden: true },
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('klorb.newSession', () => {
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
      void readServerOptions(apiKeyManager)
        .then((options) => connection.start(options, sessionCwd()))
        .then(() => {
          provider.postHostMessage({ type: 'sessionReset' });
          vscode.window.showInformationMessage('Klorb server restarted.');
          void workspaceTrustBridge.offerIfNeeded();
        })
        .catch((err: unknown) => {
          const message = errorMessage(err);
          void vscode.window.showErrorMessage(`Klorb: ${message}`);
          provider.postHostMessage({ type: 'turnError', message });
        });
    })
  );

  const commandsVsCode = realCommandsVsCode(provider);
  context.subscriptions.push(
    vscode.commands.registerCommand('klorb.selectModel', () =>
      selectModelCommand(sessionControls, commandsVsCode)
    ),
    vscode.commands.registerCommand('klorb.setThinking', () =>
      setThinkingCommand(sessionControls, commandsVsCode)
    ),
    vscode.commands.registerCommand('klorb.cyclePermissionMode', () =>
      cyclePermissionModeCommand(sessionControls, commandsVsCode)
    ),
    vscode.commands.registerCommand('klorb.showSessionStats', () =>
      showSessionStatsCommand(sessionControls, commandsVsCode)
    ),
    vscode.commands.registerCommand('klorb.reloadSkills', () =>
      reloadSkillsCommand(sessionControls, commandsVsCode)
    ),
    vscode.commands.registerCommand('klorb.setOpenRouterApiKey', () =>
      apiKeyManager.setApiKeyCommand()
    ),
    vscode.commands.registerCommand('klorb.clearOpenRouterApiKey', () =>
      apiKeyManager.clearApiKeyCommand()
    )
  );
}

export function deactivate(): void {}
