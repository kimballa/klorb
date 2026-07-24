/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { act } from 'react';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';

import { App, type VsCodeApi } from '../src/webview/App';

interface FakeVsCode {
  vscode: VsCodeApi;
  posted: unknown[];
}

function makeVsCode(): FakeVsCode {
  const posted: unknown[] = [];
  return {
    posted,
    vscode: {
      postMessage: (message: unknown) => posted.push(message),
      setState: () => undefined,
      getState: () => undefined,
    },
  };
}

function postHostMessage(data: unknown): void {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', { data }));
  });
}

function promptTextarea(container: HTMLElement): Element {
  const textarea = container.querySelector('vscode-textarea');
  if (textarea === null) {
    throw new Error('vscode-textarea not rendered');
  }
  return textarea;
}

/**
 * Types into the `<vscode-textarea>` custom element and hits Enter. jsdom doesn't recognize
 * a custom element as having a native `value` setter, so `fireEvent.input`'s usual
 * target-value shortcut doesn't apply here (per the plan's note to assert against the
 * custom-element tag boundary, not its shadow internals): the value is set directly on the
 * element before dispatching a plain `input` event, mirroring how the real Lit component
 * fires `input` after its own internal state changes.
 */
function typeAndSubmit(container: HTMLElement, text: string): void {
  const textarea = promptTextarea(container) as HTMLElement & { value: string };
  textarea.value = text;
  fireEvent(textarea, new Event('input', { bubbles: true }));
  fireEvent.keyDown(textarea, { key: 'Enter' });
}

beforeAll(() => {
  // jsdom doesn't implement scrollIntoView, which App calls after each entries change.
  window.HTMLElement.prototype.scrollIntoView = () => undefined;
  // Tells React this environment supports act(), silencing its warning when state updates
  // (like the message-event handler below) happen outside a render/event call React tracks.
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(cleanup);

describe('App', () => {
  it('posts submitPrompt and echoes the prompt entry on submit', () => {
    const { vscode, posted } = makeVsCode();
    const { container } = render(<App vscode={vscode} initialEntries={[]} />);

    typeAndSubmit(container, 'hello klorb');

    expect(posted).toEqual([{ type: 'submitPrompt', text: 'hello klorb' }]);
    expect(screen.getByText('hello klorb')).toBeTruthy();
  });

  it('renders incoming response chunks as they stream in', () => {
    const { vscode } = makeVsCode();
    render(<App vscode={vscode} initialEntries={[]} />);

    postHostMessage({ type: 'agentChunk', text: 'Hello' });
    postHostMessage({ type: 'agentChunk', text: ' world' });

    expect(screen.getByText('Hello world')).toBeTruthy();
  });

  it('renders thinking chunks inside a collapsed disclosure', () => {
    const { vscode } = makeVsCode();
    const { container } = render(<App vscode={vscode} initialEntries={[]} />);

    postHostMessage({ type: 'thoughtChunk', text: 'pondering deeply' });

    const details = container.querySelector('details.entry-thinking');
    expect(details).not.toBeNull();
    expect(details?.hasAttribute('open')).toBe(false);
    expect(screen.getByText('pondering deeply')).toBeTruthy();
  });

  it('disables the input and shows Stop while a turn is in flight', () => {
    const { vscode } = makeVsCode();
    const { container } = render(<App vscode={vscode} initialEntries={[]} />);

    typeAndSubmit(container, 'long task');

    expect(promptTextarea(container).hasAttribute('disabled')).toBe(true);
    expect(screen.getByText('Stop')).toBeTruthy();
  });

  it('posts cancelTurn when Stop is clicked', () => {
    const { vscode, posted } = makeVsCode();
    const { container } = render(<App vscode={vscode} initialEntries={[]} />);

    typeAndSubmit(container, 'long task');
    fireEvent.click(screen.getByText('Stop'));

    expect(posted).toContainEqual({ type: 'cancelTurn' });
  });

  it('re-enables the input when the turn ends', () => {
    const { vscode } = makeVsCode();
    const { container } = render(<App vscode={vscode} initialEntries={[]} />);

    typeAndSubmit(container, 'quick task');
    postHostMessage({ type: 'turnEnded', stopReason: 'end_turn' });

    expect(promptTextarea(container).hasAttribute('disabled')).toBe(false);
    expect(screen.getByText('Send')).toBeTruthy();
  });

  it('shows a turnError as an error entry and re-enables the input', () => {
    const { vscode } = makeVsCode();
    const { container } = render(<App vscode={vscode} initialEntries={[]} />);

    typeAndSubmit(container, 'doomed task');
    postHostMessage({ type: 'turnError', message: 'server exploded' });

    expect(screen.getByText('server exploded')).toBeTruthy();
    expect(promptTextarea(container).hasAttribute('disabled')).toBe(false);
  });

  it('clears the history on sessionReset', () => {
    const { vscode } = makeVsCode();
    render(
      <App
        vscode={vscode}
        initialEntries={[{ kind: 'prompt', text: 'old prompt', streaming: false }]}
      />,
    );

    expect(screen.getByText('old prompt')).toBeTruthy();
    postHostMessage({ type: 'sessionReset' });
    expect(screen.queryByText('old prompt')).toBeNull();
  });
});
