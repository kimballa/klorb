// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import type { HostMessage, SubagentNodeInfo } from 'shared/webviewMessages';
import {
  applyChatHistoryUpdate,
  chatDisplayName,
  chatNickname,
  chatRowMarker,
  findChatMentionSpans,
} from 'webview/features/chatRoom';

const ROOT_NODE: SubagentNodeInfo = {
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
  maxTokens: 128000,
  outputTokens: 0,
};

const CHILD_NODE: SubagentNodeInfo = {
  id: 'subagent-1',
  parentId: 'root-1',
  address: '1.1',
  title: 'find the bug',
  role: 'explorer',
  state: 'running',
  aborted: false,
  model: 'moonshotai/kimi-k2.7-code',
  thinkingEnabled: true,
  thinkingEffort: 'high',
  usedTokens: 500,
  maxTokens: null,
  outputTokens: 50,
};

describe('chatNickname', () => {
  it('formats role-address, e.g. explorer-1.1', () => {
    expect(chatNickname(CHILD_NODE)).toBe('explorer-1.1');
    expect(chatNickname(ROOT_NODE)).toBe('operator-1');
  });
});

describe('chatDisplayName', () => {
  it('returns "You" for the reserved user id', () => {
    expect(chatDisplayName('user', [ROOT_NODE, CHILD_NODE])).toBe('You');
  });

  it("returns a live participant's own nickname", () => {
    expect(chatDisplayName('subagent-1', [ROOT_NODE, CHILD_NODE])).toBe('explorer-1.1');
  });

  it('falls back to the raw sender id for a session no longer in the tree', () => {
    expect(chatDisplayName('long-gone-session', [ROOT_NODE])).toBe('long-gone-session');
  });
});

describe('findChatMentionSpans', () => {
  it('resolves a mention by raw session id', () => {
    const spans = findChatMentionSpans('hi @subagent-1', [ROOT_NODE, CHILD_NODE]);
    expect(spans).toEqual([{ start: 3, end: 14, resolvedId: 'subagent-1' }]);
  });

  it('resolves a mention by role-address nickname, case-insensitively', () => {
    const spans = findChatMentionSpans('hi @Explorer-1.1', [ROOT_NODE, CHILD_NODE]);
    expect(spans[0]?.resolvedId).toBe('subagent-1');
  });

  it('resolves @user (any casing) to the reserved user id', () => {
    const spans = findChatMentionSpans('please look, @User', [ROOT_NODE]);
    expect(spans[0]?.resolvedId).toBe('user');
  });

  it('leaves an unresolved mention with resolvedId undefined', () => {
    const spans = findChatMentionSpans('hi @nobody-here', [ROOT_NODE]);
    expect(spans[0]?.resolvedId).toBeUndefined();
  });

  it('finds no spans in a message with no @mentions', () => {
    expect(findChatMentionSpans('just chatting', [ROOT_NODE])).toEqual([]);
  });

  it('does not match an email-like mid-word @', () => {
    expect(findChatMentionSpans('foo@bar.com', [ROOT_NODE])).toEqual([]);
  });
});

describe('applyChatHistoryUpdate', () => {
  it('replaces the snapshot wholesale on chatHistoryUpdate', () => {
    const message: HostMessage = {
      type: 'chatHistoryUpdate',
      messages: [{ seq: 1, senderId: 'root-1', timestamp: '2026-01-01T00:00:00', body: 'hi' }],
      unreadCount: 1,
      unreadMentionCount: 0,
    };
    expect(applyChatHistoryUpdate(undefined, message)).toEqual({
      messages: message.messages,
      unreadCount: 1,
      unreadMentionCount: 0,
    });
  });

  it('clears the snapshot on sessionReset', () => {
    const existing = { messages: [], unreadCount: 1, unreadMentionCount: 0 };
    expect(applyChatHistoryUpdate(existing, { type: 'sessionReset' })).toBeUndefined();
  });

  it('leaves the snapshot unchanged for an unrelated message', () => {
    const existing = { messages: [], unreadCount: 1, unreadMentionCount: 0 };
    expect(applyChatHistoryUpdate(existing, { type: 'turnStarted' })).toBe(existing);
  });
});

describe('chatRowMarker', () => {
  it('is undefined while the chat room is selected', () => {
    expect(chatRowMarker(5, 1, true, true)).toBeUndefined();
  });

  it('is undefined when there is nothing unread', () => {
    expect(chatRowMarker(0, 0, false, true)).toBeUndefined();
  });

  it('is a steady "!" for unread messages with no mention', () => {
    expect(chatRowMarker(3, 0, false, false)).toBe('!');
    expect(chatRowMarker(3, 0, false, true)).toBe('!');
  });

  it("blinks with the panel's own blink phase once one unread message @mentions the user", () => {
    expect(chatRowMarker(3, 1, false, true)).toBe('!');
    expect(chatRowMarker(3, 1, false, false)).toBeUndefined();
  });
});
