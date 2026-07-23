// © Copyright 2026 Aaron Kimball
import { classifyEnterKey } from './keyHandling';

declare function acquireVsCodeApi(): { setState(state: unknown): void; getState(): unknown };

interface SessionState {
  entries: string[];
}

function main(): void {
  // acquireVsCodeApi() throws if called more than once per page load, so the single call
  // result is threaded through rather than each function calling it for itself.
  const vscode = acquireVsCodeApi();
  const history = document.getElementById('history') as HTMLDivElement;
  const input = document.getElementById('prompt-input') as HTMLTextAreaElement;
  const submitButton = document.getElementById('submit-button') as HTMLButtonElement;

  const state = (vscode.getState() as SessionState | undefined) ?? { entries: [] };

  function appendEntry(text: string): void {
    const entry = document.createElement('div');
    entry.className = 'history-entry';
    entry.textContent = text;
    history.appendChild(entry);
    entry.scrollIntoView({ block: 'end' });
  }

  function submit(): void {
    const text = input.value.trim();
    if (text.length === 0) {
      return;
    }
    appendEntry(text);
    state.entries.push(text);
    vscode.setState(state);
    input.value = '';
  }

  state.entries.forEach(appendEntry);

  input.addEventListener('keydown', (event: KeyboardEvent) => {
    if (event.key !== 'Enter') {
      return;
    }
    if (classifyEnterKey(event.shiftKey, event.ctrlKey) === 'submit') {
      event.preventDefault();
      submit();
    }
  });

  submitButton.addEventListener('click', submit);
}

main();
