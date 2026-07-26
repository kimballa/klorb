// © Copyright 2026 Aaron Kimball
import type * as vscode from 'vscode';

/** The `SecretStorage` key the OpenRouter API key is stored under -- see
 * docs/specs/vscode-plugin.md's API-key storage section. */
export const OPENROUTER_API_KEY_SECRET_KEY = 'klorb.openRouterApiKey';

/** The subset of live `vscode` APIs `ApiKeyManager` needs, injected so this module never
 * imports the real `vscode` runtime value (see host/editorIntegration.ts's own doc comment
 * for why). `secrets` is `vscode.SecretStorage` directly, since that interface is already
 * minimal and type-only imports don't pull in the runtime module. */
export interface ApiKeyVsCode {
  secrets: vscode.SecretStorage;
  showInputBox(options: vscode.InputBoxOptions): Thenable<string | undefined>;
  showInformationMessage(message: string, ...items: string[]): Thenable<string | undefined>;
}

/**
 * Resolves and manages the OpenRouter API key, stored in `vscode.ExtensionContext.secrets`,
 * and the set/clear commands -- see docs/specs/vscode-plugin.md's API-key storage section.
 */
export class ApiKeyManager {
  public constructor(private readonly _vs: ApiKeyVsCode) {}

  /** The stored API key, or `undefined` (rather than an empty string) when none is set, so the
   * caller can leave the child's environment untouched and let an already-exported
   * `OPENROUTER_API_KEY` pass through unchanged. */
  public async resolve(): Promise<string | undefined> {
    const stored = await this._vs.secrets.get(OPENROUTER_API_KEY_SECRET_KEY);
    return stored !== undefined && stored.length > 0 ? stored : undefined;
  }

  /** `klorb.setOpenRouterApiKey`: prompts for a key and stores it; an empty submission
   * deletes the stored secret instead. */
  public async setApiKeyCommand(): Promise<void> {
    const value = await this._vs.showInputBox({
      prompt: 'OpenRouter API key',
      password: true,
      ignoreFocusOut: true,
    });
    if (value === undefined) {
      return;
    }
    if (value.length === 0) {
      await this._vs.secrets.delete(OPENROUTER_API_KEY_SECRET_KEY);
      await this._vs.showInformationMessage('Klorb: OpenRouter API key cleared.');
      return;
    }
    await this._vs.secrets.store(OPENROUTER_API_KEY_SECRET_KEY, value);
    await this._vs.showInformationMessage('Klorb: OpenRouter API key stored.');
  }

  /** `klorb.clearOpenRouterApiKey`: deletes the stored secret explicitly. */
  public async clearApiKeyCommand(): Promise<void> {
    await this._vs.secrets.delete(OPENROUTER_API_KEY_SECRET_KEY);
    await this._vs.showInformationMessage('Klorb: OpenRouter API key cleared.');
  }
}
