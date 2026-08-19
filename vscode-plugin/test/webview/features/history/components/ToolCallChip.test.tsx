/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ToolCallChip } from 'webview/features/history';
import type { ToolCallHistoryEntry } from 'webview/features/history';

// Mock useVsCodeApi so the component doesn't crash outside VS Code.
vi.mock('webview/hooks/useVsCodeApi', () => ({
  default: () => ({ postMessage: vi.fn() }),
}));

afterEach(cleanup);

function makeEntry(overrides: Partial<ToolCallHistoryEntry> = {}): ToolCallHistoryEntry {
  return {
    kind: 'toolCall',
    id: 'call-1',
    callId: 'call-1',
    status: 'completed',
    title: 'ReadFile',
    toolKind: 'read',
    locations: [],
    expanded: true,
    ...overrides,
  };
}

describe('ToolCallChip ReadFile rendering', () => {
  it('renders ReadFile content with line numbers when readFileMeta is present', () => {
    const entry = makeEntry({
      readFileMeta: {
        filename: 'hello.txt',
        content: '1|first line\n2|second line\n3|third line',
      },
    });
    render(<ToolCallChip entry={entry} onToggleExpanded={vi.fn()} />);
    expect(screen.getByText('1')).toBeTruthy();
    expect(screen.getByText('first line')).toBeTruthy();
    expect(screen.getByText('2')).toBeTruthy();
    expect(screen.getByText('second line')).toBeTruthy();
  });

  it('falls back to contentText when readFileMeta is absent', () => {
    const entry = makeEntry({
      contentText: 'some raw content',
    });
    render(<ToolCallChip entry={entry} onToggleExpanded={vi.fn()} />);
    expect(screen.getByText('some raw content')).toBeTruthy();
  });

  it('handles content lines that contain pipe characters', () => {
    const entry = makeEntry({
      readFileMeta: {
        filename: 'code.py',
        content: '5|x = a | b',
      },
    });
    render(<ToolCallChip entry={entry} onToggleExpanded={vi.fn()} />);
    expect(screen.getByText('5')).toBeTruthy();
    expect(screen.getByText('x = a | b')).toBeTruthy();
  });
});
