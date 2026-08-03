/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { TaskInfo } from 'shared/webviewMessages';
import type { TaskListSnapshot } from 'webview/features/history';
import { TaskPanel } from 'webview/features/tasks';

afterEach(cleanup);

function task(overrides: Partial<TaskInfo> = {}): TaskInfo {
  return {
    issueId: 12,
    text: '#12 Fix the bug',
    priority: 'high',
    status: 'pending',
    blocked: false,
    isCurrentTask: false,
    closed: false,
    ...overrides,
  };
}

/** The summary line's full text, and whether its leading "Tasks: N open" clause is bold -- a
 * direct `container.querySelector` (rather than `screen.getByText`) since the bold headline is
 * a nested `<span>` whose own text can equal the whole line's text when there's no trailing
 * clause, which would otherwise make `getByText` match both the inner and outer element. */
function summaryLine(container: HTMLElement): { text: string | null; headlineBold: boolean } {
  // eslint-disable-next-line testing-library/no-node-access
  const summaryText = container.querySelector('.task-panel-summary-text');
  // eslint-disable-next-line testing-library/no-node-access
  const headline = container.querySelector('.task-panel-summary-headline');
  return { text: summaryText?.textContent ?? null, headlineBold: headline !== null };
}

describe('TaskPanel', () => {
  it('shows "No tasks available" until a plan update has arrived, with no expand affordance', () => {
    const { container } = render(
      <TaskPanel taskList={undefined} onToggleVisibility={() => undefined} />
    );

    expect(summaryLine(container)).toEqual({ text: 'No tasks available', headlineBold: false });
    // No chevron -- there's nothing to expand into, mirroring the zero-entry "Tasks: none" case.
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const summary = container.querySelector('.task-panel-summary');

    expect(summary?.firstElementChild?.className).toBe('task-panel-icon');
  });

  it('shows "Tasks: none" for a plan update with zero entries, not bold', () => {
    const taskList: TaskListSnapshot = {
      summary: { openCount: 0, closedCount: 0, blockedCount: 0, currentTaskId: null },
      tasks: [],
    };
    const { container } = render(
      <TaskPanel taskList={taskList} onToggleVisibility={() => undefined} />
    );

    expect(summaryLine(container)).toEqual({ text: 'Tasks: none', headlineBold: false });
  });

  it("builds the summary line from open/blocked counts and the current task's id and title", () => {
    const taskList: TaskListSnapshot = {
      summary: { openCount: 3, closedCount: 1, blockedCount: 1, currentTaskId: 12 },
      tasks: [task({ text: '#12 Fix the bug' })],
    };
    const { container } = render(
      <TaskPanel taskList={taskList} onToggleVisibility={() => undefined} />
    );

    expect(summaryLine(container)).toEqual({
      text: 'Tasks: 3 open · 1 blocked · #12 – Fix the bug',
      headlineBold: true,
    });
  });

  it('falls back to the bare id when currentTaskId names no entry in tasks', () => {
    const taskList: TaskListSnapshot = {
      summary: { openCount: 1, closedCount: 0, blockedCount: 0, currentTaskId: 99 },
      tasks: [task({ issueId: 12 })],
    };
    const { container } = render(
      <TaskPanel taskList={taskList} onToggleVisibility={() => undefined} />
    );

    expect(summaryLine(container).text).toBe('Tasks: 1 open · #99');
  });

  it('omits the blocked/current-task clauses when there is nothing to report', () => {
    const taskList: TaskListSnapshot = {
      summary: { openCount: 2, closedCount: 0, blockedCount: 0, currentTaskId: null },
      tasks: [task()],
    };
    const { container } = render(
      <TaskPanel taskList={taskList} onToggleVisibility={() => undefined} />
    );

    expect(summaryLine(container)).toEqual({ text: 'Tasks: 2 open', headlineBold: true });
  });

  it('does not bold the headline when openCount is 0, even with tasks present', () => {
    const taskList: TaskListSnapshot = {
      summary: { openCount: 0, closedCount: 1, blockedCount: 0, currentTaskId: null },
      tasks: [task({ status: 'completed', closed: true })],
    };
    const { container } = render(
      <TaskPanel taskList={taskList} onToggleVisibility={() => undefined} />
    );

    expect(summaryLine(container)).toEqual({ text: 'Tasks: 0 open', headlineBold: false });
  });

  it('renders a chevron icon at the start of the summary row', () => {
    const taskList: TaskListSnapshot = {
      summary: { openCount: 1, closedCount: 0, blockedCount: 0, currentTaskId: null },
      tasks: [task()],
    };
    const { container } = render(
      <TaskPanel taskList={taskList} onToggleVisibility={() => undefined} />
    );

    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const summary = container.querySelector('.task-panel-summary');
    // eslint-disable-next-line testing-library/no-node-access
    const chevron = summary?.firstElementChild;
    expect(chevron?.className).toBe('task-panel-chevron');
    expect(chevron?.getAttribute('name')).toBe('chevron-right');
  });

  it('stars the current task, strikes closed tasks, and flags blocked ones', () => {
    const taskList: TaskListSnapshot = {
      summary: { openCount: 2, closedCount: 1, blockedCount: 1, currentTaskId: 12 },
      tasks: [
        task({ issueId: 12, text: '#12 Fix the bug', isCurrentTask: true }),
        task({ issueId: 13, text: '#13 Write docs', blocked: true }),
        task({ issueId: 11, text: '#11 Old task', status: 'completed', closed: true }),
      ],
    };
    const { container } = render(
      <TaskPanel taskList={taskList} onToggleVisibility={() => undefined} />
    );

    expect(screen.getByText('#12 Fix the bug')).toBeTruthy();
    expect(screen.getByText('★', { exact: false })).toBeTruthy();
    expect(screen.getByText('#13 Write docs')).toBeTruthy();
    expect(screen.getByText('(blocked)', { exact: false })).toBeTruthy();
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const closedRow = container.querySelector('.task-row-closed');
    expect(closedRow?.textContent).toContain('#11 Old task');
  });

  it('renders a star-column span for every row, even ones that are not starred', () => {
    const taskList: TaskListSnapshot = {
      summary: { openCount: 2, closedCount: 0, blockedCount: 0, currentTaskId: 12 },
      tasks: [
        task({ issueId: 12, text: '#12 Fix the bug', isCurrentTask: true }),
        task({ issueId: 13, text: '#13 Write docs' }),
      ],
    };
    const { container } = render(
      <TaskPanel taskList={taskList} onToggleVisibility={() => undefined} />
    );

    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const stars = container.querySelectorAll('.task-star');
    expect(stars).toHaveLength(2);
    expect(stars[0]?.textContent).toBe('★');
    expect(stars[1]?.textContent).toBe('');
  });

  it('calls onToggleVisibility from the pin control without expanding the disclosure', () => {
    const onToggleVisibility = vi.fn();
    const taskList: TaskListSnapshot = {
      summary: { openCount: 1, closedCount: 0, blockedCount: 0, currentTaskId: null },
      tasks: [task()],
    };
    const { container } = render(
      <TaskPanel taskList={taskList} onToggleVisibility={onToggleVisibility} />
    );

    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const pin = container.querySelector('.task-panel-pin') as Element;
    fireEvent.click(pin);

    expect(onToggleVisibility).toHaveBeenCalledOnce();
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    expect(container.querySelector('details')?.hasAttribute('open')).toBe(false);
  });

  it('toggles the disclosure open when the summary is clicked', () => {
    const taskList: TaskListSnapshot = {
      summary: { openCount: 1, closedCount: 0, blockedCount: 0, currentTaskId: null },
      tasks: [task()],
    };
    const { container } = render(
      <TaskPanel taskList={taskList} onToggleVisibility={() => undefined} />
    );

    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const details = container.querySelector('details') as HTMLDetailsElement;
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const summary = container.querySelector('.task-panel-summary') as Element;
    expect(details.open).toBe(false);
    fireEvent.click(summary);
    expect(details.open).toBe(true);
  });
});
