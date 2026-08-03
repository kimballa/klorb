/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SubagentNodeInfo } from 'shared/webviewMessages';
import { SubagentsPanel } from 'webview/features/subagents';

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

describe('SubagentsPanel', () => {
  it('renders nothing until a subagentTreeUpdate has arrived', () => {
    const { container } = render(
      <SubagentsPanel
        nodes={[]}
        selectedSessionId={null}
        onSelect={() => undefined}
        onToggleVisibility={() => undefined}
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
      />
    );

    expect(screen.getByText('Role: explorer')).toBeTruthy();
  });

  it('calls onToggleVisibility when the pin icon is clicked', () => {
    const onToggleVisibility = vi.fn();
    render(
      <SubagentsPanel
        nodes={[ROOT_NODE]}
        selectedSessionId={null}
        onSelect={() => undefined}
        onToggleVisibility={onToggleVisibility}
      />
    );

    fireEvent.click(screen.getByTitle('Unpin subagents panel'));
    expect(onToggleVisibility).toHaveBeenCalledOnce();
  });
});
