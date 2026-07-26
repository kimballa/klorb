/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { act } from 'react';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';

import App from 'webview/App';
import type { VsCodeApi } from 'webview/components/VsCodeApiProvider';

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
  // Testing Library has no role-based query for the <vscode-textarea> custom element, so a
  // direct querySelector is the only way to reach it (see typeAndSubmit's comment below).
  // eslint-disable-next-line testing-library/no-node-access
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

    // No Testing Library query targets a bare <details> by class; a direct query is the only
    // way to assert its collapsed state here.
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
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

  it('renders a tool call chip that goes busy then completed', () => {
    const { vscode } = makeVsCode();
    const { container } = render(<App vscode={vscode} initialEntries={[]} />);

    postHostMessage({
      type: 'toolCallStarted',
      callId: 'call-1',
      title: 'Read foo.py',
      kind: 'read',
      locations: [{ path: '/tmp/foo.py', line: 3 }],
    });

    expect(screen.getByText('Read foo.py')).toBeTruthy();
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    expect(container.querySelector('.tool-call-in_progress')).not.toBeNull();

    postHostMessage({
      type: 'toolCallUpdated',
      callId: 'call-1',
      status: 'completed',
      contentText: 'read 10 lines',
    });

    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    expect(container.querySelector('.tool-call-completed')).not.toBeNull();
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    expect(container.querySelector('.tool-call-in_progress')).toBeNull();
  });

  it('posts openLocation with the payload when a tool call title is clicked', () => {
    const { vscode, posted } = makeVsCode();
    render(<App vscode={vscode} initialEntries={[]} />);

    postHostMessage({
      type: 'toolCallStarted',
      callId: 'call-1',
      title: 'Read foo.py',
      kind: 'read',
      locations: [{ path: '/tmp/foo.py', line: 3 }],
    });
    fireEvent.click(screen.getByText('Read foo.py'));

    expect(posted).toContainEqual({ type: 'openLocation', path: '/tmp/foo.py', line: 3 });
  });

  it('restores a pending interaction from initialPendingInteraction (the vscode.setState round-trip)', () => {
    const { vscode } = makeVsCode();
    render(
      <App
        vscode={vscode}
        initialEntries={[]}
        initialPendingInteraction={{
          type: 'permissionAsk',
          requestId: 7,
          title: 'Run: ls',
          options: [{ id: 'allow:once', name: 'Allow once', kind: 'allow_once' }],
          klorbMeta: { resourceDescription: 'run shell command: ls', headerKind: 'Run command' },
        }}
      />
    );

    expect(screen.getByText('Permission requested: Run command')).toBeTruthy();
    expect(screen.getByText('run shell command: ls')).toBeTruthy();
  });

  it('mounts the ApprovalPanel on a permissionAsk and posts the decision on a click', () => {
    const { vscode, posted } = makeVsCode();
    render(<App vscode={vscode} initialEntries={[]} />);

    postHostMessage({
      type: 'permissionAsk',
      requestId: 1,
      title: 'Run: ls',
      options: [{ id: 'allow:once', name: 'Allow once', kind: 'allow_once' }],
      klorbMeta: { resourceDescription: 'run shell command: ls', headerKind: 'Run command' },
    });

    expect(screen.getByText('Permission requested: Run command')).toBeTruthy();
    fireEvent.click(screen.getByText('Allow once'));

    expect(posted).toContainEqual({
      type: 'permissionDecision',
      requestId: 1,
      optionId: 'allow:once',
      otherText: undefined,
    });
    expect(screen.queryByText('Permission requested: Run command')).toBeNull();
    expect(screen.getByText(/Decision: Allow once/)).toBeTruthy();
  });

  it('mounts the QuestionPanel on a questionAsk and posts the answer on a click', () => {
    const { vscode, posted } = makeVsCode();
    render(<App vscode={vscode} initialEntries={[]} />);

    postHostMessage({
      type: 'questionAsk',
      requestId: 1,
      header: 'Format',
      question: 'Which format?',
      options: [{ label: 'JSON' }, { label: 'YAML', description: 'human-friendly' }],
      index: 0,
      total: 2,
    });

    expect(screen.getByText('Which format?')).toBeTruthy();
    fireEvent.click(screen.getByText('JSON'));

    expect(posted).toContainEqual({
      type: 'questionAnswer',
      requestId: 1,
      selectedOptionIndex: 0,
    });
    expect(screen.queryByText('Which format?')).toBeNull();
    expect(screen.getByText(/Answer: JSON/)).toBeTruthy();
  });

  it('renders a statusUpdate in the status row and posts intents on click', () => {
    const { vscode, posted } = makeVsCode();
    render(<App vscode={vscode} initialEntries={[]} />);

    postHostMessage({
      type: 'statusUpdate',
      model: 'gpt-5',
      thinkingEnabled: true,
      thinkingEffort: 'high',
      permissionMode: 'auto',
    });

    expect(screen.getByText('gpt-5')).toBeTruthy();
    expect(screen.getByText('High')).toBeTruthy();
    fireEvent.click(screen.getByText('gpt-5'));
    fireEvent.click(screen.getByText('High'));
    fireEvent.click(screen.getByText('[auto]'));

    expect(posted).toContainEqual({ type: 'pickModel' });
    expect(posted).toContainEqual({ type: 'pickThinking' });
    expect(posted).toContainEqual({ type: 'cyclePermissionMode' });
  });

  it('posts the status menu intents for the actions with no chip of their own', () => {
    const { vscode, posted } = makeVsCode();
    const { container } = render(<App vscode={vscode} initialEntries={[]} />);

    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const menu = container.querySelector('vscode-context-menu') as Element;
    function selectMenuItem(value: string): void {
      fireEvent(
        menu,
        new CustomEvent('vsc-context-menu-select', {
          detail: { value, label: '', keybinding: '', separator: false, tabindex: 0 },
        })
      );
    }

    selectMenuItem('permissionMode');
    selectMenuItem('sessionStats');
    selectMenuItem('newSession');
    selectMenuItem('reloadSkills');

    expect(posted).toContainEqual({ type: 'setPermissionMode' });
    expect(posted).not.toContainEqual({ type: 'cyclePermissionMode' });
    expect(posted).toContainEqual({ type: 'showSessionStats' });
    expect(posted).toContainEqual({ type: 'newSession' });
    expect(posted).toContainEqual({ type: 'reloadSkills' });
  });

  it('posts cyclePermissionMode on Shift+Tab in the prompt textarea, without moving focus', () => {
    const { vscode, posted } = makeVsCode();
    const { container } = render(<App vscode={vscode} initialEntries={[]} />);

    const textarea = promptTextarea(container);
    fireEvent.keyDown(textarea, { key: 'Tab', shiftKey: true });

    expect(posted).toContainEqual({ type: 'cyclePermissionMode' });
  });

  it('shows the active session title in the top title bar, not the status row', () => {
    const { vscode } = makeVsCode();
    render(<App vscode={vscode} initialEntries={[]} />);

    expect(screen.getByText('New session…')).toBeTruthy();

    postHostMessage({
      type: 'statusUpdate',
      sessionTitle: 'Fix the bug',
      workspaceTrusted: false,
    });

    expect(screen.getByText('Fix the bug (Untrusted)')).toBeTruthy();
  });

  it('replaces the status snapshot wholesale on each statusUpdate', () => {
    const { vscode } = makeVsCode();
    render(<App vscode={vscode} initialEntries={[]} />);

    postHostMessage({ type: 'statusUpdate', model: 'gpt-5', permissionMode: 'ask' });
    expect(screen.getByText('gpt-5')).toBeTruthy();

    postHostMessage({ type: 'statusUpdate', permissionMode: 'auto' });
    expect(screen.queryByText('gpt-5')).toBeNull();
    expect(screen.getAllByText('...')).toHaveLength(2);
  });

  it('clears the history on sessionReset', () => {
    const { vscode } = makeVsCode();
    render(
      <App
        vscode={vscode}
        initialEntries={[{ kind: 'prompt', text: 'old prompt', streaming: false }]}
      />
    );

    expect(screen.getByText('old prompt')).toBeTruthy();
    postHostMessage({ type: 'sessionReset' });
    expect(screen.queryByText('old prompt')).toBeNull();
  });
});
