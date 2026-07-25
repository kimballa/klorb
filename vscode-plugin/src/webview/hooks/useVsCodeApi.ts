// © Copyright 2026 Aaron Kimball
import { useContext } from 'react';

import { VsCodeApiContext, type VsCodeApi } from 'webview/components/VsCodeApiProvider';

/** Returns the `vscode` object the nearest `<VsCodeApiProvider>` ancestor was constructed
 * with. Throws if rendered outside one — every component needing the VS Code API sits under
 * `<App>`'s provider rather than acquiring or receiving it another way (see
 * `docs/adrs/call-acquirevscodeapi-exactly-once-per-webview-page.md`). */
export default function useVsCodeApi(): VsCodeApi {
  const vscode = useContext(VsCodeApiContext);
  if (vscode === undefined) {
    throw new Error('useVsCodeApi() must be called within a <VsCodeApiProvider>');
  }
  return vscode;
}
