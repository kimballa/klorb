// © Copyright 2026 Aaron Kimball
import { createContext, type JSX, type ReactNode } from 'react';

/** The API VS Code injects into the webview via `acquireVsCodeApi()`: posting messages to the
 * extension host, and persisting/reading small bits of state across the webview's context
 * teardown/rebuild (see `retainContextWhenHidden`). */
export interface VsCodeApi {
  postMessage(message: unknown): void;
  setState(state: unknown): void;
  getState(): unknown;
}

export const VsCodeApiContext = createContext<VsCodeApi | undefined>(undefined);

interface VsCodeApiProviderProps {
  vscode: VsCodeApi;
  children: ReactNode;
}

/**
 * Distributes the single `vscode` object obtained from `main.tsx`'s one `acquireVsCodeApi()`
 * call (see `docs/adrs/call-acquirevscodeapi-exactly-once-per-webview-page.md`) to any
 * descendant via `useVsCodeApi()`, instead of prop-drilling it through every intermediate
 * component that doesn't otherwise need it.
 */
export default function VsCodeApiProvider({
  vscode,
  children,
}: VsCodeApiProviderProps): JSX.Element {
  return <VsCodeApiContext.Provider value={vscode}>{children}</VsCodeApiContext.Provider>;
}
