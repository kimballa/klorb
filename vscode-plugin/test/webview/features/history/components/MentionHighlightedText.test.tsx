/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { MentionHighlightedText } from 'webview/features/history';

afterEach(cleanup);

describe('MentionHighlightedText', () => {
  it('renders plain text with no mentions unchanged, with no chip element', () => {
    const { container } = render(<MentionHighlightedText text="no mentions here" />);
    expect(container.textContent).toBe('no mentions here');
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    expect(container.querySelectorAll('.mention-chip')).toHaveLength(0);
  });

  it('wraps a mention in a .mention-chip span, leaving surrounding text outside it', () => {
    const { container } = render(<MentionHighlightedText text="check @foo.txt please" />);
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const chips = container.querySelectorAll('.mention-chip');
    expect(chips).toHaveLength(1);
    expect(chips[0]?.textContent).toBe('@foo.txt');
    expect(container.textContent).toBe('check @foo.txt please');
  });

  it('excludes trailing sentence punctuation from the highlighted chip', () => {
    const { container } = render(<MentionHighlightedText text="see @foo.txt. thanks" />);
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const chips = container.querySelectorAll('.mention-chip');
    expect(chips).toHaveLength(1);
    expect(chips[0]?.textContent).toBe('@foo.txt');
    expect(container.textContent).toBe('see @foo.txt. thanks');
  });

  it('highlights multiple mentions independently', () => {
    const { container } = render(<MentionHighlightedText text="@a.txt and @b.txt" />);
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const chips = container.querySelectorAll('.mention-chip');
    expect(chips).toHaveLength(2);
    expect(chips[0]?.textContent).toBe('@a.txt');
    expect(chips[1]?.textContent).toBe('@b.txt');
  });

  it('includes the surrounding quotes when highlighting a quoted mention', () => {
    const { container } = render(<MentionHighlightedText text='see @"foo bar.txt" now' />);
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const chips = container.querySelectorAll('.mention-chip');
    expect(chips).toHaveLength(1);
    expect(chips[0]?.textContent).toBe('@"foo bar.txt"');
  });
});
