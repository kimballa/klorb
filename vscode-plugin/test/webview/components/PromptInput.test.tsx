/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { createRef } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import PromptInput, { type PromptInputHandle } from 'webview/components/PromptInput';

const NOOP = { onCancel: () => undefined, onCyclePermissionMode: () => undefined };

function promptTextarea(container: HTMLElement): HTMLElement & { value: string } {
  // eslint-disable-next-line testing-library/no-node-access
  const textarea = container.querySelector('vscode-textarea');
  if (textarea === null) {
    throw new Error('vscode-textarea not rendered');
  }
  return textarea as HTMLElement & { value: string };
}

function typeText(container: HTMLElement, text: string): void {
  const textarea = promptTextarea(container);
  textarea.value = text;
  fireEvent(textarea, new Event('input', { bubbles: true }));
}

afterEach(cleanup);

describe('PromptInput', () => {
  it('disables the textarea and shows only Stop while a turn is in flight (no capability)', () => {
    const { container } = render(<PromptInput inFlight onSubmit={() => undefined} {...NOOP} />);

    expect(promptTextarea(container).hasAttribute('disabled')).toBe(true);
    expect(screen.queryByText('Send')).toBeNull();
    expect(screen.getByText('Stop')).toBeTruthy();
  });

  it('keeps the textarea enabled and shows both Send and Stop when enqueueMessageCapable', () => {
    const { container } = render(
      <PromptInput inFlight enqueueMessageCapable onSubmit={() => undefined} {...NOOP} />
    );

    expect(promptTextarea(container).hasAttribute('disabled')).toBe(false);
    expect(screen.getByText('Send')).toBeTruthy();
    expect(screen.getByText('Stop')).toBeTruthy();
  });

  it('calls onSubmit with the trimmed text on Send while enqueueMessageCapable mid-turn', () => {
    const onSubmit = vi.fn();
    const { container } = render(
      <PromptInput inFlight enqueueMessageCapable onSubmit={onSubmit} {...NOOP} />
    );

    typeText(container, '  also check the tests  ');
    fireEvent.click(screen.getByText('Send'));

    expect(onSubmit).toHaveBeenCalledWith('also check the tests');
  });

  it('does not submit an empty/whitespace-only draft', () => {
    const onSubmit = vi.fn();
    const { container } = render(<PromptInput inFlight={false} onSubmit={onSubmit} {...NOOP} />);

    typeText(container, '   ');
    fireEvent.click(screen.getByText('Send'));

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('exposes an imperative focus() that focuses the underlying textarea', () => {
    const ref = createRef<PromptInputHandle>();
    const { container } = render(
      <PromptInput ref={ref} inFlight={false} onSubmit={() => undefined} {...NOOP} />
    );
    const textarea = promptTextarea(container);
    const focusSpy = vi.spyOn(textarea, 'focus');

    ref.current?.focus();

    expect(focusSpy).toHaveBeenCalled();
  });
});
