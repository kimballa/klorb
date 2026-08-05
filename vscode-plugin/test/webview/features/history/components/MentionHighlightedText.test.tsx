/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import VsCodeApiProvider from 'webview/components/VsCodeApiProvider';
import { MentionHighlightedText } from 'webview/features/history';

function makeVsCode(): {
  vscode: { postMessage: (msg: unknown) => void; setState: () => void; getState: () => void };
  posted: unknown[];
} {
  const posted: unknown[] = [];
  return {
    posted,
    vscode: {
      postMessage: (msg: unknown) => posted.push(msg),
      setState: () => undefined,
      getState: () => undefined,
    },
  };
}

afterEach(cleanup);

describe('MentionHighlightedText', () => {
  it('renders plain text with no mentions unchanged, with no chip element', () => {
    const { vscode } = makeVsCode();
    const { container } = render(
      <VsCodeApiProvider vscode={vscode}>
        <MentionHighlightedText text="no mentions here" />
      </VsCodeApiProvider>
    );
    expect(container.textContent).toBe('no mentions here');
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    expect(container.querySelectorAll('.mention-chip')).toHaveLength(0);
  });

  it('wraps a mention in a .mention-chip span, leaving surrounding text outside it', () => {
    const { vscode } = makeVsCode();
    const { container } = render(
      <VsCodeApiProvider vscode={vscode}>
        <MentionHighlightedText text="check @foo.txt please" />
      </VsCodeApiProvider>
    );
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const chips = container.querySelectorAll('.mention-chip');
    expect(chips).toHaveLength(1);
    expect(chips[0]?.textContent).toBe('@foo.txt');
    expect(container.textContent).toBe('check @foo.txt please');
  });

  it('excludes trailing sentence punctuation from the highlighted chip', () => {
    const { vscode } = makeVsCode();
    const { container } = render(
      <VsCodeApiProvider vscode={vscode}>
        <MentionHighlightedText text="see @foo.txt. thanks" />
      </VsCodeApiProvider>
    );
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const chips = container.querySelectorAll('.mention-chip');
    expect(chips).toHaveLength(1);
    expect(chips[0]?.textContent).toBe('@foo.txt');
    expect(container.textContent).toBe('see @foo.txt. thanks');
  });

  it('highlights multiple mentions independently', () => {
    const { vscode } = makeVsCode();
    const { container } = render(
      <VsCodeApiProvider vscode={vscode}>
        <MentionHighlightedText text="@a.txt and @b.txt" />
      </VsCodeApiProvider>
    );
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const chips = container.querySelectorAll('.mention-chip');
    expect(chips).toHaveLength(2);
    expect(chips[0]?.textContent).toBe('@a.txt');
    expect(chips[1]?.textContent).toBe('@b.txt');
  });

  it('includes the surrounding quotes when highlighting a quoted mention', () => {
    const { vscode } = makeVsCode();
    const { container } = render(
      <VsCodeApiProvider vscode={vscode}>
        <MentionHighlightedText text='see @"foo bar.txt" now' />
      </VsCodeApiProvider>
    );
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const chips = container.querySelectorAll('.mention-chip');
    expect(chips).toHaveLength(1);
    expect(chips[0]?.textContent).toBe('@"foo bar.txt"');
  });

  it('posts openLocation with the resolved filename when a chip is clicked', () => {
    const { vscode, posted } = makeVsCode();
    const { container } = render(
      <VsCodeApiProvider vscode={vscode}>
        <MentionHighlightedText text="check @foo.txt please" />
      </VsCodeApiProvider>
    );
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const chip = container.querySelector('.mention-chip');
    expect(chip).not.toBeNull();
    fireEvent.click(chip!);
    expect(posted).toEqual([{ type: 'openLocation', path: 'foo.txt' }]);
  });
});
