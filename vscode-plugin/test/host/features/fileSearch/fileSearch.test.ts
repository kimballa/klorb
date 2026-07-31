// © Copyright 2026 Aaron Kimball
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { FileSearch, type FileSearchVsCode } from 'host/features/fileSearch';

/** A minimal fake `vscode.Uri`-like value: just enough for `FileSearch`'s own use (round-
 * tripping through `relativePath`/`readFile`) without depending on the real `vscode` module --
 * mirrors `test/host/editorIntegration.test.ts`'s own `FakeUri`. */
interface FakeUri {
  relPath: string;
  toString(): string;
}

function fakeUri(relPath: string): FakeUri {
  return { relPath, toString: () => `file:///work/${relPath}` };
}

/** A minimal in-memory `FileSearchVsCode`: `fileContents` maps a workspace-relative path (every
 * `.gitignore` under test) to its text content; every other path in `allPaths` is a plain file
 * with no content lookup needed. `findFiles` supports exactly the two globs `FileSearch` itself
 * uses. */
function makeFakeVsCode(
  allPaths: string[],
  fileContents: Record<string, string>
): FileSearchVsCode {
  return {
    workspaceRoot: () => fakeUri('') as unknown as never,
    findFiles: (_root, glob) => {
      if (glob === '**/.gitignore') {
        return Promise.resolve(
          allPaths
            .filter((p) => p === '.gitignore' || p.endsWith('/.gitignore'))
            .map((p) => fakeUri(p) as unknown as never)
        );
      }
      return Promise.resolve(allPaths.map((p) => fakeUri(p) as unknown as never));
    },
    readFile: (uri) => {
      const relPath = (uri as unknown as FakeUri).relPath;
      const content = fileContents[relPath];
      if (content === undefined) {
        return Promise.reject(new Error(`no fake content for ${relPath}`));
      }
      return Promise.resolve(new TextEncoder().encode(content));
    },
    relativePath: (uri) => (uri as unknown as FakeUri).relPath,
    createFileSystemWatcher: () => makeFakeWatcher() as unknown as never,
  };
}

/** A minimal fake `vscode.FileSystemWatcher`: `fire*()` invoke whichever listener is currently
 * registered (or no-op if none is), and a registration's own `dispose()` actually clears the
 * listener -- so a test can verify `FileSearch.watch()`'s own disposal really stops callbacks,
 * not just that `dispose()` was called on something. */
/** A minimal fake `vscode.FileSystemWatcher`: `fire*(relPath)` invokes whichever listener is
 * currently registered with a `FakeUri` for `relPath` (or no-ops if none is registered), and a
 * registration's own `dispose()` actually clears the listener -- so a test can verify
 * `FileSearch.watch()`'s own disposal really stops callbacks, not just that `dispose()` was
 * called on something. */
interface FakeWatcher {
  disposed: boolean;
  onDidCreate(listener: (uri: FakeUri) => void): { dispose(): void };
  onDidChange(listener: (uri: FakeUri) => void): { dispose(): void };
  onDidDelete(listener: (uri: FakeUri) => void): { dispose(): void };
  dispose(): void;
  fireCreate(relPath: string): void;
  fireChange(relPath: string): void;
  fireDelete(relPath: string): void;
}

function makeFakeWatcher(): FakeWatcher {
  let createListener: ((uri: FakeUri) => void) | undefined;
  let changeListener: ((uri: FakeUri) => void) | undefined;
  let deleteListener: ((uri: FakeUri) => void) | undefined;
  const watcher: FakeWatcher = {
    disposed: false,
    onDidCreate: (listener) => {
      createListener = listener;
      return {
        dispose: () => {
          createListener = undefined;
        },
      };
    },
    onDidChange: (listener) => {
      changeListener = listener;
      return {
        dispose: () => {
          changeListener = undefined;
        },
      };
    },
    onDidDelete: (listener) => {
      deleteListener = listener;
      return {
        dispose: () => {
          deleteListener = undefined;
        },
      };
    },
    dispose: () => {
      watcher.disposed = true;
    },
    fireCreate: (relPath) => createListener?.(fakeUri(relPath)),
    fireChange: (relPath) => changeListener?.(fakeUri(relPath)),
    fireDelete: (relPath) => deleteListener?.(fakeUri(relPath)),
  };
  return watcher;
}

interface CapturedWatch {
  vs: FileSearchVsCode;
  files: FakeWatcher;
  gitignore: FakeWatcher;
  watcherCalls: { glob: string; ignoreChangeEvents: boolean }[];
  /** Every glob `findFiles()` was actually called with -- lets a test assert that an ordinary
   * create/delete was applied as an in-memory delta (no new entries here) versus a `.gitignore`
   * change, which must trigger a real rescan (two new entries: the all-files glob and the
   * `.gitignore` glob). */
  findFilesCalls: string[];
}

/** A `FileSearchVsCode` for `FileSearch.watch()` tests: reuses `makeFakeVsCode()`'s scan
 * behavior (so an initial `allPaths`/`fileContents` seed is honored) but hands back the same two
 * fake watcher instances every time `createFileSystemWatcher()` is called for the all-files glob
 * vs. the `.gitignore` glob, so a test can fire events on them with real paths and assert
 * `watch()`'s debounced reaction. */
function makeWatchVsCode(
  allPaths: string[] = [],
  fileContents: Record<string, string> = {}
): CapturedWatch {
  const files = makeFakeWatcher();
  const gitignore = makeFakeWatcher();
  const watcherCalls: { glob: string; ignoreChangeEvents: boolean }[] = [];
  const findFilesCalls: string[] = [];
  const base = makeFakeVsCode(allPaths, fileContents);
  const vs: FileSearchVsCode = {
    ...base,
    findFiles: (root, glob, maxResults) => {
      findFilesCalls.push(glob);
      return base.findFiles(root, glob, maxResults);
    },
    createFileSystemWatcher: (_root, glob, ignoreChangeEvents) => {
      watcherCalls.push({ glob, ignoreChangeEvents });
      return (glob === '**/*' ? files : gitignore) as unknown as never;
    },
  };
  return { vs, files, gitignore, watcherCalls, findFilesCalls };
}

describe('FileSearch.listWorkspaceFiles', () => {
  it('returns every file when there is no .gitignore', async () => {
    const vs = makeFakeVsCode(['src/App.tsx', 'README.md'], {});
    const fileSearch = new FileSearch(vs);

    expect(await fileSearch.listWorkspaceFiles()).toEqual(['src/App.tsx', 'README.md']);
  });

  it('filters out files matched by the root .gitignore', async () => {
    const vs = makeFakeVsCode(
      ['.gitignore', 'src/App.tsx', 'node_modules/pkg/index.js', 'dist/out.js'],
      { '.gitignore': 'node_modules/\ndist/\n' }
    );
    const fileSearch = new FileSearch(vs);

    expect(await fileSearch.listWorkspaceFiles()).toEqual(['.gitignore', 'src/App.tsx']);
  });

  it('scopes a nested .gitignore to its own subtree', async () => {
    const vs = makeFakeVsCode(
      ['pkg/.gitignore', 'pkg/build/out.js', 'pkg/src/App.tsx', 'other/build/keep.js'],
      { 'pkg/.gitignore': 'build/\n' }
    );
    const fileSearch = new FileSearch(vs);

    const files = await fileSearch.listWorkspaceFiles();

    expect(files).toContain('other/build/keep.js');
    expect(files).not.toContain('pkg/build/out.js');
    expect(files).toContain('pkg/src/App.tsx');
  });

  it('ignores blank lines and comments in a .gitignore', async () => {
    const vs = makeFakeVsCode(['.gitignore', 'keep.txt', 'drop.log'], {
      '.gitignore': '\n# comment\n*.log\n',
    });
    const fileSearch = new FileSearch(vs);

    expect(await fileSearch.listWorkspaceFiles()).toEqual(['.gitignore', 'keep.txt']);
  });

  it('honors a negated re-include pattern', async () => {
    const vs = makeFakeVsCode(['.gitignore', 'logs/keep.log', 'logs/drop.log'], {
      '.gitignore': '*.log\n!logs/keep.log\n',
    });
    const fileSearch = new FileSearch(vs);

    expect(await fileSearch.listWorkspaceFiles()).toEqual(['.gitignore', 'logs/keep.log']);
  });

  it('returns an empty list when no workspace folder is open', async () => {
    const vs: FileSearchVsCode = {
      workspaceRoot: () => undefined,
      findFiles: () => Promise.resolve([]),
      readFile: () => Promise.reject(new Error('should not be called')),
      relativePath: () => '',
      createFileSystemWatcher: () => {
        throw new Error('should not be called');
      },
    };
    const fileSearch = new FileSearch(vs);

    expect(await fileSearch.listWorkspaceFiles()).toEqual([]);
  });

  it('logs and continues when a .gitignore cannot be read', async () => {
    const vs = makeFakeVsCode(['.gitignore', 'src/App.tsx'], {});
    const logs: string[] = [];
    const fileSearch = new FileSearch(vs, (message) => logs.push(message));

    const files = await fileSearch.listWorkspaceFiles();

    expect(files).toEqual(['.gitignore', 'src/App.tsx']);
    expect(logs.some((line) => line.includes('.gitignore'))).toBe(true);
  });
});

describe('FileSearch.watch', () => {
  // Mirrors fileSearch.ts's own (unexported) WATCH_DEBOUNCE_MS.
  const DEBOUNCE_MS = 400;

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('watches **/* ignoring content changes, and **/.gitignore not ignoring them', () => {
    const { vs, watcherCalls } = makeWatchVsCode();
    const fileSearch = new FileSearch(vs);

    fileSearch.watch(() => undefined);

    expect(watcherCalls).toEqual([
      { glob: '**/*', ignoreChangeEvents: true },
      { glob: '**/.gitignore', ignoreChangeEvents: false },
    ]);
  });

  it('calls onChanged with the initial scan as soon as it lands', async () => {
    const { vs } = makeWatchVsCode(['src/App.tsx', 'README.md']);
    const fileSearch = new FileSearch(vs);
    const onChanged = vi.fn();

    fileSearch.watch(onChanged);
    await vi.advanceTimersByTimeAsync(0);

    expect(onChanged).toHaveBeenCalledExactlyOnceWith(['src/App.tsx', 'README.md']);
  });

  it('adds a created file to the list, debounced, without a full filesystem rescan', async () => {
    const { vs, files, findFilesCalls } = makeWatchVsCode(['README.md']);
    const fileSearch = new FileSearch(vs);
    const onChanged = vi.fn();
    fileSearch.watch(onChanged);
    await vi.advanceTimersByTimeAsync(0);
    const scansAfterInitial = findFilesCalls.length;
    onChanged.mockClear();

    files.fireCreate('src/App.tsx');
    await vi.advanceTimersByTimeAsync(0);
    expect(onChanged).not.toHaveBeenCalled(); // still debouncing

    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    expect(onChanged).toHaveBeenCalledExactlyOnceWith(['README.md', 'src/App.tsx']);
    expect(findFilesCalls).toHaveLength(scansAfterInitial); // no rescan
  });

  it('removes a deleted file from the list without a full filesystem rescan', async () => {
    const { vs, files, findFilesCalls } = makeWatchVsCode(['README.md', 'src/App.tsx']);
    const fileSearch = new FileSearch(vs);
    const onChanged = vi.fn();
    fileSearch.watch(onChanged);
    await vi.advanceTimersByTimeAsync(0);
    const scansAfterInitial = findFilesCalls.length;
    onChanged.mockClear();

    files.fireDelete('src/App.tsx');
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);

    expect(onChanged).toHaveBeenCalledExactlyOnceWith(['README.md']);
    expect(findFilesCalls).toHaveLength(scansAfterInitial);
  });

  it('does not add a created file a .gitignore already excludes', async () => {
    const { vs, files } = makeWatchVsCode(['README.md', '.gitignore'], { '.gitignore': '*.log\n' });
    const fileSearch = new FileSearch(vs);
    const onChanged = vi.fn();
    fileSearch.watch(onChanged);
    await vi.advanceTimersByTimeAsync(0);
    onChanged.mockClear();

    files.fireCreate('debug.log');
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);

    expect(onChanged).toHaveBeenCalledExactlyOnceWith(['README.md', '.gitignore']);
  });

  it('a .gitignore change triggers a full rescan instead of an incremental delta', async () => {
    const { vs, gitignore, findFilesCalls } = makeWatchVsCode(['README.md', '.gitignore'], {
      '.gitignore': '',
    });
    const fileSearch = new FileSearch(vs);
    const onChanged = vi.fn();
    fileSearch.watch(onChanged);
    await vi.advanceTimersByTimeAsync(0);
    const scansAfterInitial = findFilesCalls.length;
    onChanged.mockClear();

    gitignore.fireChange('.gitignore');
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);

    expect(findFilesCalls.length).toBeGreaterThan(scansAfterInitial);
    expect(onChanged).toHaveBeenCalledExactlyOnceWith(['README.md', '.gitignore']);
  });

  it('collapses a burst of create/delete events into a single debounced call', async () => {
    const { vs, files } = makeWatchVsCode(['README.md']);
    const fileSearch = new FileSearch(vs);
    const onChanged = vi.fn();
    fileSearch.watch(onChanged);
    await vi.advanceTimersByTimeAsync(0);
    onChanged.mockClear();

    files.fireCreate('a.ts');
    await vi.advanceTimersByTimeAsync(200);
    files.fireCreate('b.ts');
    await vi.advanceTimersByTimeAsync(200);
    files.fireDelete('a.ts');
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);

    expect(onChanged).toHaveBeenCalledExactlyOnceWith(['README.md', 'b.ts']);
  });

  it('disposing stops further callbacks and disposes both watchers', async () => {
    const { vs, files, gitignore } = makeWatchVsCode(['README.md']);
    const fileSearch = new FileSearch(vs);
    const onChanged = vi.fn();
    const subscription = fileSearch.watch(onChanged);
    await vi.advanceTimersByTimeAsync(0);
    onChanged.mockClear();

    subscription.dispose();

    expect(files.disposed).toBe(true);
    expect(gitignore.disposed).toBe(true);

    files.fireCreate('a.ts');
    await vi.advanceTimersByTimeAsync(1000);
    expect(onChanged).not.toHaveBeenCalled();
  });

  it('is a no-op that disposes cleanly when no workspace folder is open', () => {
    const vs: FileSearchVsCode = {
      workspaceRoot: () => undefined,
      findFiles: () => Promise.resolve([]),
      readFile: () => Promise.reject(new Error('not used')),
      relativePath: () => '',
      createFileSystemWatcher: () => {
        throw new Error('should not create a watcher with no workspace folder open');
      },
    };
    const fileSearch = new FileSearch(vs);

    const subscription = fileSearch.watch(() => undefined);

    expect(() => subscription.dispose()).not.toThrow();
  });
});
