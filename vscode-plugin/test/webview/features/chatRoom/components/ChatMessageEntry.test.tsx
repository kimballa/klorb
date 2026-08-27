/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ChatMessageInfo, SubagentNodeInfo } from 'shared/webviewMessages';
import { ChatMessageEntry } from 'webview/features/chatRoom';

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

const EXPLORER_NODE: SubagentNodeInfo = {
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

const NODES = [ROOT_NODE, EXPLORER_NODE];

function message(overrides: Partial<ChatMessageInfo> = {}): ChatMessageInfo {
  return {
    seq: 1,
    senderId: 'root-1',
    timestamp: '2026-01-01T12:34:00',
    body: 'hello',
    ...overrides,
  };
}

afterEach(cleanup);

describe('ChatMessageEntry', () => {
  it("renders the sender's nickname and body, without the .chat-message-own class", () => {
    const { container } = render(
      <ChatMessageEntry message={message()} nodes={NODES} onSelectParticipant={vi.fn()} />
    );
    expect(container.textContent).toContain('operator-1');
    expect(container.textContent).toContain('hello');
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    expect(container.querySelector('.chat-message-own')).toBeNull();
  });

  it("renders 'You' and the .chat-message-own class for the user's own post", () => {
    const { container } = render(
      <ChatMessageEntry
        message={message({ senderId: 'user' })}
        nodes={NODES}
        onSelectParticipant={vi.fn()}
      />
    );
    expect(container.textContent).toContain('You');
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    expect(container.querySelector('.chat-message-own')).not.toBeNull();
  });

  it('renders a resolved agent @mention as a clickable .mention-chip, selecting it on click', () => {
    const onSelectParticipant = vi.fn();
    const { container } = render(
      <ChatMessageEntry
        message={message({ body: 'hi @explorer-1.1' })}
        nodes={NODES}
        onSelectParticipant={onSelectParticipant}
      />
    );
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const chip = container.querySelector('.mention-chip');
    expect(chip).not.toBeNull();
    expect(chip?.textContent).toBe('@explorer-1.1');
    fireEvent.click(chip!);
    expect(onSelectParticipant).toHaveBeenCalledWith('subagent-1');
  });

  it('renders an @user mention as "@You" with no click handler (no-op)', () => {
    const onSelectParticipant = vi.fn();
    const { container } = render(
      <ChatMessageEntry
        message={message({ body: 'thanks @user' })}
        nodes={NODES}
        onSelectParticipant={onSelectParticipant}
      />
    );
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const chip = container.querySelector('.mention-chip');
    expect(chip?.textContent).toBe('@You');
    fireEvent.click(chip!);
    expect(onSelectParticipant).not.toHaveBeenCalled();
  });

  it('leaves an unresolved mention as plain unstyled text', () => {
    const { container } = render(
      <ChatMessageEntry
        message={message({ body: 'hi @nobody-here' })}
        nodes={NODES}
        onSelectParticipant={vi.fn()}
      />
    );
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    expect(container.querySelectorAll('.mention-chip')).toHaveLength(0);
    expect(container.textContent).toContain('@nobody-here');
  });
});
