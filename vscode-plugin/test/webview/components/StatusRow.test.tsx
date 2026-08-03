/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import StatusRow from 'webview/components/StatusRow';

afterEach(cleanup);

const NOOP = {
  taskPanelVisible: true,
  subagentsPanelVisible: false,
  onPickModel: () => undefined,
  onPickThinking: () => undefined,
  onCyclePermissionMode: () => undefined,
  onSetPermissionMode: () => undefined,
  onShowSessionStats: () => undefined,
  onNewSession: () => undefined,
  onReloadSkills: () => undefined,
  onToggleTaskPanel: () => undefined,
  onToggleSubagentsPanel: () => undefined,
  onAttachImage: () => undefined,
};

describe('StatusRow', () => {
  it('shows the model chip alone, independent of thinking state', () => {
    render(<StatusRow model="gpt-5" thinkingEnabled={false} permissionMode="ask" {...NOOP} />);

    expect(screen.getByText('gpt-5')).toBeTruthy();
  });

  it('shows the thinking chip as "Off" while disabled', () => {
    render(<StatusRow thinkingEnabled={false} thinkingEffort="high" {...NOOP} />);

    expect(screen.getByText('Off')).toBeTruthy();
  });

  it('shows the effort label on the thinking chip while enabled', () => {
    render(<StatusRow thinkingEnabled={true} thinkingEffort="high" {...NOOP} />);

    expect(screen.getByText('High')).toBeTruthy();
  });

  it('shows a placeholder before the model or thinking config is known', () => {
    render(<StatusRow permissionMode="ask" {...NOOP} />);

    expect(screen.getAllByText('...')).toHaveLength(2);
  });

  it('renders the bracketed permission mode and defaults to ask', () => {
    render(<StatusRow {...NOOP} />);

    expect(screen.getByText('[ask]')).toBeTruthy();
  });

  it('renders the token tally with a limit, and omits it when max is null', () => {
    const { rerender } = render(<StatusRow usedTokens={1400} maxTokens={128000} {...NOOP} />);
    expect(screen.getByText('↑ 1.4k / 130k')).toBeTruthy();

    rerender(<StatusRow usedTokens={1400} maxTokens={null} {...NOOP} />);
    expect(screen.getByText('↑ 1.4k')).toBeTruthy();
  });

  it('omits the tally entirely before any usage is known', () => {
    const { container } = render(<StatusRow {...NOOP} />);

    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    expect(container.querySelector('.status-tokens')).toBeNull();
  });

  it('includes the output token count alongside the context tally', () => {
    render(<StatusRow usedTokens={1400} maxTokens={128000} outputTokens={2500} {...NOOP} />);

    expect(screen.getByText('↑ 1.4k / 130k ↓ 2.5k')).toBeTruthy();
  });

  it('shows the output token count alone before any context usage is known', () => {
    render(<StatusRow outputTokens={2500} {...NOOP} />);

    expect(screen.getByText('↓ 2.5k')).toBeTruthy();
  });

  it('posts pickModel intent when the model chip is clicked', () => {
    const onPickModel = vi.fn();
    render(<StatusRow model="gpt-5" {...NOOP} onPickModel={onPickModel} />);

    fireEvent.click(screen.getByText('gpt-5'));
    expect(onPickModel).toHaveBeenCalledOnce();
  });

  it('posts pickThinking intent when the thinking chip is clicked', () => {
    const onPickThinking = vi.fn();
    render(
      <StatusRow
        thinkingEnabled={true}
        thinkingEffort="medium"
        {...NOOP}
        onPickThinking={onPickThinking}
      />
    );

    fireEvent.click(screen.getByText('Medium'));
    expect(onPickThinking).toHaveBeenCalledOnce();
  });

  it('posts cyclePermissionMode intent when the badge is clicked', () => {
    const onCyclePermissionMode = vi.fn();
    render(
      <StatusRow permissionMode="auto" {...NOOP} onCyclePermissionMode={onCyclePermissionMode} />
    );

    fireEvent.click(screen.getByText('[auto]'));
    expect(onCyclePermissionMode).toHaveBeenCalledOnce();
  });

  it('renders the model/thinking chips as plain text, not buttons, while interactive is false', () => {
    const onPickModel = vi.fn();
    render(
      <StatusRow
        model="gpt-5"
        thinkingEnabled={true}
        thinkingEffort="medium"
        interactive={false}
        {...NOOP}
        onPickModel={onPickModel}
      />
    );

    const modelChip = screen.getByText('gpt-5');
    expect(modelChip.tagName).toBe('SPAN');
    fireEvent.click(modelChip);
    expect(onPickModel).not.toHaveBeenCalled();
  });
});
