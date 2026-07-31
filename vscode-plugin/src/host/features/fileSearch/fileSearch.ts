// © Copyright 2026 Aaron Kimball
import ignore, { type Ignore } from 'ignore';
import type * as vscode from 'vscode';

import { errorMessage, type LogFn } from 'host/features/acp';

/** Upper bound on how many workspace files a full scan will enumerate, so a pathological
 * monorepo can't make the `@`-mention file finder scan (or hold in memory) an unbounded number
 * of paths. */
const MAX_WORKSPACE_FILES = 20000;

/** How long `watch()` waits after the most recent filesystem event before applying it, so a
 * burst of events (e.g. `npm install`, a branch checkout) collapses into one update instead of
 * one per event. */
const WATCH_DEBOUNCE_MS = 400;

/** The slice of the real `vscode` API `FileSearch` needs, injected so this module only ever
 * imports `vscode`'s *types*, never its runtime value (mirrors `host/editorIntegration.ts`'s own
 * `EditorIntegrationVsCode`). `findFiles`'/`createFileSystemWatcher`'s glob is matched relative
 * to `root`; the real implementation (built in `extension.ts`) wraps it in a
 * `vscode.RelativePattern`. */
export interface FileSearchVsCode {
  /** The first open workspace folder's root, or `undefined` when none is open -- mirrors
   * `extension.ts`'s own single-root convention (`sessionCwd()`). */
  workspaceRoot(): vscode.Uri | undefined;
  findFiles(root: vscode.Uri, glob: string, maxResults: number): Thenable<vscode.Uri[]>;
  readFile(uri: vscode.Uri): Thenable<Uint8Array>;
  relativePath(uri: vscode.Uri): string;
  /** `ignoreChangeEvents` is the only knob `watch()` needs: a plain file's content doesn't
   * change whether it's *in* the list, but a `.gitignore`'s content changes the filter itself. */
  createFileSystemWatcher(
    root: vscode.Uri,
    glob: string,
    ignoreChangeEvents: boolean
  ): vscode.FileSystemWatcher;
}

/** A full scan's result: the filtered file list `listWorkspaceFiles()` returns, plus the
 * `Ignore` matcher that produced it -- `watch()` keeps the matcher around so it can classify a
 * single newly-created file without re-reading every `.gitignore` in the workspace again. */
interface ScanResult {
  files: string[];
  filter: Ignore;
}

function toPosix(relPath: string): string {
  return relPath.replace(/\\/g, '/');
}

function dirOf(relPath: string): string {
  const idx = relPath.lastIndexOf('/');
  return idx === -1 ? '' : relPath.slice(0, idx);
}

/** Rewrites one `.gitignore` line so a single flattened `ignore()` instance can enforce every
 * nested `.gitignore` in the workspace at once: a pattern from a `.gitignore` at `dir` only
 * applies within that subtree, so a non-root pattern is prefixed with `dir` (and, unless the
 * pattern is itself anchored -- a leading `/` or a `/` before its last character -- widened with
 * `**` so it still matches at any depth under `dir`, mirroring git's own unanchored-pattern
 * semantics). Returns `undefined` for a blank or `#`-comment line. */
function scopeGitignoreLine(rawLine: string, dir: string): string | undefined {
  const line = rawLine.replace(/\r$/, '');
  if (line.trim().length === 0 || line.trimStart().startsWith('#')) {
    return undefined;
  }
  if (dir === '') {
    return line;
  }
  const negated = line.startsWith('!');
  const body = negated ? line.slice(1) : line;
  const anchored = body.startsWith('/');
  const stem = anchored ? body.slice(1) : body;
  const anchoredMidway = stem.slice(0, -1).includes('/');
  const scoped = anchored || anchoredMidway ? `${dir}/${stem}` : `${dir}/**/${stem}`;
  return negated ? `!${scoped}` : scoped;
}

/**
 * Enumerates workspace files for the prompt input's `@`-mention file finder (see
 * docs/specs/vscode-plugin.md's file-finder section). `vscode.workspace.findFiles()` already
 * honors VS Code's own `files.exclude` setting, but -- unlike the Explorer/search UI -- not
 * `.gitignore`, so a scan additionally loads every `.gitignore` in the workspace (flattened into
 * one `ignore()` matcher via `scopeGitignoreLine`) and filters `findFiles`' results against it,
 * without needing a `git` binary on `PATH`. `watch()` keeps a list fresh after that initial scan
 * by applying each create/delete event as an incremental update against the cached list and
 * matcher, rather than re-scanning the whole workspace on every change.
 */
export class FileSearch {
  public constructor(
    private readonly _vs: FileSearchVsCode,
    private readonly _log: LogFn = (message: string) => console.error(message)
  ) {}

  /** Lists every non-`.gitignore`d workspace file as a POSIX-style path relative to the
   * workspace root, capped at `MAX_WORKSPACE_FILES` entries. Empty when no folder is open. */
  public async listWorkspaceFiles(): Promise<string[]> {
    const root = this._vs.workspaceRoot();
    if (root === undefined) {
      return [];
    }
    return (await this._scan(root)).files;
  }

  /**
   * Watches the workspace for changes that can affect `listWorkspaceFiles()`'s result, calling
   * `onChanged` with the updated file list -- debounced by `WATCH_DEBOUNCE_MS` so a burst of
   * events resolves to one call, not one per event. Runs its own initial scan immediately on
   * call (there's nothing to debounce yet) and calls `onChanged` once that lands; a no-op
   * (disposing cleanly, never calling `onChanged`) when no workspace folder is open.
   *
   * A created or deleted plain file is applied as a single add/remove against the cached list
   * and `Ignore` matcher from the last scan -- no filesystem access beyond classifying that one
   * path. A `.gitignore` being created, edited, or deleted is different: its *content* changes
   * the matcher itself, and the `ignore` package has no way to un-apply previously added
   * patterns, so that case falls back to a full re-scan (rare next to ordinary file churn).
   * Ordinary file edits are deliberately not watched at all: they can't change which paths
   * exist, only their contents, and the file finder only ever shows paths.
   */
  public watch(onChanged: (files: string[]) => void): vscode.Disposable {
    const root = this._vs.workspaceRoot();
    if (root === undefined) {
      return { dispose: () => undefined };
    }

    let files = new Set<string>();
    let filter: Ignore | undefined;
    let needsFullRescan = false;
    let createdSinceFlush: string[] = [];
    let deletedSinceFlush: string[] = [];
    let timer: ReturnType<typeof setTimeout> | undefined;
    // Chains each flush onto the previous one instead of letting `doFlush()` run concurrently
    // with itself: a `.gitignore`-triggered rescan can take longer than `WATCH_DEBOUNCE_MS`, and
    // without this a second flush's timer could fire while the first is still awaiting its scan,
    // corrupting the shared `files`/`filter` state both would read and write.
    let flushChain: Promise<void> = Promise.resolve();

    const scheduleFlush = (): void => {
      if (timer !== undefined) {
        clearTimeout(timer);
      }
      timer = setTimeout(() => {
        timer = undefined;
        flushChain = flushChain.then(doFlush);
      }, WATCH_DEBOUNCE_MS);
    };

    const doFlush = async (): Promise<void> => {
      const rescan = needsFullRescan;
      const created = createdSinceFlush;
      const deleted = deletedSinceFlush;
      needsFullRescan = false;
      createdSinceFlush = [];
      deletedSinceFlush = [];
      if (rescan) {
        const result = await this._scan(root);
        files = new Set(result.files);
        filter = result.filter;
      } else {
        // `filter` is always set by the time a flush not forced by `ready` runs: the initial
        // scan below populates it before any watcher event can itself trigger a flush (its own
        // `scheduleFlush()` calls only happen from handlers registered after that scan starts,
        // and every flush path (this one included) is only reachable once `ready` has resolved.
        for (const relPath of created) {
          if (filter !== undefined && !filter.ignores(relPath)) {
            files.add(relPath);
          }
        }
        for (const relPath of deleted) {
          files.delete(relPath);
        }
      }
      onChanged(Array.from(files));
    };

    // The initial scan is not debounced -- there's no burst to coalesce yet -- but every flush
    // (including one a watcher event schedules before this lands) awaits it first, so a delta
    // never applies against the still-empty pre-scan state.
    const ready = this._scan(root).then((result) => {
      files = new Set(result.files);
      filter = result.filter;
      onChanged(Array.from(files));
    });
    const flushAfterReady = (): void => {
      void ready.then(scheduleFlush);
    };

    const filesWatcher = this._vs.createFileSystemWatcher(root, '**/*', true);
    const gitignoreWatcher = this._vs.createFileSystemWatcher(root, '**/.gitignore', false);
    const subscriptions = [
      filesWatcher,
      filesWatcher.onDidCreate((uri) => {
        createdSinceFlush.push(toPosix(this._vs.relativePath(uri)));
        flushAfterReady();
      }),
      filesWatcher.onDidDelete((uri) => {
        deletedSinceFlush.push(toPosix(this._vs.relativePath(uri)));
        flushAfterReady();
      }),
      gitignoreWatcher,
      gitignoreWatcher.onDidCreate(() => {
        needsFullRescan = true;
        flushAfterReady();
      }),
      gitignoreWatcher.onDidChange(() => {
        needsFullRescan = true;
        flushAfterReady();
      }),
      gitignoreWatcher.onDidDelete(() => {
        needsFullRescan = true;
        flushAfterReady();
      }),
    ];
    this._log('klorb: file finder: watching workspace for file/.gitignore create/delete events');
    return {
      dispose: () => {
        if (timer !== undefined) {
          clearTimeout(timer);
        }
        subscriptions.forEach((subscription) => subscription.dispose());
      },
    };
  }

  private async _scan(root: vscode.Uri): Promise<ScanResult> {
    const [uris, filter] = await Promise.all([
      this._vs.findFiles(root, '**/*', MAX_WORKSPACE_FILES),
      this._loadGitignoreFilter(root),
    ]);
    const relPaths = uris.map((uri) => toPosix(this._vs.relativePath(uri)));
    const files = relPaths.filter((relPath) => !filter.ignores(relPath));
    this._log(
      `klorb: file finder: indexed ${files.length} of ${relPaths.length} workspace files ` +
        `(${relPaths.length - files.length} .gitignore'd)`
    );
    return { files, filter };
  }

  private async _loadGitignoreFilter(root: vscode.Uri): Promise<Ignore> {
    const filter = ignore();
    let gitignoreUris: vscode.Uri[];
    try {
      gitignoreUris = await this._vs.findFiles(root, '**/.gitignore', MAX_WORKSPACE_FILES);
    } catch (err) {
      this._log(`klorb: file finder: could not enumerate .gitignore files: ${errorMessage(err)}`);
      return filter;
    }
    this._log(`klorb: file finder: loading ${gitignoreUris.length} .gitignore file(s)`);
    for (const uri of gitignoreUris) {
      const dir = dirOf(toPosix(this._vs.relativePath(uri)));
      try {
        const bytes = await this._vs.readFile(uri);
        const lines = Buffer.from(bytes)
          .toString('utf8')
          .split('\n')
          .map((line) => scopeGitignoreLine(line, dir))
          .filter((line): line is string => line !== undefined);
        filter.add(lines);
      } catch (err) {
        this._log(`klorb: file finder: could not read ${uri.toString()}: ${errorMessage(err)}`);
      }
    }
    return filter;
  }
}
