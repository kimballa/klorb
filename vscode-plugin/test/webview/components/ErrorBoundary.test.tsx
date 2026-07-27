/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import ErrorBoundary from 'webview/components/ErrorBoundary';
import type { VsCodeApi } from 'webview/components/VsCodeApiProvider';

afterEach(cleanup);

function Bomb(): never {
  throw new Error('kaboom');
}

function makeVsCode(): { vscode: VsCodeApi; posted: unknown[] } {
  const posted: unknown[] = [];
  return {
    posted,
    vscode: {
      postMessage: (message: unknown) => posted.push(message),
      setState: () => undefined,
      getState: () => undefined,
    },
  };
}

describe('ErrorBoundary', () => {
  it('renders children normally when nothing throws', () => {
    const { vscode } = makeVsCode();
    render(
      <ErrorBoundary vscode={vscode}>
        <div>fine</div>
      </ErrorBoundary>
    );

    expect(screen.getByText('fine')).toBeTruthy();
  });

  it('catches a render error, shows a fallback, and reports it to the host', () => {
    const { vscode, posted } = makeVsCode();
    // React logs a caught render error to the console by design (twice, in dev mode); silence
    // it so the test's own output doesn't read like a failure.
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);

    render(
      <ErrorBoundary vscode={vscode}>
        <Bomb />
      </ErrorBoundary>
    );

    expect(screen.getByText(/Klorb panel crashed: kaboom/)).toBeTruthy();
    expect(posted).toEqual([expect.objectContaining({ type: 'webviewError', message: 'kaboom' })]);

    consoleError.mockRestore();
  });
});
