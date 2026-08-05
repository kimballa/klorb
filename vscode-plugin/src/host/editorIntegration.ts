// © Copyright 2026 Aaron Kimball
import * as path from 'path';

import type * as vscode from 'vscode';

/** The scheme `EditorIntegration.openDiff`'s virtual before/after documents are served under. */
export const KLORB_DIFF_SCHEME = 'klorb-diff';

/** One tool call's diff content, as needed to reconstruct a before/after editor diff -- the
 * same `oldText`/`newText` pair `ToolCallUpdatedMessage.diff` carries (see
 * shared/webviewMessages.ts), kept separately here since `EditorIntegration` only needs this
 * much of the flattened message. */
export interface DiffPayload {
  oldText: string | null;
  newText: string;
}

/**
 * The slice of the real `vscode` API `EditorIntegration` needs, split out (like
 * `klorbServerProcess.ts`'s `SpawnFn`) so tests can inject a fake instead of a real `vscode`
 * module -- this file itself only ever imports `vscode`'s *types*, never its runtime value, so
 * it loads cleanly outside a running VS Code extension host (e.g. under `vitest`). The real
 * implementation is built in `extension.ts`, the one place that already imports real `vscode`
 * values for wiring the rest of the extension together.
 */
export interface EditorIntegrationVsCode {
  fileUri(path: string): vscode.Uri;
  parseUri(value: string): vscode.Uri;
  openTextDocument(uri: vscode.Uri): Thenable<vscode.TextDocument>;
  showTextDocument(document: vscode.TextDocument): Thenable<vscode.TextEditor>;
  /** Moves `editor`'s cursor/selection/viewport to `line` (1-indexed, matching klorb's own
   * convention -- see klorb.tools.util.diff_lines.DiffLine). */
  revealLine(editor: vscode.TextEditor, line: number): void;
  showWarningMessage(message: string): void;
  registerDiffContentProvider(
    scheme: string,
    provider: vscode.TextDocumentContentProvider
  ): vscode.Disposable;
  openDiffEditor(oldUri: vscode.Uri, newUri: vscode.Uri, title: string): Thenable<unknown>;
}

/** Serves the reconstructed before/after contents `EditorIntegration.openDiff` diffs against,
 * registered under the `klorb-diff:` scheme. The reassembled text is a hunk-context view, not
 * necessarily the whole file (see docs/adrs/persist-diff-hunks-in-edit-result.md) -- an elided
 * view, not the literal file contents. */
class KlorbDiffContentProvider implements vscode.TextDocumentContentProvider {
  private readonly _contentByUri = new Map<string, string>();

  public set(uri: vscode.Uri, content: string): void {
    this._contentByUri.set(uri.toString(), content);
  }

  public provideTextDocumentContent(uri: vscode.Uri): string {
    return this._contentByUri.get(uri.toString()) ?? '';
  }
}

/**
 * Bridges tool-call editor links (`openLocation`/`openDiff` webview messages, see
 * src/webview/features/history/components/ToolCallChip.tsx) to real VS Code editor
 * integration: opening a file at a line, or a real diff view built from the hunk-reassembled
 * before/after text the server sent. Keeps one bounded map of diff payloads keyed by `callId`,
 * populated by `KlorbSessionViewProvider` as `tool_call_update` diffs arrive, since the diff
 * text itself isn't retained anywhere else once flattened into the webview message.
 */
export class EditorIntegration {
  private static readonly MAX_DIFFS_TRACKED = 50;

  private readonly _diffsByCallId = new Map<string, DiffPayload>();
  private readonly _contentProvider = new KlorbDiffContentProvider();
  private readonly _vscode: EditorIntegrationVsCode;
  private readonly _registration: vscode.Disposable;

  public constructor(vscodeApi: EditorIntegrationVsCode) {
    this._vscode = vscodeApi;
    this._registration = vscodeApi.registerDiffContentProvider(
      KLORB_DIFF_SCHEME,
      this._contentProvider
    );
  }

  public dispose(): void {
    this._registration.dispose();
  }

  /** Records a diff payload for a later `openDiff`, evicting the oldest entry once the bounded
   * map is full -- a long-running session shouldn't accumulate an unbounded history of every
   * edit's full reconstructed text. */
  public recordDiff(callId: string, payload: DiffPayload): void {
    if (
      !this._diffsByCallId.has(callId) &&
      this._diffsByCallId.size >= EditorIntegration.MAX_DIFFS_TRACKED
    ) {
      const oldestKey = this._diffsByCallId.keys().next().value;
      if (oldestKey !== undefined) {
        this._diffsByCallId.delete(oldestKey);
      }
    }
    this._diffsByCallId.set(callId, payload);
  }

  /** Opens `filePath` in the editor, at `line` (1-indexed) when given. A path that can't be
   * opened (deleted, renamed, outside the workspace) surfaces a warning rather than throwing. */
  public async openLocation(filePath: string, line?: number): Promise<void> {
    try {
      const document = await this._vscode.openTextDocument(this._vscode.fileUri(filePath));
      const editor = await this._vscode.showTextDocument(document);
      if (line !== undefined) {
        this._vscode.revealLine(editor, line);
      }
    } catch {
      this._vscode.showWarningMessage(`Klorb: could not open '${filePath}'`);
    }
  }

  /** Shows a real diff editor for `callId`'s recorded diff payload, titled
   * "<basename> (Klorb edit)". Warns instead of opening anything if no diff was recorded for
   * this call (e.g. the panel's history was restored from `vscode.getState()` across a window
   * reload, which doesn't persist this host-side map). */
  public async openDiff(callId: string, filePath: string): Promise<void> {
    const payload = this._diffsByCallId.get(callId);
    if (payload === undefined) {
      this._vscode.showWarningMessage(`Klorb: no stored diff for this tool call (${filePath})`);
      return;
    }
    const basename = path.basename(filePath);
    const oldUri = this._vscode.parseUri(`${KLORB_DIFF_SCHEME}:/${callId}/before/${basename}`);
    const newUri = this._vscode.parseUri(`${KLORB_DIFF_SCHEME}:/${callId}/after/${basename}`);
    this._contentProvider.set(oldUri, payload.oldText ?? '');
    this._contentProvider.set(newUri, payload.newText);
    await this._vscode.openDiffEditor(oldUri, newUri, `${basename} (Klorb edit)`);
  }
}
