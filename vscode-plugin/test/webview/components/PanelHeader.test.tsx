/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import PanelHeader, {
  SESSION_TITLE_PLACEHOLDER,
  type PanelHeaderProps,
} from 'webview/components/PanelHeader';

afterEach(cleanup);

function mountPanelHeader(overrides: Partial<PanelHeaderProps> = {}): PanelHeaderProps {
  const props: PanelHeaderProps = {
    title: 'Session: Fix auth bug',
    titleIsPlaceholder: false,
    editable: false,
    rawTitle: 'Session: Fix auth bug',
    onNewSession: vi.fn(),
    onBrowseSessions: vi.fn(),
    onRenameSession: vi.fn(),
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

  it('renders the placeholder text in italic styling when titleIsPlaceholder', () => {
    mountPanelHeader({ title: SESSION_TITLE_PLACEHOLDER, titleIsPlaceholder: true });
    const el = screen.getByText(SESSION_TITLE_PLACEHOLDER);
    expect(el.className).toContain('title-placeholder');
  });

  it('does not enter edit mode on double-click when not editable', () => {
    mountPanelHeader({ editable: false });
    fireEvent.doubleClick(screen.getByText('Session: Fix auth bug'));
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('double-click enters edit mode seeded from rawTitle', () => {
    mountPanelHeader({ editable: true, rawTitle: 'Session: Fix auth bug' });
    fireEvent.doubleClick(screen.getByText('Session: Fix auth bug'));
    const input = screen.getByRole('textbox') as HTMLInputElement;
    expect(input.value).toBe('Session: Fix auth bug');
  });

  it('Enter commits the edited title', () => {
    const props = mountPanelHeader({ editable: true, rawTitle: 'Old title', title: 'Old title' });
    fireEvent.doubleClick(screen.getByText('Old title'));
    const input = screen.getByRole('textbox') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'New title' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(props.onRenameSession).toHaveBeenCalledOnce();
    expect(props.onRenameSession).toHaveBeenCalledWith('New title');
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('committing an empty/whitespace-only value renames to null', () => {
    const props = mountPanelHeader({ editable: true, rawTitle: 'Old title', title: 'Old title' });
    fireEvent.doubleClick(screen.getByText('Old title'));
    const input = screen.getByRole('textbox') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(props.onRenameSession).toHaveBeenCalledOnce();
    expect(props.onRenameSession).toHaveBeenCalledWith(null);
  });

  it('Escape cancels the edit without committing', () => {
    const props = mountPanelHeader({ editable: true, rawTitle: 'Old title', title: 'Old title' });
    fireEvent.doubleClick(screen.getByText('Old title'));
    const input = screen.getByRole('textbox') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Discarded edit' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(props.onRenameSession).not.toHaveBeenCalled();
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.getByText('Old title')).toBeTruthy();
  });

  it('blurring the input commits, same as Enter', () => {
    const props = mountPanelHeader({ editable: true, rawTitle: 'Old title', title: 'Old title' });
    fireEvent.doubleClick(screen.getByText('Old title'));
    const input = screen.getByRole('textbox') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Blurred title' } });
    fireEvent.blur(input);
    expect(props.onRenameSession).toHaveBeenCalledOnce();
    expect(props.onRenameSession).toHaveBeenCalledWith('Blurred title');
  });

  it('committing an unchanged value does not call onRenameSession', () => {
    const props = mountPanelHeader({ editable: true, rawTitle: 'Same title', title: 'Same title' });
    fireEvent.doubleClick(screen.getByText('Same title'));
    const input = screen.getByRole('textbox') as HTMLInputElement;
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(props.onRenameSession).not.toHaveBeenCalled();
  });
});
