/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { QuestionAskMessage } from 'shared/webviewMessages';
import QuestionPanel, { type QuestionPanelAnswer } from 'webview/components/QuestionPanel';

afterEach(cleanup);

const MULTI_CHOICE_ASK: QuestionAskMessage = {
  type: 'questionAsk',
  requestId: 1,
  header: 'Format',
  question: 'Which format should we use?',
  options: [{ label: 'JSON' }, { label: 'YAML', description: 'human-friendly' }],
  index: 0,
  total: 2,
};

const FREE_TEXT_ASK: QuestionAskMessage = {
  type: 'questionAsk',
  requestId: 2,
  header: 'Name',
  question: 'What should we call it?',
  options: [],
  index: 1,
  total: 2,
};

describe('QuestionPanel', () => {
  it('renders the header, question, "N of M" caption, and option list', () => {
    render(<QuestionPanel ask={MULTI_CHOICE_ASK} onAnswer={() => undefined} />);

    expect(screen.getByText('Format')).toBeTruthy();
    expect(screen.getByText('Which format should we use?')).toBeTruthy();
    expect(screen.getByText('Question 1 of 2')).toBeTruthy();
    expect(screen.getByText('JSON')).toBeTruthy();
    expect(screen.getByText('YAML')).toBeTruthy();
    expect(screen.getByText('human-friendly')).toBeTruthy();
  });

  it('posts the selected option index when an option is clicked', () => {
    const onAnswer = vi.fn<(answer: QuestionPanelAnswer) => void>();
    render(<QuestionPanel ask={MULTI_CHOICE_ASK} onAnswer={onAnswer} />);

    fireEvent.click(screen.getByText('YAML'));

    expect(onAnswer).toHaveBeenCalledWith({ selectedOptionIndex: 1 });
  });

  it('posts cancelled on Escape', () => {
    const onAnswer = vi.fn<(answer: QuestionPanelAnswer) => void>();
    render(<QuestionPanel ask={MULTI_CHOICE_ASK} onAnswer={onAnswer} />);

    fireEvent.keyDown(screen.getByText('Format'), { key: 'Escape' });

    expect(onAnswer).toHaveBeenCalledWith({ cancelled: true });
  });

  it('focuses the panel on mount so Escape works before the user clicks into it', () => {
    const { container } = render(
      <QuestionPanel ask={MULTI_CHOICE_ASK} onAnswer={() => undefined} />
    );

    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    expect(document.activeElement).toBe(container.querySelector('.question-panel'));
  });

  it('posts otherText on Enter in the "Other…" field', () => {
    const onAnswer = vi.fn<(answer: QuestionPanelAnswer) => void>();
    render(<QuestionPanel ask={MULTI_CHOICE_ASK} onAnswer={onAnswer} />);

    fireEvent.click(screen.getByText('Other…'));
    // eslint-disable-next-line testing-library/no-node-access
    const textfield = document.querySelector('vscode-textfield') as HTMLElement & {
      value: string;
    };
    textfield.value = 'a custom answer';
    fireEvent(textfield, new Event('input', { bubbles: true }));
    fireEvent.keyDown(textfield, { key: 'Enter' });

    expect(onAnswer).toHaveBeenCalledWith({ otherText: 'a custom answer' });
  });

  it('posts otherText from the "Other…" field', () => {
    const onAnswer = vi.fn<(answer: QuestionPanelAnswer) => void>();
    render(<QuestionPanel ask={MULTI_CHOICE_ASK} onAnswer={onAnswer} />);

    fireEvent.click(screen.getByText('Other…'));
    // eslint-disable-next-line testing-library/no-node-access
    const textfield = document.querySelector('vscode-textfield') as HTMLElement & {
      value: string;
    };
    textfield.value = 'a custom answer';
    fireEvent(textfield, new Event('input', { bubbles: true }));
    fireEvent.click(screen.getByText('Send'));

    expect(onAnswer).toHaveBeenCalledWith({ otherText: 'a custom answer' });
  });

  it('reveals the "Other…" input by default for a plain free-text question with no options', () => {
    const { container } = render(<QuestionPanel ask={FREE_TEXT_ASK} onAnswer={() => undefined} />);

    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const disclosure = container.querySelector('.question-other');
    expect(disclosure?.hasAttribute('open')).toBe(true);
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    expect(container.querySelector('.question-options')).toBeNull();
  });
});
