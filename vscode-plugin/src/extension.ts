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
  setPermissionModeCommand,
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

/** `ExtensionContext.globalState` key holding the last-used session id for workspace `cwd` --
 * scoped per-`cwd` (not a single global key) so a multi-root/multi-window setup remembers each
 * workspace's own latest session independently. Read at activation (when `klorb.
 * resumeLatestSession` is enabled) to decide what `AcpConnection.start()`'s `resumeSessionId`
 * should be; written after every successful `session/new`/`session/load` so it always reflects
 * the session actually live in this workspace. */
function lastSessionIdKey(cwd: string): string {
  return `klorb.lastSessionId:${cwd}`;
}

function readLastSessionId(context: vscode.ExtensionContext, cwd: string): string | undefined {
  return context.globalState.get<string>(lastSessionIdKey(cwd));
}

async function rememberLastSessionId(
  context: vscode.ExtensionContext,
  cwd: string,
  sessionId: string | undefined
): Promise<void> {
  if (sessionId === undefined) {
    return;
  }
  await context.globalState.update(lastSessionIdKey(cwd), sessionId);
}

export function activate(context: vscode.ExtensionContext): void {
  const outputChannel = vscode.window.createOutputChannel('Klorb');
  context.subscriptions.push(outputChannel);
  const log = (message: string): void => outputChannel.appendLine(message);

  const serverProcess = new KlorbServerProcess();
  const editorIntegration = new EditorIntegration(realEditorIntegrationVsCode());
  context.subscriptions.push(editorIntegration);
  const provider = new KlorbSessionViewProvider(context.extensionUri, editorIntegration, log);
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
    sessionControls,
    log
  );
  context.subscriptions.push({ dispose: () => workspaceTrustBridge.dispose() });
  context.subscriptions.push({ dispose: () => connection.stop() });

  const startConnection = async (): Promise<void> => {
    try {
      const cwd = sessionCwd();
      const resumeLatestSession = vscode.workspace
        .getConfiguration('klorb')
        .get<boolean>('resumeLatestSession', true);
      const resumeSessionId = resumeLatestSession ? readLastSessionId(context, cwd) : undefined;
      await connection.start(await readServerOptions(apiKeyManager), cwd, resumeSessionId);
      await rememberLastSessionId(context, cwd, connection.sessionId);
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
      const cwd = sessionCwd();
      void connection
        .newSession(cwd)
        .then(() => {
          provider.postHostMessage({ type: 'sessionReset' });
          return rememberLastSessionId(context, cwd, connection.sessionId);
        })
        .catch((err: unknown) => {
          void vscode.window.showErrorMessage(`Klorb: ${errorMessage(err)}`);
        });
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('klorb.browseSessions', () => {
      void browseSessionsCommand(connection, context);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('klorb.toggleTaskPanel', () => {
      provider.postHostMessage({ type: 'toggleTaskPanel' });
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
    vscode.commands.registerCommand('klorb.setPermissionMode', () =>
      setPermissionModeCommand(sessionControls, commandsVsCode)
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

/** `klorb.browseSessions`: fetches this workspace's saved sessions (`session/list`), shows them
 * in a native QuickPick (the currently-live one marked `current`), and loads whichever one the
 * user picks (`session/load`) -- backing both the command palette entry and the webview's
 * stopwatch ("Session history") icon (see `KlorbSessionViewProvider._handleMessage`'s
 * `listRecentSessions` case). No `sessionReset` is posted here: unlike `klorb.newSession` (which
 * has nothing to show until the next turn), a successful load is followed by the server's own
 * `_klorb/sessionReplay` notification (`KlorbSessionViewProvider.onSessionReplay`), which
 * replaces the history once it arrives -- posting `sessionReset` here too would risk clearing
 * that replay if it arrived first, defeating the stale-while-revalidate display docs/specs/
 * session-persistence.md describes. */
async function browseSessionsCommand(
  connection: AcpConnection,
  context: vscode.ExtensionContext
): Promise<void> {
  if (!connection.isReady) {
    void vscode.window.showErrorMessage('Klorb: server connection is not ready.');
    return;
  }
  const cwd = sessionCwd();
  let sessions: { id: string; title: string | null }[];
  try {
    sessions = await connection.listSessions(cwd);
  } catch (err) {
    void vscode.window.showErrorMessage(
      `Klorb: could not list saved sessions (${errorMessage(err)}).`
    );
    return;
  }
  if (sessions.length === 0) {
    void vscode.window.showInformationMessage('Klorb: no saved sessions for this workspace.');
    return;
  }
  const items = sessions.map((session) => ({
    label: session.title ?? session.id,
    description: session.id === connection.sessionId ? 'current' : undefined,
    value: session.id,
  }));
  const picked = await vscode.window.showQuickPick(items, { placeHolder: 'Load session' });
  if (picked === undefined || picked.value === connection.sessionId) {
    return;
  }
  try {
    await connection.loadSession(cwd, picked.value);
    await rememberLastSessionId(context, cwd, picked.value);
  } catch (err) {
    void vscode.window.showErrorMessage(`Klorb: could not load session (${errorMessage(err)}).`);
  }
}

export function deactivate(): void {}
