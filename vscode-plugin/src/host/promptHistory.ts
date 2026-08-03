// © Copyright 2026 Aaron Kimball

/** Testability shim for the VS Code APIs `PromptHistory` uses -- real code passes the concrete
 * `vscode.ExtensionContext` + event; tests pass a mock. */
export interface PromptHistoryVsCode {
  globalState: {
    get<T>(key: string): T | undefined;
    update(key: string, value: unknown): Thenable<void>;
  };
  onDidChangeConfiguration: (listener: () => void) => { dispose(): void };
  getConfiguration(section: string): { get<T>(key: string, defaultValue: T): T };
}

/** Persists per-workspace prompt input history in `globalState`, keyed by workspace cwd, so
 * up/down-arrow recall in the prompt textarea survives VS Code restarts. `maxItems` caps the
 * retained length (oldest entries roll off); it re-reads the `klorb.promptHistory.maxItems`
 * config value on each `append()` so a live config change takes effect without a restart. */
export class PromptHistory {
  private _maxItems: number;

  public constructor(
    private readonly _vscode: PromptHistoryVsCode,
    initialMaxItems: number
  ) {
    this._maxItems = initialMaxItems;
    this._vscode.onDidChangeConfiguration(() => {
      this._maxItems = this._vscode
        .getConfiguration('klorb')
        .get<number>('promptHistory.maxItems', 100);
    });
  }

  /** Returns the persisted prompt history for `cwd`, oldest-first. Returns `[]` when no
   * history exists yet for this workspace. */
  public loadForWorkspace(cwd: string): string[] {
    return this._vscode.globalState.get<string[]>(this._key(cwd)) ?? [];
  }

  /** Appends `text` to the persisted history for `cwd`, trimming the oldest entries when the
   * list exceeds `maxItems`. */
  public append(cwd: string, text: string): void {
    const entries = this.loadForWorkspace(cwd);
    entries.push(text);
    const trimmed =
      entries.length > this._maxItems ? entries.slice(entries.length - this._maxItems) : entries;
    void this._vscode.globalState.update(this._key(cwd), trimmed);
  }

  private _key(cwd: string): string {
    return `klorb.promptHistory:${cwd}`;
  }
}
