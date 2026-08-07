// © Copyright 2026 Aaron Kimball
import type { JSX } from 'react';

import type { OpenDiffMessage, OpenLocationMessage } from 'shared/webviewMessages';
import useVsCodeApi from 'webview/hooks/useVsCodeApi';

import type { ToolCallHistoryEntry } from '../historyModel';
import { renderDiffLines } from '../renderDiffLines';

export interface BashToolCallChipProps {
  entry: ToolCallHistoryEntry;
  onToggleExpanded(callId: string): void;
}

/** Renders the status icon for the bash tool call header: a progress ring while running,
 * an error icon on failure, or the terminal icon on success. */
function StatusIcon({ status }: { status: ToolCallHistoryEntry['status'] }): JSX.Element {
  if (status === 'in_progress') {
    return <vscode-progress-ring className="tool-call-icon" />;
  }
  if (status === 'failed') {
    return <vscode-icon className="tool-call-icon tool-call-icon-error" name="error" />;
  }
  return <vscode-icon className="tool-call-icon" name="terminal" />;
}

/** Format the result line shown after a bash command finishes: e.g. "(ok, 1.23s)" or
 * "(exit 1, 0.05s)". */
function formatResultLine(
  success: boolean | undefined,
  exitStatus: number | undefined,
  runtime: number | undefined
): string | undefined {
  if (success === undefined) {
    return undefined;
  }
  const statusText = success ? 'ok' : exitStatus !== undefined ? `exit ${exitStatus}` : 'failed';
  const runtimeText = runtime !== undefined ? `${runtime.toFixed(2)}s` : undefined;
  return runtimeText !== undefined ? `(${statusText}, ${runtimeText})` : `(${statusText})`;
}

/**
 * A dedicated chip for Bash tool calls that renders the command in monospace with
 * structured layout: the intent (when present) on its own line, then the command in
 * monospace, and after completion the exit status and runtime on a trailing line in
 * variable font. This replaces the generic `ToolCallChip`'s single-line title for
 * Bash calls only.
 */
export default function BashToolCallChip({
  entry,
  onToggleExpanded,
}: BashToolCallChipProps): JSX.Element {
  const vscode = useVsCodeApi();
  const meta = entry.bashMeta;

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
  const command = meta?.command ?? entry.title;
  const intent = meta?.intent;
  const resultLine = formatResultLine(meta?.success, meta?.exitStatus, meta?.runtime);

  return (
    <div className={`tool-call tool-call-${entry.status} bash-tool-call`}>
      <div className="tool-call-header">
        <StatusIcon status={entry.status} />
        <div className="bash-tool-call-title">
          {intent !== undefined && <span className="bash-tool-call-intent">{intent}</span>}
          {hasLocation ? (
            <button type="button" className="bash-tool-call-command" onClick={openLocation}>
              $&nbsp;{command}
            </button>
          ) : (
            <span className="bash-tool-call-command">$&nbsp;{command}</span>
          )}
          {resultLine !== undefined && <span className="bash-tool-call-result">{resultLine}</span>}
        </div>
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
                <div className="tool-call-diff">
                  {renderDiffLines(entry.diff.hunks).map((row, index) =>
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
              ) : (
                <pre className="tool-call-diff-plain">{entry.diff.newText}</pre>
              )}
              <vscode-button secondary onClick={openDiff}>
                Open diff
              </vscode-button>
            </>
          ) : (entry.bashMeta?.stdout !== undefined && entry.bashMeta.stdout.length > 0) ||
            (entry.bashMeta?.stderr !== undefined && entry.bashMeta.stderr.length > 0) ? (
            <div className="bash-tool-output">
              {entry.bashMeta.stdout !== undefined && entry.bashMeta.stdout.length > 0 && (
                <div className="bash-tool-output-section">
                  <div className="bash-tool-output-label">stdout</div>
                  <pre className="bash-tool-output-content">{entry.bashMeta.stdout}</pre>
                </div>
              )}
              {entry.bashMeta.stderr !== undefined && entry.bashMeta.stderr.length > 0 && (
                <div className="bash-tool-output-section">
                  <div className="bash-tool-output-label">stderr</div>
                  <pre className="bash-tool-output-content bash-tool-output-stderr">
                    {entry.bashMeta.stderr}
                  </pre>
                </div>
              )}
            </div>
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
