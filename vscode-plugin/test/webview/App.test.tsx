/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import {
  cleanup,
  fireEvent,
  render as rtlRender,
  screen,
  type RenderResult,
} from '@testing-library/react';
import { act, type ReactElement } from 'react';
import { VirtuosoMockContext } from 'react-virtuoso';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import App from 'webview/App';
import type { VsCodeApi } from 'webview/components/VsCodeApiProvider';

vi.mock('@chenglou/pretext', () => ({
  prepare: vi.fn(() => ({})),
  layout: vi.fn((_: unknown, __: number, lineHeight: number) => ({
    lineCount: 1,
    height: lineHeight,
  })),
}));

const mockVirtuosoRef = vi.fn();
const mockHandleAtBottomStateChange = vi.fn();
const mockScrollToBottomIfPinned = vi.fn();
const mockScrollToBottom = vi.fn();

vi.mock('webview/hooks/usePinnedScroll', () => ({
  default: () => ({
    virtuosoRef: mockVirtuosoRef,
    handleAtBottomStateChange: mockHandleAtBottomStateChange,
    scrollToBottomIfPinned: mockScrollToBottomIfPinned,
    scrollToBottom: mockScrollToBottom,
  }),
}));

/** `@testing-library/react`'s own `render`, wrapped in a `VirtuosoMockContext.Provider` with a
 * viewport large enough to always fit every entry these tests construct -- jsdom has no real
 * layout, so `HistoryView`'s `react-virtuoso` list would otherwise render nothing (see
 * `react-virtuoso`'s own `VirtuosoMockContext` doc comment). Shadows the RTL import so every
 * `render(<App .../>)` call site below gets this for free. */
function render(ui: ReactElement): RenderResult {
  return rtlRender(
    <VirtuosoMockContext.Provider value={{ viewportHeight: 100000, itemHeight: 30 }}>
      {ui}
    </VirtuosoMockContext.Provider>
  );
}

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

/** The task panel's summary line text -- a direct `querySelector`/`textContent` read (rather
 * than `screen.getByText`) since the summary's bold "Tasks: N open" clause is a nested `<span>`
 * whose own direct text-node children don't include the rest of the line (and vice versa),
 * which is exactly the shape Testing Library's default text matcher can't match against the
 * full combined string -- see `TaskPanel.test.tsx`'s own `summaryLine()` helper. */
function taskPanelSummaryText(container: HTMLElement): string | null {
  return container.querySelector('.task-panel-summary-text')?.textContent ?? null;
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

const scrollIntoView = vi.fn();

beforeAll(() => {
  // jsdom doesn't implement scrollIntoView, which the file/skill finder panels call to keep
  // their active row in view.
  window.HTMLElement.prototype.scrollIntoView = scrollIntoView;
  // Tells React this environment supports act(), silencing its warning when state updates
  // (like the message-event handler below) happen outside a render/event call React tracks.
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

beforeEach(() => {
  scrollIntoView.mockClear();
  mockVirtuosoRef.mockClear();
  mockHandleAtBottomStateChange.mockClear();
  mockScrollToBottomIfPinned.mockClear();
  mockScrollToBottom.mockClear();
});

afterEach(cleanup);

describe('App', () => {
  it('posts submitPrompt and echoes the prompt entry on submit', () => {
    const { vscode, posted } = makeVsCode();
    const { container } = render(<App vscode={vscode} initialEntries={[]} />);

    typeAndSubmit(container, 'hello klorb');

    // `posted` also carries the mount-time `setSubagentsPanelVisible` resync message (see
    // App.tsx's own mount-resync effect) -- unrelated to this test's own submit-behavior focus.
    expect(
      posted.filter((message) => (message as { type: string }).type === 'submitPrompt')
    ).toEqual([{ type: 'submitPrompt', text: 'hello klorb' }]);
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
    expect(screen.getByTitle('Stop')).toBeTruthy();
  });

  it('posts cancelTurn when Stop is clicked', () => {
    const { vscode, posted } = makeVsCode();
    const { container } = render(<App vscode={vscode} initialEntries={[]} />);

    typeAndSubmit(container, 'long task');
    fireEvent.click(screen.getByTitle('Stop'));

    expect(posted).toContainEqual({ type: 'cancelTurn' });
  });

  it('re-enables the input when the turn ends', () => {
    const { vscode } = makeVsCode();
    const { container } = render(<App vscode={vscode} initialEntries={[]} />);

    typeAndSubmit(container, 'quick task');
    postHostMessage({ type: 'turnEnded', stopReason: 'end_turn' });

    expect(promptTextarea(container).hasAttribute('disabled')).toBe(false);
    expect(screen.getByTitle('Send')).toBeTruthy();
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

  it('shows the placeholder title once a session exists with no name, and posts renameSession on edit', () => {
    const { vscode, posted } = makeVsCode();
    render(<App vscode={vscode} initialEntries={[]} />);

    postHostMessage({ type: 'statusUpdate', sessionTitle: null, workspaceTrusted: true });

    const placeholder = screen.getByText('Klorb agent session');
    expect(placeholder.className).toContain('title-placeholder');

    fireEvent.doubleClick(placeholder);
    const input = screen.getByRole('textbox') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'My renamed session' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(posted).toContainEqual({ type: 'renameSession', title: 'My renamed session' });
  });

  it('does not allow editing the title before a session exists', () => {
    const { vscode } = makeVsCode();
    render(<App vscode={vscode} initialEntries={[]} />);

    fireEvent.doubleClick(screen.getByText('New session…'));

    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('posts newSession and listRecentSessions from the panel header icons', () => {
    const { vscode, posted } = makeVsCode();
    render(<App vscode={vscode} initialEntries={[]} />);

    fireEvent.click(screen.getByTitle('New session'));
    fireEvent.click(screen.getByTitle('Session history'));

    expect(posted).toContainEqual({ type: 'newSession' });
    expect(posted).toContainEqual({ type: 'listRecentSessions' });
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

  it('restores the task panel from initialTaskList (the vscode.setState round-trip)', () => {
    const { vscode } = makeVsCode();
    const { container } = render(
      <App
        vscode={vscode}
        initialEntries={[]}
        initialTaskList={{
          summary: { openCount: 1, closedCount: 0, blockedCount: 0, currentTaskId: 12 },
          tasks: [
            {
              issueId: 12,
              text: '#12 Fix the bug',
              priority: 'high',
              status: 'in_progress',
              blocked: false,
              isCurrentTask: true,
              closed: false,
            },
          ],
        }}
      />
    );

    expect(taskPanelSummaryText(container)).toBe('Tasks: 1 open · #12 – Fix the bug');
  });

  it('renders a taskListUpdate in the task panel and clears it on sessionReset', () => {
    const { vscode } = makeVsCode();
    const { container } = render(<App vscode={vscode} initialEntries={[]} />);

    postHostMessage({
      type: 'taskListUpdate',
      summary: { openCount: 1, closedCount: 0, blockedCount: 0, currentTaskId: 12 },
      tasks: [
        {
          issueId: 12,
          text: '#12 Fix the bug',
          priority: 'high',
          status: 'in_progress',
          blocked: false,
          isCurrentTask: true,
          closed: false,
        },
      ],
    });

    expect(taskPanelSummaryText(container)).toBe('Tasks: 1 open · #12 – Fix the bug');

    postHostMessage({ type: 'sessionReset' });

    expect(taskPanelSummaryText(container)).toBe('No tasks available');
  });

  it('hides the task panel on toggleTaskPanel and shows it again on a second toggle', () => {
    const { vscode } = makeVsCode();
    const { container } = render(<App vscode={vscode} initialEntries={[]} />);

    postHostMessage({
      type: 'taskListUpdate',
      summary: { openCount: 1, closedCount: 0, blockedCount: 0, currentTaskId: null },
      tasks: [
        {
          text: 'Investigate',
          priority: 'medium',
          status: 'pending',
          blocked: false,
          isCurrentTask: false,
          closed: false,
        },
      ],
    });
    expect(taskPanelSummaryText(container)).toBe('Tasks: 1 open');

    postHostMessage({ type: 'toggleTaskPanel' });
    expect(taskPanelSummaryText(container)).toBeNull();

    postHostMessage({ type: 'toggleTaskPanel' });
    expect(taskPanelSummaryText(container)).toBe('Tasks: 1 open');
  });

  it('shows "No tasks available" while the panel is open but no task-plan update has arrived yet', () => {
    const { vscode } = makeVsCode();
    const { container } = render(<App vscode={vscode} initialEntries={[]} />);

    // taskPanelVisible defaults to true, so the panel is already open on a fresh session.
    expect(taskPanelSummaryText(container)).toBe('No tasks available');

    postHostMessage({ type: 'toggleTaskPanel' });
    expect(taskPanelSummaryText(container)).toBeNull();

    postHostMessage({ type: 'toggleTaskPanel' });
    expect(taskPanelSummaryText(container)).toBe('No tasks available');
  });

  it('brings the task panel back via the status menu after its own pin hides it', () => {
    // The task panel's own header pin (TaskPanel.tsx) hides it with no UI of its own left to
    // bring it back -- the status row's StatusMenu is the recovery path, entirely client-side
    // (no round trip to the host), unlike the menu's other items.
    const { vscode, posted } = makeVsCode();
    const { container } = render(<App vscode={vscode} initialEntries={[]} />);

    postHostMessage({
      type: 'taskListUpdate',
      summary: { openCount: 1, closedCount: 0, blockedCount: 0, currentTaskId: null },
      tasks: [
        {
          text: 'Investigate',
          priority: 'medium',
          status: 'pending',
          blocked: false,
          isCurrentTask: false,
          closed: false,
        },
      ],
    });
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const pin = container.querySelector('.task-panel-pin') as Element;
    fireEvent.click(pin);
    expect(taskPanelSummaryText(container)).toBeNull();

    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const menu = container.querySelector('vscode-context-menu') as Element;
    fireEvent(
      menu,
      new CustomEvent('vsc-context-menu-select', {
        detail: {
          value: 'toggleTaskPanel',
          label: '',
          keybinding: '',
          separator: false,
          tabindex: 0,
        },
      })
    );

    expect(taskPanelSummaryText(container)).toBe('Tasks: 1 open');
    expect(posted).not.toContainEqual(expect.objectContaining({ type: 'toggleTaskPanel' }));
  });

  it('does not scroll the history on a taskListUpdate that leaves entries unchanged', () => {
    // Regression test: taskListUpdate/toggleTaskPanel used to share one useEffect with the
    // scroll-to-bottom call, so a taskListUpdate (which can arrive several times per turn, once
    // per TodoCreate/TodoUpdate/TodoNext call) re-scrolled the history on every one of them --
    // fighting the browser's own attempt to keep a focused element elsewhere on the page (e.g.
    // the task panel's own <summary>) in view, which visibly read as the history freezing until
    // focus moved away. Scrolling should only follow an actual entries/pendingInteraction/status
    // change now.
    const { vscode } = makeVsCode();
    render(<App vscode={vscode} initialEntries={[]} />);
    mockScrollToBottomIfPinned.mockClear();

    postHostMessage({
      type: 'taskListUpdate',
      summary: { openCount: 1, closedCount: 0, blockedCount: 0, currentTaskId: null },
      tasks: [
        {
          text: 'Investigate',
          priority: 'medium',
          status: 'pending',
          blocked: false,
          isCurrentTask: false,
          closed: false,
        },
      ],
    });
    expect(mockScrollToBottomIfPinned).not.toHaveBeenCalled();

    postHostMessage({ type: 'toggleTaskPanel' });
    expect(mockScrollToBottomIfPinned).not.toHaveBeenCalled();

    postHostMessage({ type: 'agentChunk', text: 'hi' });
    expect(mockScrollToBottomIfPinned).toHaveBeenCalledOnce();
  });

  it('clears the history on sessionReset', () => {
    const { vscode } = makeVsCode();
    render(
      <App
        vscode={vscode}
        initialEntries={[{ kind: 'prompt', text: 'old prompt', streaming: false, id: 'p1' }]}
      />
    );

    expect(screen.getByText('old prompt')).toBeTruthy();
    postHostMessage({ type: 'sessionReset' });
    expect(screen.queryByText('old prompt')).toBeNull();
  });

  it(
    'keeps the input enabled and posts enqueueMessage for a mid-turn submit when the ' +
      'server advertises the capability',
    () => {
      const { vscode, posted } = makeVsCode();
      const { container } = render(<App vscode={vscode} initialEntries={[]} />);
      postHostMessage({ type: 'statusUpdate', enqueueMessageCapable: true });

      typeAndSubmit(container, 'long task');
      expect(promptTextarea(container).hasAttribute('disabled')).toBe(false);

      typeAndSubmit(container, 'also check the tests');

      expect(posted).toContainEqual({ type: 'enqueueMessage', text: 'also check the tests' });
      expect(posted).not.toContainEqual({ type: 'submitPrompt', text: 'also check the tests' });
    }
  );

  it('falls back to a disabled input for a mid-turn submit without the capability', () => {
    const { vscode, posted } = makeVsCode();
    const { container } = render(<App vscode={vscode} initialEntries={[]} />);

    typeAndSubmit(container, 'long task');
    expect(promptTextarea(container).hasAttribute('disabled')).toBe(true);

    // The textarea is disabled, so a second Enter (even if fired anyway) submits nothing new.
    typeAndSubmit(container, 'also check the tests');
    // `posted` also carries the mount-time `setSubagentsPanelVisible` resync message (see
    // App.tsx's own mount-resync effect) -- unrelated to this test's own submit-behavior focus.
    expect(
      posted.filter((message) => (message as { type: string }).type === 'submitPrompt')
    ).toEqual([{ type: 'submitPrompt', text: 'long task' }]);
  });

  it('renders a messageQueued entry in queued styling and flips it on queuedMessageSent', () => {
    const { vscode } = makeVsCode();
    render(<App vscode={vscode} initialEntries={[]} />);

    postHostMessage({ type: 'messageQueued', text: 'also check the tests' });
    expect(screen.getByText('Queued message')).toBeTruthy();
    expect(screen.getByText('also check the tests')).toBeTruthy();

    postHostMessage({ type: 'queuedMessageSent', text: 'also check the tests' });
    expect(screen.queryByText('Queued message')).toBeNull();
    expect(screen.getByText('also check the tests')).toBeTruthy();
  });

  it('renders an (interrupted) marker on a streaming response for a cancelled turn', () => {
    const { vscode } = makeVsCode();
    const { container } = render(<App vscode={vscode} initialEntries={[]} />);

    typeAndSubmit(container, 'long task');
    postHostMessage({ type: 'agentChunk', text: 'partial reply' });
    postHostMessage({ type: 'turnEnded', stopReason: 'cancelled' });

    expect(screen.getByText(/partial reply/)).toBeTruthy();
    expect(screen.getByText(/\(interrupted\)/)).toBeTruthy();
  });

  it('renders a serverLost entry with a Restart Server action that posts restartServer', () => {
    const { vscode, posted } = makeVsCode();
    render(<App vscode={vscode} initialEntries={[]} />);

    postHostMessage({ type: 'serverLost', message: 'klorb server exited unexpectedly' });
    expect(screen.getByText('klorb server exited unexpectedly')).toBeTruthy();

    fireEvent.click(screen.getByText('Restart Server'));
    expect(posted).toContainEqual({ type: 'restartServer' });
  });

  describe('subagents panel', () => {
    const TREE_NODES = [
      {
        id: 'root-1',
        parentId: null,
        address: '1',
        title: null,
        role: 'operator',
        state: null,
        aborted: false,
        model: 'anthropic/claude-sonnet-5',
        thinkingEnabled: false,
        thinkingEffort: 'medium' as const,
        usedTokens: 0,
        maxTokens: 128000,
        outputTokens: 0,
      },
      {
        id: 'subagent-1',
        parentId: 'root-1',
        address: '1.1',
        title: 'find the bug',
        role: 'explorer',
        state: 'running' as const,
        aborted: false,
        model: 'moonshotai/kimi-k2.7-code',
        thinkingEnabled: true,
        thinkingEffort: 'high' as const,
        usedTokens: 500,
        maxTokens: null,
        outputTokens: 50,
      },
    ];

    it('opens the subagents panel automatically on a CreateSubagent tool call', () => {
      const { vscode, posted } = makeVsCode();
      render(<App vscode={vscode} initialEntries={[]} />);
      posted.length = 0;

      postHostMessage({
        type: 'toolCallStarted',
        callId: 'call-1',
        title: 'Create subagent',
        kind: 'other',
        locations: [],
        toolName: 'CreateSubagent',
      });

      expect(posted).toContainEqual({ type: 'setSubagentsPanelVisible', visible: true });
      postHostMessage({ type: 'subagentTreeUpdate', nodes: TREE_NODES });
      expect(screen.getByText('Subagents')).toBeTruthy();
    });

    it('does not re-toggle an already-visible subagents panel on a later CreateSubagent call', () => {
      const { vscode, posted } = makeVsCode();
      render(<App vscode={vscode} initialEntries={[]} />);
      postHostMessage({ type: 'toggleSubagentsPanel' });
      posted.length = 0;

      postHostMessage({
        type: 'toolCallStarted',
        callId: 'call-1',
        title: 'Create subagent',
        kind: 'other',
        locations: [],
        toolName: 'CreateSubagent',
      });

      expect(posted).not.toContainEqual(
        expect.objectContaining({ type: 'setSubagentsPanelVisible' })
      );
      postHostMessage({ type: 'subagentTreeUpdate', nodes: TREE_NODES });
      expect(screen.getByText('Subagents')).toBeTruthy();
    });

    it('shows the fallback attention bar when a subagent ask arrives while the panel is hidden', () => {
      const { vscode, posted } = makeVsCode();
      render(<App vscode={vscode} initialEntries={[]} />);

      postHostMessage({ type: 'subagentTreeUpdate', nodes: TREE_NODES });
      postHostMessage({
        type: 'permissionAsk',
        requestId: 1,
        title: '[subagent 1.1 (explorer)] Run: ls',
        options: [{ id: 'allow:once', name: 'Allow once', kind: 'allow_once' }],
        klorbMeta: { resourceDescription: 'run shell command: ls' },
        originSessionId: 'subagent-1',
      });

      expect(screen.getByText('Agent 1.1 needs your input')).toBeTruthy();
      // The ask itself isn't shown -- the root session (the current selection) doesn't own it.
      expect(screen.queryByText('run shell command: ls')).toBeNull();

      fireEvent.click(screen.getByText('Agent 1.1 needs your input'));

      expect(posted).toContainEqual({ type: 'setSubagentsPanelVisible', visible: true });
      expect(posted).toContainEqual({ type: 'selectSubagent', sessionId: 'subagent-1' });
      // Selecting the owning session surfaces the ask and clears the fallback bar.
      expect(screen.getByText('run shell command: ls')).toBeTruthy();
      expect(screen.queryByText('Agent 1.1 needs your input')).toBeNull();
    });

    it('keeps the prompt input enabled once a subagent row is selected', () => {
      const { vscode } = makeVsCode();
      const { container } = render(<App vscode={vscode} initialEntries={[]} />);

      postHostMessage({ type: 'subagentTreeUpdate', nodes: TREE_NODES });
      postHostMessage({ type: 'toggleSubagentsPanel' });
      fireEvent.click(screen.getByText('find the bug'));

      // Selecting a subagent no longer disables the input -- submitting addresses it directly
      // instead of the root session (see docs/specs/vscode-plugin.md's "Subagents panel"
      // section).
      expect(promptTextarea(container).hasAttribute('disabled')).toBe(false);
    });

    it('submitting while a subagent is selected posts submitPrompt with its subagentId', () => {
      const { vscode, posted } = makeVsCode();
      const { container } = render(<App vscode={vscode} initialEntries={[]} />);

      postHostMessage({ type: 'subagentTreeUpdate', nodes: TREE_NODES });
      postHostMessage({ type: 'toggleSubagentsPanel' });
      fireEvent.click(screen.getByText('find the bug'));
      posted.length = 0;

      typeAndSubmit(container, 'steer the subagent');

      expect(posted).toEqual([
        { type: 'submitPrompt', text: 'steer the subagent', subagentId: 'subagent-1' },
      ]);
      // Neither the root session's own turn state nor its history is touched by this submit.
      expect(screen.queryByText('steer the subagent')).toBeNull();
    });

    it('resets the selection back to root on sessionReset', () => {
      const { vscode, posted } = makeVsCode();
      render(<App vscode={vscode} initialEntries={[]} />);

      postHostMessage({ type: 'subagentTreeUpdate', nodes: TREE_NODES });
      postHostMessage({ type: 'toggleSubagentsPanel' });
      fireEvent.click(screen.getByText('find the bug'));
      posted.length = 0;

      postHostMessage({ type: 'sessionReset' });

      expect(posted).toContainEqual({ type: 'selectSubagent', sessionId: null });
    });
  });
});
