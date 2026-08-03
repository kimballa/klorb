// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import { PromptHistory, type PromptHistoryVsCode } from 'host/promptHistory';

function mockVsCode(initialMaxItems: number = 100): PromptHistoryVsCode & {
  store: Record<string, unknown>;
  maxItems: number;
  fireConfigChange: () => void;
} {
  const store: Record<string, unknown> = {};
  let configListener: (() => void) | undefined;
  const state = {
    store,
    maxItems: initialMaxItems,
    globalState: {
      get<T>(key: string): T | undefined {
        return store[key] as T | undefined;
      },
      update(key: string, value: unknown): Thenable<void> {
        store[key] = value;
        return Promise.resolve();
      },
    },
    onDidChangeConfiguration: (listener: () => void) => {
      configListener = listener;
      return { dispose: () => undefined };
    },
    getConfiguration: (_section: string) => ({
      get<T>(_key: string, _defaultValue: T): T {
        return state.maxItems as T;
      },
    }),
    fireConfigChange: () => configListener?.(),
  };
  return state;
}

describe('PromptHistory', () => {
  it('returns [] for a workspace with no prior history', () => {
    const vscode = mockVsCode();
    const history = new PromptHistory(vscode, 100);

    expect(history.loadForWorkspace('/home/user/project')).toEqual([]);
  });

  it('appends and retrieves entries for a workspace', () => {
    const vscode = mockVsCode();
    const history = new PromptHistory(vscode, 100);

    history.append('/home/user/project', 'first prompt');
    history.append('/home/user/project', 'second prompt');

    expect(history.loadForWorkspace('/home/user/project')).toEqual([
      'first prompt',
      'second prompt',
    ]);
  });

  it('keeps histories for different workspaces independent', () => {
    const vscode = mockVsCode();
    const history = new PromptHistory(vscode, 100);

    history.append('/home/user/project-a', 'prompt A');
    history.append('/home/user/project-b', 'prompt B');

    expect(history.loadForWorkspace('/home/user/project-a')).toEqual(['prompt A']);
    expect(history.loadForWorkspace('/home/user/project-b')).toEqual(['prompt B']);
  });

  it('trims oldest entries when exceeding maxItems', () => {
    const vscode = mockVsCode();
    const history = new PromptHistory(vscode, 3);

    history.append('/cwd', 'a');
    history.append('/cwd', 'b');
    history.append('/cwd', 'c');
    history.append('/cwd', 'd');

    expect(history.loadForWorkspace('/cwd')).toEqual(['b', 'c', 'd']);
  });

  it('does not trim when exactly at maxItems', () => {
    const vscode = mockVsCode();
    const history = new PromptHistory(vscode, 3);

    history.append('/cwd', 'a');
    history.append('/cwd', 'b');
    history.append('/cwd', 'c');

    expect(history.loadForWorkspace('/cwd')).toEqual(['a', 'b', 'c']);
  });

  it('re-reads maxItems on each append (config change takes effect)', () => {
    const vscode = mockVsCode(5);
    const history = new PromptHistory(vscode, 5);

    for (let i = 0; i < 5; i++) {
      history.append('/cwd', `item-${i}`);
    }
    expect(history.loadForWorkspace('/cwd')).toHaveLength(5);

    // Simulate config change.
    vscode.maxItems = 3;
    vscode.fireConfigChange();
    history.append('/cwd', 'item-5');

    expect(history.loadForWorkspace('/cwd')).toEqual(['item-3', 'item-4', 'item-5']);
  });

  it('persists to globalState under a workspace-scoped key', () => {
    const vscode = mockVsCode();
    const history = new PromptHistory(vscode, 100);

    history.append('/home/user/myproject', 'test');

    expect(vscode.store['klorb.promptHistory:/home/user/myproject']).toEqual(['test']);
  });
});
