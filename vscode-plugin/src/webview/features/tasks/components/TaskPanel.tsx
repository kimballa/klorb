// © Copyright 2026 Aaron Kimball
import { useState, type JSX, type MouseEvent, type ToggleEvent } from 'react';

import type { TaskInfo } from 'shared/webviewMessages';
import type { TaskListSnapshot } from 'webview/features/history';

export interface TaskPanelProps {
  taskList: TaskListSnapshot | undefined;
  onToggleVisibility(): void;
}

/** `"#<id> – <title>"` for the current task, reusing `task.text`'s own `"#<id> <title>"`
 * formatting (see docs/specs/klorb-server.md's "Chainlink task-plan updates" section) rather
 * than re-deriving the title -- `undefined` when no task is current, or (defensively) when
 * `currentTaskId` names no entry in `tasks` at all. */
function currentTaskLabel(taskList: TaskListSnapshot): string | undefined {
  const currentTaskId = taskList.summary.currentTaskId;
  if (currentTaskId === null) {
    return undefined;
  }
  const current = taskList.tasks.find((task) => task.issueId === currentTaskId);
  if (current === undefined) {
    return `#${currentTaskId}`;
  }
  const spaceIndex = current.text.indexOf(' ');
  const title = spaceIndex === -1 ? current.text : current.text.slice(spaceIndex + 1);
  return `#${currentTaskId} – ${title}`;
}

/** The header row's one-line summary, built from the snapshot's counts -- "Tasks: none" for a
 * plan update with zero entries (distinct from no update having arrived at all, which shows "No
 * tasks available" instead -- see `TaskPanel`'s own doc comment). The leading "Tasks: `<n>` open"
 * clause renders bold when there's open work to call out (`openCount !== 0`); the trailing
 * blocked-count/current-task clauses stay normal weight. Split into a real component (rather than
 * building one string) specifically so the bold clause can be its own element without losing the
 * rest of the line's plain-text styling. */
function TaskPanelSummaryText({ taskList }: { taskList: TaskListSnapshot }): JSX.Element {
  if (taskList.tasks.length === 0) {
    return <>Tasks: none</>;
  }
  const { summary } = taskList;
  const rest: string[] = [];
  if (summary.blockedCount > 0) {
    rest.push(`${summary.blockedCount} blocked`);
  }
  const currentLabel = currentTaskLabel(taskList);
  if (currentLabel !== undefined) {
    rest.push(currentLabel);
  }
  return (
    <>
      <span className={summary.openCount !== 0 ? 'task-panel-summary-headline' : undefined}>
        Tasks: {summary.openCount} open
      </span>
      {rest.length > 0 ? ` · ${rest.join(' · ')}` : null}
    </>
  );
}

/** Renders `task.isCurrentTask`'s star in its own fixed-width column (`.task-star`, always
 * present even when empty) so every row's title lines up at the same starting column, whether
 * or not that row happens to be starred. */
function TaskRow({ task }: { task: TaskInfo }): JSX.Element {
  return (
    <div className={`task-row${task.closed ? ' task-row-closed' : ''}`}>
      <span className="task-star">{task.isCurrentTask ? '★' : ''}</span>
      <span className="task-text">
        {task.text}
        {task.blocked ? <span className="task-blocked"> (blocked)</span> : null}
      </span>
    </div>
  );
}

const EMPTY_TASK_LIST: TaskInfo[] = [];

/**
 * The collapsible strip docked at the top of the sidebar rendering the session's chainlink-
 * backed task list -- the tall-narrow adaptation of the TUI's Ctrl+T right-hand sidebar (see
 * docs/specs/vscode-plugin.md). A plain `<details>`/`<summary>` disclosure (mirroring the
 * thinking block/approval "Show full command" elsewhere in this panel) gives click/Enter
 * expand-collapse for free; its own open/closed state is not persisted, unlike the panel's own
 * shown/hidden state, which `App` tracks
 * and persists. The leading `.task-panel-chevron` icon (a fixed `chevron-right` glyph, rotated
 * 90° via `.task-panel[open]` CSS rather than swapped to a different icon name) is what actually
 * signals expand/collapse state, since the native `<details>` marker triangle is suppressed by
 * `.task-panel-summary`'s own `display: flex` (needed for the chevron/icon/text/pin row layout
 * regardless). Shows a static "No tasks available" summary (no expand affordance, same as a
 * zero-entry `"Tasks: none"` update) before the server's first `plan` update arrives
 * (`taskList === undefined`) -- there is no client-side chainlink access to derive a real
 * placeholder from (see docs/specs/klorb-server.md's "Chainlink task-plan updates" section), but
 * the panel itself always renders once opened, so a user who explicitly asks to see it isn't met
 * with nothing at all.
 */
export default function TaskPanel({ taskList, onToggleVisibility }: TaskPanelProps): JSX.Element {
  const [detailsOpen, setDetailsOpen] = useState<boolean>(false);

  function hidePanel(event: MouseEvent): void {
    // Cancels <summary>'s own default click action (toggling the parent <details>), so hiding
    // the panel doesn't also flip its expand/collapse state.
    event.preventDefault();
    onToggleVisibility();
  }

  function onToggleDetails(event: ToggleEvent<HTMLDetailsElement>): void {
    setDetailsOpen(event.currentTarget.open);
  }

  const tasks: TaskInfo[] = taskList?.tasks || EMPTY_TASK_LIST;
  const hasTasks: boolean = !!(tasks?.length > 0);

  function onClickSummary(event: MouseEvent): void {
    if (!hasTasks && !detailsOpen) {
      // No tasks - don't allow the task list to be expanded.
      event.preventDefault();
    }
  }

  const summaryTitle: string | undefined = detailsOpen
    ? 'Hide task tracking details'
    : hasTasks
      ? 'Show detailed task tracking'
      : undefined;

  return (
    <details className="task-panel" onToggle={onToggleDetails}>
      <summary className="task-panel-summary" onClick={onClickSummary} title={summaryTitle}>
        {hasTasks && <vscode-icon className="task-panel-chevron" name="chevron-right" />}
        <vscode-icon className="task-panel-icon" name="checklist" />
        <span className="task-panel-summary-text">
          {taskList === undefined ? (
            'No tasks available'
          ) : (
            <TaskPanelSummaryText taskList={taskList} />
          )}
        </span>
        <vscode-icon
          className="task-panel-pin"
          name="pin"
          label="Hide task panel"
          title="Unpin task panel"
          actionIcon
          onClick={hidePanel}
        />
      </summary>
      <div className="task-panel-list">
        {tasks.map((task, index) => (
          <TaskRow task={task} key={task.issueId ?? index} />
        ))}
      </div>
    </details>
  );
}
