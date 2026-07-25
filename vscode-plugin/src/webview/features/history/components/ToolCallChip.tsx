// © Copyright 2026 Aaron Kimball
import type { JSX } from 'react';

import type { DiffHunk, OpenDiffMessage, OpenLocationMessage } from 'shared/webviewMessages';
import useVsCodeApi from 'webview/hooks/useVsCodeApi';

import type { ToolCallHistoryEntry } from '../historyModel';
import { renderDiffLines } from '../renderDiffLines';

/** ACP `ToolKind` (see docs/specs/klorb-server.md's `TOOL_KIND_MAP`) to the codicon shown in a
 * chip's collapsed row -- a kind this table doesn't cover (a future `ToolKind` klorb's own
 * server doesn't emit yet) falls back to the generic `tools` icon at lookup time. */
const KIND_ICON: Record<string, string> = {
  read: 'book',
  edit: 'edit',
  search: 'search',
  execute: 'terminal',
  fetch: 'globe',
  think: 'checklist',
  delete: 'trash',
  other: 'tools',
};

function iconForKind(kind: string): string {
  return KIND_ICON[kind] ?? 'tools';
}

interface DiffLinesProps {
  hunks: DiffHunk[];
}

function DiffLines({ hunks }: DiffLinesProps): JSX.Element {
  const rows = renderDiffLines(hunks);
  return (
    <div className="tool-call-diff">
      {rows.map((row, index) =>
        row.kind === 'hunk-separator' ? (
          <div className="diff-row diff-hunk-separator" key={index}>
            {'⋮'}
          </div>
        ) : (
          <div className={`diff-row diff-row-${row.kind}`} key={index}>
            <span className="diff-gutter diff-gutter-old">{row.oldLineno ?? ''}</span>
            <span className="diff-gutter diff-gutter-new">{row.newLineno ?? ''}</span>
            <span className="diff-text">{row.text}</span>
          </div>
        )
      )}
    </div>
  );
}

export interface ToolCallChipProps {
  entry: ToolCallHistoryEntry;
  onToggleExpanded(callId: string): void;
}

/**
 * One tool-call chip: a collapsed row (kind icon or a busy/error indicator, title, and an
 * expand chevron), and — when expanded — its content detail or diff view. The title becomes a
 * button posting `openLocation` when the call named a file location; a diff additionally gets
 * an "Open diff" button posting `openDiff`, so the extension host can show a real editor diff
 * reconstructed from the server's before/after text (see src/host/editorIntegration.ts).
 */
export default function ToolCallChip({ entry, onToggleExpanded }: ToolCallChipProps): JSX.Element {
  const vscode = useVsCodeApi();

  function openLocation(): void {
    const location = entry.locations[0];
    if (location === undefined) {
      return;
    }
    const message: OpenLocationMessage = {
      type: 'openLocation',
      path: location.path,
      line: location.line,
    };
    vscode.postMessage(message);
  }

  function openDiff(): void {
    if (entry.diff === undefined) {
      return;
    }
    const message: OpenDiffMessage = {
      type: 'openDiff',
      callId: entry.callId,
      path: entry.diff.path,
    };
    vscode.postMessage(message);
  }

  const hasLocation = entry.locations.length > 0;

  return (
    <div className={`tool-call tool-call-${entry.status}`}>
      <div className="tool-call-header">
        {entry.status === 'in_progress' ? (
          <vscode-progress-ring className="tool-call-icon" />
        ) : entry.status === 'failed' ? (
          <vscode-icon className="tool-call-icon tool-call-icon-error" name="error" />
        ) : (
          <vscode-icon className="tool-call-icon" name={iconForKind(entry.toolKind)} />
        )}
        {hasLocation ? (
          <button type="button" className="tool-call-title" onClick={openLocation}>
            {entry.title}
          </button>
        ) : (
          <span className="tool-call-title">{entry.title}</span>
        )}
        <vscode-icon
          className="tool-call-toggle"
          name={entry.expanded ? 'chevron-down' : 'chevron-right'}
          label={entry.expanded ? 'Collapse detail' : 'Expand detail'}
          actionIcon
          onClick={() => onToggleExpanded(entry.callId)}
        />
      </div>
      {entry.expanded ? (
        <div className="tool-call-detail">
          {entry.diff !== undefined ? (
            <>
              {entry.diff.hunks !== undefined ? (
                <DiffLines hunks={entry.diff.hunks} />
              ) : (
                <pre className="tool-call-diff-plain">{entry.diff.newText}</pre>
              )}
              <vscode-button secondary onClick={openDiff}>
                Open diff
              </vscode-button>
            </>
          ) : (
            entry.contentText !== undefined && (
              <div className="tool-call-content-text">{entry.contentText}</div>
            )
          )}
        </div>
      ) : null}
    </div>
  );
}
