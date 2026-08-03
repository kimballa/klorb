// © Copyright 2026 Aaron Kimball
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AcpConnection } from 'host/features/acp';
import { SubagentPoller } from 'host/features/subagents';

/** A minimal `AcpConnection` double exposing only the surface `SubagentPoller` actually calls
 * (`subagentsCapable`, `extMethod`) -- casting a plain object rather than constructing a real
 * `AcpConnection` (which needs a live server process) keeps this a focused unit test of the
 * poller's own timer/gating logic. */
function fakeConnection(overrides: Partial<AcpConnection> = {}): AcpConnection {
  return {
    subagentsCapable: true,
    extMethod: vi.fn().mockResolvedValue({ nodes: [] }),
    ...overrides,
  } as unknown as AcpConnection;
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('SubagentPoller', () => {
  it('does not poll the tree until the panel becomes visible', async () => {
    const connection = fakeConnection();
    const poller = new SubagentPoller(connection, vi.fn(), vi.fn(), vi.fn());

    await vi.advanceTimersByTimeAsync(5000);
    expect(connection.extMethod).not.toHaveBeenCalled();

    poller.setPanelVisible(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(connection.extMethod).toHaveBeenCalledWith('_klorb/subagentTree', {});
  });

  it('stops polling the tree once the panel is hidden', async () => {
    const connection = fakeConnection();
    const poller = new SubagentPoller(connection, vi.fn(), vi.fn(), vi.fn());

    poller.setPanelVisible(true);
    await vi.advanceTimersByTimeAsync(0);
    const callsWhileVisible = (connection.extMethod as ReturnType<typeof vi.fn>).mock.calls.length;

    poller.setPanelVisible(false);
    await vi.advanceTimersByTimeAsync(10000);
    expect(connection.extMethod).toHaveBeenCalledTimes(callsWhileVisible);
  });

  it('never polls at all when the server is not subagents-capable', async () => {
    const connection = fakeConnection({ subagentsCapable: false });
    const poller = new SubagentPoller(connection, vi.fn(), vi.fn(), vi.fn());

    poller.setPanelVisible(true);
    poller.selectSubagent('subagent-1');
    await vi.advanceTimersByTimeAsync(10000);

    expect(connection.extMethod).not.toHaveBeenCalled();
  });

  it('resync starts the tree poll once subagentsCapable flips true after setPanelVisible(true)', async () => {
    // Regression test: a `setPanelVisible(true)` that arrives before the ACP connection's
    // `initialize()` handshake resolves (a startup race the webview's own mount-effect resync
    // message can trigger against a persisted `subagentsPanelVisible: true`) used to leave the
    // poller permanently idle -- nothing ever re-checked `subagentsCapable` afterwards.
    const connection = fakeConnection({ subagentsCapable: false });
    const poller = new SubagentPoller(connection, vi.fn(), vi.fn(), vi.fn());

    poller.setPanelVisible(true);
    await vi.advanceTimersByTimeAsync(5000);
    expect(connection.extMethod).not.toHaveBeenCalled();

    (connection as unknown as { subagentsCapable: boolean }).subagentsCapable = true;
    poller.resync();
    await vi.advanceTimersByTimeAsync(0);

    expect(connection.extMethod).toHaveBeenCalledWith('_klorb/subagentTree', {});
  });

  it('pushes a parsed tree snapshot to the onTree listener', async () => {
    const nodes = [
      {
        id: 'root-1',
        parentId: null,
        address: '1',
        title: null,
        role: 'operator',
        state: null,
        aborted: false,
        model: 'anthropic/claude-sonnet-5',
        thinkingEnabled: false,
        thinkingEffort: 'medium',
        usedTokens: 0,
        maxTokens: null,
        outputTokens: 0,
      },
    ];
    const connection = fakeConnection({
      extMethod: vi.fn().mockResolvedValue({ nodes }),
    });
    const onTree = vi.fn();
    const poller = new SubagentPoller(connection, onTree, vi.fn(), vi.fn());

    poller.setPanelVisible(true);
    await vi.advanceTimersByTimeAsync(0);

    expect(onTree).toHaveBeenCalledWith(nodes);
  });

  it('only polls the transcript once a real subagent id is selected', async () => {
    const connection = fakeConnection();
    const poller = new SubagentPoller(connection, vi.fn(), vi.fn(), vi.fn());
    poller.setPanelVisible(true);
    await vi.advanceTimersByTimeAsync(0);
    (connection.extMethod as ReturnType<typeof vi.fn>).mockClear();

    poller.selectSubagent(null);
    await vi.advanceTimersByTimeAsync(5000);
    expect(connection.extMethod).not.toHaveBeenCalledWith(
      '_klorb/subagentTranscript',
      expect.anything()
    );

    poller.selectSubagent('subagent-1');
    await vi.advanceTimersByTimeAsync(0);
    expect(connection.extMethod).toHaveBeenCalledWith('_klorb/subagentTranscript', {
      subagentId: 'subagent-1',
    });
  });

  it('pushes a parsed transcript snapshot to the onTranscript listener', async () => {
    const connection = fakeConnection({
      extMethod: vi.fn().mockImplementation((method: string) => {
        if (method === '_klorb/subagentTranscript') {
          return Promise.resolve({
            entries: [{ kind: 'response', text: 'found it', streaming: false }],
            state: 'finished',
            aborted: false,
          });
        }
        return Promise.resolve({ nodes: [] });
      }),
    });
    const onTranscript = vi.fn();
    const poller = new SubagentPoller(connection, vi.fn(), onTranscript, vi.fn());

    poller.setPanelVisible(true);
    poller.selectSubagent('subagent-1');
    await vi.advanceTimersByTimeAsync(0);

    expect(onTranscript).toHaveBeenCalledWith({
      sessionId: 'subagent-1',
      entries: [{ kind: 'response', text: 'found it', streaming: false }],
      state: 'finished',
      aborted: false,
    });
  });

  it('stops the transcript poll once the panel is hidden, even with a subagent still selected', async () => {
    const connection = fakeConnection();
    const poller = new SubagentPoller(connection, vi.fn(), vi.fn(), vi.fn());
    poller.setPanelVisible(true);
    poller.selectSubagent('subagent-1');
    await vi.advanceTimersByTimeAsync(0);

    poller.setPanelVisible(false);
    (connection.extMethod as ReturnType<typeof vi.fn>).mockClear();
    await vi.advanceTimersByTimeAsync(5000);

    expect(connection.extMethod).not.toHaveBeenCalled();
  });

  it('cancelSubagent calls the subagentCancel ext method with the given id', async () => {
    const connection = fakeConnection();
    const poller = new SubagentPoller(connection, vi.fn(), vi.fn(), vi.fn());

    await poller.cancelSubagent('subagent-1');

    expect(connection.extMethod).toHaveBeenCalledWith('_klorb/subagentCancel', {
      subagentId: 'subagent-1',
    });
  });

  it('dispose stops both timers', async () => {
    const connection = fakeConnection();
    const poller = new SubagentPoller(connection, vi.fn(), vi.fn(), vi.fn());
    poller.setPanelVisible(true);
    poller.selectSubagent('subagent-1');
    await vi.advanceTimersByTimeAsync(0);
    (connection.extMethod as ReturnType<typeof vi.fn>).mockClear();

    poller.dispose();
    await vi.advanceTimersByTimeAsync(10000);

    expect(connection.extMethod).not.toHaveBeenCalled();
  });
});
