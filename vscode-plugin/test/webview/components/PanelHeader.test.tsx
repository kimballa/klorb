/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import PanelHeader, { type PanelHeaderProps } from 'webview/components/PanelHeader';

afterEach(cleanup);

function mountPanelHeader(overrides: Partial<PanelHeaderProps> = {}): PanelHeaderProps {
  const props: PanelHeaderProps = {
    title: 'Session: Fix auth bug',
    onNewSession: vi.fn(),
    onBrowseSessions: vi.fn(),
    ...overrides,
  };
  render(<PanelHeader {...props} />);
  return props;
}

describe('PanelHeader', () => {
  it('renders the given title', () => {
    mountPanelHeader({ title: 'Session: Fix auth bug' });
    expect(screen.getByText('Session: Fix auth bug')).toBeTruthy();
  });

  it('calls onNewSession when the "New session" icon is clicked', () => {
    const props = mountPanelHeader();
    fireEvent.click(screen.getByTitle('New session'));
    expect(props.onNewSession).toHaveBeenCalledOnce();
    expect(props.onBrowseSessions).not.toHaveBeenCalled();
  });

  it('calls onBrowseSessions when the "Session history" icon is clicked', () => {
    const props = mountPanelHeader();
    fireEvent.click(screen.getByTitle('Session history'));
    expect(props.onBrowseSessions).toHaveBeenCalledOnce();
    expect(props.onNewSession).not.toHaveBeenCalled();
  });
});
