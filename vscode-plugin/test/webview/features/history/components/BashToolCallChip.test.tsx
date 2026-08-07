/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { BashToolCallChip } from 'webview/features/history';
import type { ToolCallHistoryEntry } from 'webview/features/history';

// Mock useVsCodeApi so the component doesn't crash outside VS Code.
vi.mock('webview/hooks/useVsCodeApi', () => ({
  default: () => ({ postMessage: vi.fn() }),
}));

afterEach(cleanup);

function makeEntry(overrides: Partial<ToolCallHistoryEntry> = {}): ToolCallHistoryEntry {
  return {
    kind: 'toolCall',
    callId: 'call-1',
    status: 'completed',
    title: 'Bash',
    toolKind: 'execute',
    locations: [],
    bashMeta: { command: 'echo hello' },
    expanded: true,
    ...overrides,
  };
}

describe('BashToolCallChip', () => {
  it('renders stdout and stderr from bashMeta when expanded', () => {
    const entry = makeEntry({
      bashMeta: {
        command: 'echo hello',
        success: true,
        exitStatus: 0,
        runtime: 0.5,
        stdout: 'hello world',
        stderr: 'warning: something',
      },
    });
    render(<BashToolCallChip entry={entry} onToggleExpanded={vi.fn()} />);
    expect(screen.getByText('stdout')).toBeTruthy();
    expect(screen.getByText('hello world')).toBeTruthy();
    expect(screen.getByText('stderr')).toBeTruthy();
    expect(screen.getByText('warning: something')).toBeTruthy();
  });

  it('falls back to contentText when bashMeta has no stdout/stderr', () => {
    const entry = makeEntry({
      bashMeta: { command: 'echo hi' },
      contentText: 'raw json detail',
    });
    render(<BashToolCallChip entry={entry} onToggleExpanded={vi.fn()} />);
    expect(screen.getByText('raw json detail')).toBeTruthy();
  });

  it('falls back to contentText when stdout and stderr are both empty', () => {
    const entry = makeEntry({
      bashMeta: { command: 'cd /tmp', success: true, stdout: '', stderr: '' },
      contentText: 'raw json detail',
    });
    render(<BashToolCallChip entry={entry} onToggleExpanded={vi.fn()} />);
    expect(screen.getByText('raw json detail')).toBeTruthy();
  });

  it('omits stdout section when stdout is empty', () => {
    const entry = makeEntry({
      bashMeta: {
        command: 'test',
        success: true,
        stdout: '',
        stderr: 'some error',
      },
    });
    render(<BashToolCallChip entry={entry} onToggleExpanded={vi.fn()} />);
    expect(screen.queryByText('stdout')).toBeNull();
    expect(screen.getByText('stderr')).toBeTruthy();
  });
});
