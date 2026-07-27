// © Copyright 2026 Aaron Kimball
import { Component, type ErrorInfo, type ReactNode } from 'react';

import type { VsCodeApi } from 'webview/components/VsCodeApiProvider';

export interface ErrorBoundaryProps {
  vscode: VsCodeApi;
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | undefined;
}

/**
 * Catches an uncaught render error anywhere in the panel and replaces the crashed tree with a
 * plain-text fallback instead of leaving the whole webview blank -- React unmounts everything
 * below the nearest error boundary (or the whole root, with none) the moment a render throws.
 * Also reports the error to the extension host as a `webviewError` message, which
 * `KlorbSessionViewProvider` logs to the "Klorb" output channel: the webview runs in its own
 * sandboxed `vscode-webview://` document with its own JS console (reachable via VS Code's
 * "Developer: Open Webview Developer Tools" command), so an uncaught exception there never
 * otherwise reaches anywhere the extension's own logging goes.
 */
export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  public override state: ErrorBoundaryState = { error: undefined };

  public static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  public override componentDidCatch(error: Error, info: ErrorInfo): void {
    this.props.vscode.postMessage({
      type: 'webviewError',
      message: error.message,
      stack: error.stack ?? info.componentStack ?? undefined,
    });
  }

  public override render(): ReactNode {
    const { error } = this.state;
    if (error === undefined) {
      return this.props.children;
    }
    return (
      <div className="webview-crash">
        <p>Klorb panel crashed: {error.message}</p>
        {error.name && (
          <p>
            <strong>name:</strong>&nbsp;{error.name}
          </p>
        )}
        {!!error.cause && (
          <p>
            <strong>cause:</strong>&nbsp;{error.cause.toString()}
          </p>
        )}
        {error.stack && <div className="webview-crash-stacktrace">{error.stack}</div>}
        <p>
          Stack trace info is also available via{' '}
          <strong>Developer: Open Webview Developer Tools</strong>, or the &quot;Klorb&quot; output
          channel.
        </p>
      </div>
    );
  }
}
