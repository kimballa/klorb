/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SubagentNodeInfo } from 'shared/webviewMessages';
import { SubagentsPanel, type SubagentsPanelProps } from 'webview/features/subagents';

afterEach(cleanup);

const ROOT_NODE: SubagentNodeInfo = {
  id: 'root-1',
  parentId: null,
  address: '1',
  title: 'Fix the bug',
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

/** Fills in the chat-room props every test doesn't specifically exercise, so each test only
 * spells out what it actually varies. */
function chatRoomDefaults(): Pick<
  SubagentsPanelProps,
  'chatRoomSelected' | 'chatUnreadCount' | 'chatUnreadMentionCount' | 'onSelectChatRoom'
> {
  return {
    chatRoomSelected: false,
    chatUnreadCount: 0,
    chatUnreadMentionCount: 0,
    onSelectChatRoom: () => undefined,
  };
}

describe('SubagentsPanel', () => {
  it('renders nothing until a subagentTreeUpdate has arrived', () => {
    const { container } = render(
      <SubagentsPanel
        nodes={[]}
        selectedSessionId={null}
        onSelect={() => undefined}
        onToggleVisibility={() => undefined}
        {...chatRoomDefaults()}
      />
    );

    expect(container.innerHTML).toBe('');
  });

  it('renders one row per node, including the root', () => {
    render(
      <SubagentsPanel
        nodes={[ROOT_NODE, CHILD_NODE]}
        selectedSessionId={null}
        onSelect={() => undefined}
        onToggleVisibility={() => undefined}
        {...chatRoomDefaults()}
      />
    );

    expect(screen.getByText('1')).toBeTruthy();
    expect(screen.getByText('Fix the bug')).toBeTruthy();
    expect(screen.getByText('1.1')).toBeTruthy();
    expect(screen.getByText('find the bug')).toBeTruthy();
  });

  it('selects the root (null) when the root row is clicked', () => {
    const onSelect = vi.fn();
    render(
      <SubagentsPanel
        nodes={[ROOT_NODE, CHILD_NODE]}
        selectedSessionId="subagent-1"
        onSelect={onSelect}
        onToggleVisibility={() => undefined}
        {...chatRoomDefaults()}
      />
    );

    fireEvent.click(screen.getByText('Fix the bug'));
    expect(onSelect).toHaveBeenCalledWith(null);
  });

  it("selects a subagent's own id when its row is clicked", () => {
    const onSelect = vi.fn();
    render(
      <SubagentsPanel
        nodes={[ROOT_NODE, CHILD_NODE]}
        selectedSessionId={null}
        onSelect={onSelect}
        onToggleVisibility={() => undefined}
        {...chatRoomDefaults()}
      />
    );

    fireEvent.click(screen.getByText('find the bug'));
    expect(onSelect).toHaveBeenCalledWith('subagent-1');
  });

  it("shows the selected row's role in the footer", () => {
    render(
      <SubagentsPanel
        nodes={[ROOT_NODE, CHILD_NODE]}
        selectedSessionId="subagent-1"
        onSelect={() => undefined}
        onToggleVisibility={() => undefined}
        {...chatRoomDefaults()}
      />
    );

    expect(screen.getByText('Agent role: Explorer')).toBeTruthy();
  });

  it('calls onToggleVisibility when the pin icon is clicked', () => {
    const onToggleVisibility = vi.fn();
    render(
      <SubagentsPanel
        nodes={[ROOT_NODE]}
        selectedSessionId={null}
        onSelect={() => undefined}
        onToggleVisibility={onToggleVisibility}
        {...chatRoomDefaults()}
      />
    );

    fireEvent.click(screen.getByTitle('Unpin subagents panel'));
    expect(onToggleVisibility).toHaveBeenCalledOnce();
  });

  it('does not render the Chat Room row when the server is not chat-capable', () => {
    render(
      <SubagentsPanel
        nodes={[ROOT_NODE]}
        selectedSessionId={null}
        onSelect={() => undefined}
        onToggleVisibility={() => undefined}
        chatCapable={false}
        {...chatRoomDefaults()}
      />
    );

    expect(screen.queryByText('💬 Chat Room')).toBeNull();
  });

  it('renders the Chat Room row first when chat-capable, and selecting it calls onSelectChatRoom', () => {
    const onSelectChatRoom = vi.fn();
    render(
      <SubagentsPanel
        nodes={[ROOT_NODE, CHILD_NODE]}
        selectedSessionId={null}
        onSelect={() => undefined}
        onToggleVisibility={() => undefined}
        chatCapable
        {...chatRoomDefaults()}
        onSelectChatRoom={onSelectChatRoom}
      />
    );

    const rows = screen.getAllByRole('option');
    expect(rows[0]?.textContent).toContain('💬 Chat Room');

    fireEvent.click(screen.getByText('💬 Chat Room'));
    expect(onSelectChatRoom).toHaveBeenCalledOnce();
  });

  it('shows a steady marker for unread chat messages while it is not selected', () => {
    render(
      <SubagentsPanel
        nodes={[ROOT_NODE]}
        selectedSessionId={null}
        onSelect={() => undefined}
        onToggleVisibility={() => undefined}
        chatCapable
        chatRoomSelected={false}
        chatUnreadCount={3}
        chatUnreadMentionCount={0}
        onSelectChatRoom={() => undefined}
      />
    );

    const chatRow = screen.getByRole('option', { name: /Chat Room/ });
    expect(chatRow.textContent).toContain('!');
  });

  it('shows "Chat Room" in the footer, and no marker, once the row is selected', () => {
    render(
      <SubagentsPanel
        nodes={[ROOT_NODE]}
        selectedSessionId={null}
        onSelect={() => undefined}
        onToggleVisibility={() => undefined}
        chatCapable
        chatRoomSelected
        chatUnreadCount={3}
        chatUnreadMentionCount={0}
        onSelectChatRoom={() => undefined}
      />
    );

    expect(screen.getByText('Chat Room')).toBeTruthy();
    const chatRow = screen.getByRole('option', { name: /Chat Room/ });
    expect(chatRow.textContent).not.toContain('!');
  });
});
