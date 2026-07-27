// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import type { VsCodeApi } from 'webview/components/VsCodeApiProvider';
import { readPersistedState } from 'webview/features/sessionState';

function fakeVsCode(state: unknown): VsCodeApi {
  return {
    postMessage: () => undefined,
    setState: () => undefined,
    getState: () => state,
  };
}

describe('readPersistedState', () => {
  it('returns an empty entries array when nothing is persisted yet', () => {
    expect(readPersistedState(fakeVsCode(undefined))).toEqual({ entries: [] });
  });

  it('returns an empty entries array when the persisted state is not an object at all', () => {
    expect(readPersistedState(fakeVsCode('corrupt'))).toEqual({ entries: [] });
  });

  it('passes through a well-formed entries array unchanged', () => {
    const entries = [{ kind: 'prompt', text: 'hi', streaming: false }];
    expect(readPersistedState(fakeVsCode({ entries }))).toEqual({ entries });
  });

  it('drops a bare non-object entry left over from a stale/incompatible build', () => {
    const result = readPersistedState(
      fakeVsCode({ entries: ['test?', { kind: 'prompt', text: 'hi', streaming: false }] })
    );

    expect(result.entries).toEqual([{ kind: 'prompt', text: 'hi', streaming: false }]);
  });

  it('falls back to an empty array when entries itself is not an array', () => {
    expect(readPersistedState(fakeVsCode({ entries: 'not an array' })).entries).toEqual([]);
  });

  it('leaves every other persisted field untouched', () => {
    const status = { model: 'gpt-5' };
    const taskList = {
      summary: { openCount: 1, closedCount: 0, blockedCount: 0, currentTaskId: null },
      tasks: [],
    };
    const result = readPersistedState(
      fakeVsCode({ entries: [], status, taskList, taskPanelVisible: false })
    );

    expect(result.status).toBe(status);
    expect(result.taskList).toBe(taskList);
    expect(result.taskPanelVisible).toBe(false);
  });
});
