/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { createRef } from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import PromptInput, { type PromptInputHandle } from 'webview/components/PromptInput';

beforeAll(() => {
  // jsdom doesn't implement scrollIntoView, which FileFinderPanel calls to keep its active row
  // visible (see test/webview/App.test.tsx's own identical stub).
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

vi.mock('@chenglou/pretext', () => ({
  prepare: vi.fn(() => ({})),
  layout: vi.fn((_: unknown, __: number, lineHeight: number) => ({
    lineCount: 1,
    height: lineHeight,
  })),
}));

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
    expect(screen.queryByTitle('Send')).toBeNull();
    expect(screen.getByTitle('Stop')).toBeTruthy();
  });

  it('keeps the textarea enabled and shows both Send and Stop when enqueueMessageCapable', () => {
    const { container } = render(
      <PromptInput inFlight enqueueMessageCapable onSubmit={() => undefined} {...NOOP} />
    );

    expect(promptTextarea(container).hasAttribute('disabled')).toBe(false);
    expect(screen.getByTitle('Send')).toBeTruthy();
    expect(screen.getByTitle('Stop')).toBeTruthy();
  });

  it('disables Send while the draft is empty, and enables it once text is typed', () => {
    const { container } = render(
      <PromptInput inFlight={false} onSubmit={() => undefined} {...NOOP} />
    );

    expect(screen.getByTitle('Send').hasAttribute('disabled')).toBe(true);

    typeText(container, 'hello');

    expect(screen.getByTitle('Send').hasAttribute('disabled')).toBe(false);
  });

  it('calls onSubmit with the trimmed text on Send while enqueueMessageCapable mid-turn', () => {
    const onSubmit = vi.fn();
    const { container } = render(
      <PromptInput inFlight enqueueMessageCapable onSubmit={onSubmit} {...NOOP} />
    );

    typeText(container, '  also check the tests  ');
    fireEvent.click(screen.getByTitle('Send'));

    expect(onSubmit).toHaveBeenCalledWith('also check the tests');
  });

  it('does not submit an empty/whitespace-only draft', () => {
    const onSubmit = vi.fn();
    const { container } = render(<PromptInput inFlight={false} onSubmit={onSubmit} {...NOOP} />);

    typeText(container, '   ');
    fireEvent.click(screen.getByTitle('Send'));

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

const WORKSPACE_FILES = ['src/App.tsx', 'README.md'];

describe('PromptInput file finder', () => {
  it('shows matching files once @ is typed', () => {
    const { container } = render(
      <PromptInput
        inFlight={false}
        workspaceFiles={WORKSPACE_FILES}
        onSubmit={() => undefined}
        {...NOOP}
      />
    );

    typeText(container, '@App');

    expect(screen.getByText('/App.tsx')).toBeTruthy();
    expect(screen.queryByText('README.md')).toBeNull();
  });

  it('splices the active match into the draft on Enter, keeping the finder closed', () => {
    const { container } = render(
      <PromptInput
        inFlight={false}
        workspaceFiles={WORKSPACE_FILES}
        onSubmit={() => undefined}
        {...NOOP}
      />
    );

    typeText(container, '@App');
    fireEvent.keyDown(promptTextarea(container), { key: 'Enter' });

    expect(promptTextarea(container).value).toBe('@src/App.tsx ');
    expect(screen.queryByText('/App.tsx')).toBeNull();
  });

  it('selects a match on click', () => {
    const { container } = render(
      <PromptInput
        inFlight={false}
        workspaceFiles={WORKSPACE_FILES}
        onSubmit={() => undefined}
        {...NOOP}
      />
    );

    typeText(container, '@App');
    fireEvent.click(screen.getByText('/App.tsx'));

    expect(promptTextarea(container).value).toBe('@src/App.tsx ');
  });

  it('Escape dismisses the finder without submitting or cancelling', () => {
    const onCancel = vi.fn();
    const { container } = render(
      <PromptInput
        inFlight={false}
        workspaceFiles={WORKSPACE_FILES}
        onSubmit={() => undefined}
        onCancel={onCancel}
        onCyclePermissionMode={() => undefined}
      />
    );

    typeText(container, '@App');
    fireEvent.keyDown(promptTextarea(container), { key: 'Escape' });

    expect(screen.queryByText('/App.tsx')).toBeNull();
    expect(promptTextarea(container).value).toBe('@App');
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('closes the finder once further typing rules out every file', () => {
    const { container } = render(
      <PromptInput
        inFlight={false}
        workspaceFiles={WORKSPACE_FILES}
        onSubmit={() => undefined}
        {...NOOP}
      />
    );

    typeText(container, '@App');
    expect(screen.getByText('/App.tsx')).toBeTruthy();

    typeText(container, '@Appzzzznomatch');

    expect(screen.queryByText('/App.tsx')).toBeNull();
  });
});
