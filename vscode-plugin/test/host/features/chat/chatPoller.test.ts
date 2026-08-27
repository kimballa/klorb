// © Copyright 2026 Aaron Kimball
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AcpConnection } from 'host/features/acp';
import { ChatPoller } from 'host/features/chat';

/** A minimal `AcpConnection` double exposing only the surface `ChatPoller` actually calls:
 * `chatCapable` and `extMethod`. */
function fakeConnection(overrides: Partial<AcpConnection> = {}): AcpConnection {
  return {
    chatCapable: true,
    extMethod: vi.fn().mockResolvedValue({ messages: [], unreadCount: 0, unreadMentionCount: 0 }),
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

describe('ChatPoller', () => {
  it('does not poll history until the chat room is selected', async () => {
    const connection = fakeConnection();
    const poller = new ChatPoller(connection, vi.fn(), vi.fn());

    await vi.advanceTimersByTimeAsync(5000);
    expect(connection.extMethod).not.toHaveBeenCalled();

    poller.setSelected(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(connection.extMethod).toHaveBeenCalledWith('_klorb/chatHistory', {});
  });

  it('stops polling once the chat room is deselected', async () => {
    const connection = fakeConnection();
    const poller = new ChatPoller(connection, vi.fn(), vi.fn());
    poller.setSelected(true);
    await vi.advanceTimersByTimeAsync(0);
    const callsWhileSelected = (connection.extMethod as ReturnType<typeof vi.fn>).mock.calls.length;

    poller.setSelected(false);
    await vi.advanceTimersByTimeAsync(10000);
    expect(connection.extMethod).toHaveBeenCalledTimes(callsWhileSelected);
  });

  it('never polls at all when the server is not chat-capable', async () => {
    const connection = fakeConnection({ chatCapable: false });
    const poller = new ChatPoller(connection, vi.fn(), vi.fn());

    poller.setSelected(true);
    await vi.advanceTimersByTimeAsync(10000);

    expect(connection.extMethod).not.toHaveBeenCalled();
  });

  it('resync starts the poll once chatCapable flips true after setSelected(true)', async () => {
    const connection = fakeConnection({ chatCapable: false });
    const poller = new ChatPoller(connection, vi.fn(), vi.fn());

    poller.setSelected(true);
    await vi.advanceTimersByTimeAsync(5000);
    expect(connection.extMethod).not.toHaveBeenCalled();

    (connection as unknown as { chatCapable: boolean }).chatCapable = true;
    poller.resync();
    await vi.advanceTimersByTimeAsync(0);

    expect(connection.extMethod).toHaveBeenCalledWith('_klorb/chatHistory', {});
  });

  it('pushes a parsed history snapshot to the onHistory listener', async () => {
    const update = {
      messages: [{ seq: 1, senderId: 'root-1', timestamp: '2026-01-01T00:00:00', body: 'hi' }],
      unreadCount: 1,
      unreadMentionCount: 0,
    };
    const connection = fakeConnection({ extMethod: vi.fn().mockResolvedValue(update) });
    const onHistory = vi.fn();
    const poller = new ChatPoller(connection, onHistory, vi.fn());

    poller.setSelected(true);
    await vi.advanceTimersByTimeAsync(0);

    expect(onHistory).toHaveBeenCalledWith(update);
  });

  it('postMessage calls the chatPost ext method with the given text', async () => {
    const connection = fakeConnection();
    const poller = new ChatPoller(connection, vi.fn(), vi.fn());

    await poller.postMessage('hey @explorer-1.1');

    expect(connection.extMethod).toHaveBeenCalledWith('_klorb/chatPost', {
      text: 'hey @explorer-1.1',
    });
  });

  it('postMessage polls history immediately when the chat room is selected', async () => {
    const connection = fakeConnection();
    const poller = new ChatPoller(connection, vi.fn(), vi.fn());
    poller.setSelected(true);
    await vi.advanceTimersByTimeAsync(0);
    (connection.extMethod as ReturnType<typeof vi.fn>).mockClear();

    await poller.postMessage('hi');
    await vi.advanceTimersByTimeAsync(0);

    expect(connection.extMethod).toHaveBeenCalledWith('_klorb/chatHistory', {});
  });

  it('dispose stops the timer', async () => {
    const connection = fakeConnection();
    const poller = new ChatPoller(connection, vi.fn(), vi.fn());
    poller.setSelected(true);
    await vi.advanceTimersByTimeAsync(0);
    (connection.extMethod as ReturnType<typeof vi.fn>).mockClear();

    poller.dispose();
    await vi.advanceTimersByTimeAsync(10000);

    expect(connection.extMethod).not.toHaveBeenCalled();
  });
});
