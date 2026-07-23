// © Copyright 2026 Aaron Kimball

/** What a keydown on the prompt textarea should do. */
export type EnterAction = 'submit' | 'newline';

/**
 * Decides how a keydown event on the prompt textarea should be handled. Only meaningful for
 * `key === 'Enter'`; callers should ignore every other key before reaching here.
 */
export function classifyEnterKey(shiftKey: boolean, ctrlKey: boolean): EnterAction {
  return shiftKey || ctrlKey ? 'newline' : 'submit';
}
