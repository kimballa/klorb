/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { createRef } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import IconButton from 'webview/components/IconButton';

afterEach(cleanup);

describe('IconButton', () => {
  it('renders children, and uses title as both the tooltip and accessible name', () => {
    render(
      <IconButton title="Session history" onClick={() => undefined}>
        <span>icon</span>
      </IconButton>
    );

    const button = screen.getByTitle('Session history');
    expect(button).toBeTruthy();
    expect(button).toBe(screen.getByLabelText('Session history'));
    expect(screen.getByText('icon')).toBeTruthy();
  });

  it('defaults to the toolbar variant and switches to filled when requested', () => {
    const { rerender } = render(
      <IconButton title="Toolbar" onClick={() => undefined}>
        <span>icon</span>
      </IconButton>
    );
    expect(screen.getByTitle('Toolbar').className).toBe('icon-button icon-button-toolbar');

    rerender(
      <IconButton title="Toolbar" variant="filled" onClick={() => undefined}>
        <span>icon</span>
      </IconButton>
    );
    expect(screen.getByTitle('Toolbar').className).toBe('icon-button icon-button-filled');
  });

  it('calls onClick when clicked', () => {
    const onClick = vi.fn();
    render(
      <IconButton title="Click me" onClick={onClick}>
        <span>icon</span>
      </IconButton>
    );

    fireEvent.click(screen.getByTitle('Click me'));

    expect(onClick).toHaveBeenCalledOnce();
  });

  it('forwards a ref to the underlying button element', () => {
    const ref = createRef<HTMLButtonElement>();
    render(
      <IconButton ref={ref} title="Ref target" onClick={() => undefined}>
        <span>icon</span>
      </IconButton>
    );

    expect(ref.current).toBe(screen.getByTitle('Ref target'));
  });
});
