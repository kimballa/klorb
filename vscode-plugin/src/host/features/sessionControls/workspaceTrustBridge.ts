// © Copyright 2026 Aaron Kimball
import type { SessionControls } from './sessionControls';

/** The subset of live `vscode` APIs `WorkspaceTrustBridge` needs, injected so this module
 * never imports the real `vscode` runtime value (see host/editorIntegration.ts's own doc
 * comment for why). */
export interface WorkspaceTrustVsCode {
  isTrusted(): boolean;
  onDidGrantWorkspaceTrust(listener: () => void): { dispose(): void };
  showInformationMessage(message: string, ...items: string[]): Thenable<string | undefined>;
}

/**
 * Offers, at most once per activation, to trust the session's workspace in Klorb -- see
 * docs/specs/vscode-plugin.md's workspace-trust-bridging section. If VS Code's own workspace
 * trust is granted but the klorb workspace isn't, `offerIfNeeded()` (called once after the
 * connection starts) shows the prompt immediately. If VS Code itself is in Restricted Mode,
 * the constructor's `onDidGrantWorkspaceTrust` subscription is what lets the offer happen
 * later, once VS Code's own trust is granted.
 */
export class WorkspaceTrustBridge {
  private _offered = false;
  private readonly _disposable: { dispose(): void };

  public constructor(
    private readonly _vs: WorkspaceTrustVsCode,
    private readonly _sessionControls: Pick<SessionControls, 'workspaceTrusted' | 'trustWorkspace'>
  ) {
    this._disposable = this._vs.onDidGrantWorkspaceTrust(() => {
      void this.offerIfNeeded();
    });
  }

  public dispose(): void {
    this._disposable.dispose();
  }

  public async offerIfNeeded(): Promise<void> {
    if (
      this._offered ||
      !this._vs.isTrusted() ||
      this._sessionControls.workspaceTrusted !== false
    ) {
      return;
    }
    this._offered = true;
    const choice = await this._vs.showInformationMessage(
      'Trust this workspace in Klorb? The agent gains read access beyond the workspace root ' +
        'and context files are loaded.',
      'Trust',
      'Not now'
    );
    if (choice === 'Trust') {
      await this._sessionControls.trustWorkspace();
    }
  }
}
