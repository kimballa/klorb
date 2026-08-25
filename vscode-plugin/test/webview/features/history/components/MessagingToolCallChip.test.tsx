/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { MessagingToolCallChip } from 'webview/features/history';
import type { ToolCallHistoryEntry } from 'webview/features/history';

afterEach(cleanup);

function makeEntry(overrides: Partial<ToolCallHistoryEntry> = {}): ToolCallHistoryEntry {
  return {
    kind: 'toolCall',
    id: 'call-1',
    callId: 'call-1',
    status: 'completed',
    title: 'Send message to agent child-1',
    toolKind: 'other',
    toolName: 'SendMessage',
    locations: [],
    expanded: false,
    ...overrides,
  };
}

/** Build a `default_tool_call_detail` JSON string for SendMessage. */
function sendDetailJson(
  args: Record<string, unknown> = { id: 'child-1', message: 'Hello there' },
  resultOrError?: { result?: unknown; error?: string }
): string {
  const payload: Record<string, unknown> = { name: 'SendMessage', args };
  if (resultOrError?.error !== undefined) {
    payload.error = resultOrError.error;
  } else {
    payload.result = resultOrError?.result ?? { status: 'delivered', target_id: 'child-1' };
  }
  return JSON.stringify(payload);
}

/** Build a `default_tool_call_detail` JSON string for GetMessages. */
function getDetailJson(
  messages: Array<{ sender_id: string; sender_role: string; body: string }> = [],
  parent_id: string | null = null
): string {
  return JSON.stringify({
    name: 'GetMessages',
    args: {},
    result: { messages, parent_id },
  });
}

describe('MessagingToolCallChip', () => {
  it('renders the title in a summary element', () => {
    const entry = makeEntry({ title: 'Send message to agent child-1' });
    render(<MessagingToolCallChip entry={entry} />);
    expect(screen.getByText('Send message to agent child-1')).toBeTruthy();
  });

  it('renders without error for completed status', () => {
    const entry = makeEntry({ status: 'completed' });
    render(<MessagingToolCallChip entry={entry} />);
    expect(screen.getByText('Send message to agent child-1')).toBeTruthy();
  });

  it('renders without error for failed status', () => {
    const entry = makeEntry({ status: 'failed' });
    render(<MessagingToolCallChip entry={entry} />);
    expect(screen.getByText('Send message to agent child-1')).toBeTruthy();
  });

  it('renders without error for in_progress status', () => {
    const entry = makeEntry({ status: 'in_progress' });
    render(<MessagingToolCallChip entry={entry} />);
    expect(screen.getByText('Send message to agent child-1')).toBeTruthy();
  });

  it('shows contentText inside the disclosure when present', () => {
    const entry = makeEntry({ contentText: 'some content' });
    render(<MessagingToolCallChip entry={entry} />);
    expect(screen.getByText('some content')).toBeTruthy();
  });

  describe('SendMessage detail parsing', () => {
    it('renders target id and message body from default_tool_call_detail JSON', () => {
      const entry = makeEntry({
        toolName: 'SendMessage',
        contentText: sendDetailJson({ id: 'child-1', message: 'Please review the PR' }),
      });
      render(<MessagingToolCallChip entry={entry} />);
      expect(screen.getByText('child-1')).toBeTruthy();
      expect(screen.getByText('Please review the PR')).toBeTruthy();
    });

    it('renders the "To:" label for the target', () => {
      const entry = makeEntry({
        toolName: 'SendMessage',
        contentText: sendDetailJson(),
      });
      render(<MessagingToolCallChip entry={entry} />);
      expect(screen.getByText(/To:/)).toBeTruthy();
    });

    it('renders error field when the detail has an error', () => {
      const entry = makeEntry({
        toolName: 'SendMessage',
        status: 'failed',
        contentText: sendDetailJson({ id: 'bad-id', message: 'hi' }, { error: 'No such agent' }),
      });
      render(<MessagingToolCallChip entry={entry} />);
      expect(screen.getByText('No such agent')).toBeTruthy();
    });

    it('shows "(unknown)" when args.id is missing', () => {
      const entry = makeEntry({
        toolName: 'SendMessage',
        contentText: sendDetailJson({ message: 'hi' }),
      });
      render(<MessagingToolCallChip entry={entry} />);
      expect(screen.getByText('(unknown)')).toBeTruthy();
    });
  });

  describe('GetMessages detail parsing', () => {
    it('renders message count and each message sender/role/body', () => {
      const messages = [
        { sender_id: 'child-1', sender_role: 'implementer', body: 'Done with task A' },
        { sender_id: 'child-2', sender_role: 'explorer', body: 'Found a bug in X' },
      ];
      const entry = makeEntry({
        toolName: 'GetMessages',
        title: 'Get messages (2 unread)',
        contentText: getDetailJson(messages),
      });
      render(<MessagingToolCallChip entry={entry} />);
      expect(screen.getByText('2 messages')).toBeTruthy();
      expect(screen.getByText('child-1')).toBeTruthy();
      expect(screen.getByText('(implementer)')).toBeTruthy();
      expect(screen.getByText('Done with task A')).toBeTruthy();
      expect(screen.getByText('child-2')).toBeTruthy();
      expect(screen.getByText('(explorer)')).toBeTruthy();
      expect(screen.getByText('Found a bug in X')).toBeTruthy();
    });

    it('renders singular "message" for a single message', () => {
      const entry = makeEntry({
        toolName: 'GetMessages',
        title: 'Get messages (1 unread)',
        contentText: getDetailJson([
          { sender_id: 'agent-a', sender_role: 'worker', body: 'All done' },
        ]),
      });
      render(<MessagingToolCallChip entry={entry} />);
      expect(screen.getByText('1 message')).toBeTruthy();
    });

    it('renders "0 messages" for an empty result', () => {
      const entry = makeEntry({
        toolName: 'GetMessages',
        title: 'Get messages (none unread)',
        contentText: getDetailJson([]),
      });
      render(<MessagingToolCallChip entry={entry} />);
      expect(screen.getByText('0 messages')).toBeTruthy();
    });
  });

  describe('fallback rendering', () => {
    it('renders raw contentText when it is not valid JSON', () => {
      const entry = makeEntry({
        contentText: 'Message delivered to agent child-1; its next turn is now running.',
      });
      render(<MessagingToolCallChip entry={entry} />);
      expect(
        screen.getByText('Message delivered to agent child-1; its next turn is now running.')
      ).toBeTruthy();
    });

    it('renders raw contentText when JSON lacks name/args envelope', () => {
      const entry = makeEntry({
        contentText: JSON.stringify({ status: 'delivered', target_id: 'child-1' }),
      });
      render(<MessagingToolCallChip entry={entry} />);
      expect(screen.getByText(/"status":\s*"delivered"/)).toBeTruthy();
    });

    it('renders no detail content when contentText is undefined', () => {
      const entry = makeEntry({ contentText: undefined });
      render(<MessagingToolCallChip entry={entry} />);
      expect(screen.getByText('Send message to agent child-1')).toBeTruthy();
      expect(screen.queryByText('To:')).toBeNull();
      expect(screen.queryByText('messages')).toBeNull();
    });

    it('renders raw contentText for an unrecognized tool name in the envelope', () => {
      const entry = makeEntry({
        toolName: 'SendMessage',
        contentText: JSON.stringify({ name: 'UnknownTool', args: { x: 1 }, result: 'ok' }),
      });
      render(<MessagingToolCallChip entry={entry} />);
      expect(screen.getByText(/"name":\s*"UnknownTool"/)).toBeTruthy();
    });

    it('falls back to raw text when args is null', () => {
      const entry = makeEntry({
        contentText: JSON.stringify({ name: 'SendMessage', args: null, result: 'ok' }),
      });
      render(<MessagingToolCallChip entry={entry} />);
      expect(screen.getByText(/"args":\s*null/)).toBeTruthy();
    });
  });

  describe('malformed message resilience', () => {
    it('skips non-object entries in GetMessages messages array', () => {
      const rawJson = JSON.stringify({
        name: 'GetMessages',
        args: {},
        result: {
          messages: [
            'not an object',
            null,
            42,
            { sender_id: 'child-1', sender_role: 'worker', body: 'valid message' },
            { sender_id: 'child-2' },
          ],
        },
      });
      const entry = makeEntry({
        toolName: 'GetMessages',
        contentText: rawJson,
      });
      render(<MessagingToolCallChip entry={entry} />);
      expect(screen.getByText('1 message')).toBeTruthy();
      expect(screen.getByText('child-1')).toBeTruthy();
      expect(screen.getByText('valid message')).toBeTruthy();
      expect(screen.queryByText('child-2')).toBeNull();
    });
  });
});
