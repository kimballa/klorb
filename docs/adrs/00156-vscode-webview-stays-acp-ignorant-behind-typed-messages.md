# Keep the VS Code webview ACP-ignorant behind a typed host↔webview message protocol

* Date: 2026-07-24 15:30
* Question: The VS Code plugin's ACP client-side connection (`AcpConnection`,
  `KlorbAcpClient`) has to live somewhere in the extension's two-world split — the Node/
  CommonJS extension host, or the sandboxed `vscode-webview://` React app. Should the
  webview drive the ACP connection directly (importing `@agentclientprotocol/sdk` into the
  browser bundle and speaking session updates straight into React state), or should the
  extension host own the connection and translate it into something else for the webview?
* Answer: The extension host owns the entire ACP connection. `KlorbSessionViewProvider`
  (a `SessionUpdateListener`) receives `AcpConnection`'s streamed text callbacks and
  `session/prompt`/`session/cancel` results, and re-expresses them as a small, versioned
  discriminated union defined once in `src/shared/webviewMessages.ts`
  (`HostMessage`/`WebviewMessage`), included by both the host and webview tsconfigs. The
  webview never imports the ACP SDK, never sees an ACP schema type, and only exchanges
  `postMessage`/`onDidReceiveMessage` payloads shaped by that union. `parseHostMessage`/
  `parseWebviewMessage` type-guard every payload crossing the boundary, since
  `onDidReceiveMessage`'s argument is untyped `unknown` from the extension host's point of
  view (following the `isSubmitMessage` pattern the original greet-stub plugin already used).
* Reasoning: The webview bundle is a browser build with no Node module resolution and a
  locked-down CSP; pulling `@agentclientprotocol/sdk` into it would mean bundling JSON-RPC
  connection plumbing, stream machinery, and the full generated ACP schema into code that
  has no business owning a process connection — the webview's only job is presenting state
  and expressing user intent. Keeping ACP entirely host-side also means the webview's tests
  (`test/App.test.tsx`, `test/historyModel.test.ts`) run against plain message fixtures with
  no ACP SDK, no VS Code API, and no real or mocked stream at all — `historyModel.ts`'s
  reducer-style `applyHostMessage()` is a pure function over the shared message union, testable
  in milliseconds under jsdom. The alternative (webview drives ACP directly) would require
  either running the SDK's JSON-RPC connection inside the webview's sandboxed iframe context —
  a process boundary ACP's stdio transport doesn't cross — or piping raw ACP JSON-RPC frames
  over `postMessage`, which just re-invents this ADR's typed union one level lower with none of
  its guardrails (no shared type-guard, no host-side integration point for VS Code editor
  actions ACP's `tool_call` locations will eventually drive, per the plan overview's "VS Code
  client architecture" section). This is also why translation happens at
  `KlorbSessionViewProvider`, not `AcpConnection` itself: `AcpConnection` stays a
  webview-independent ACP client any other host-side caller could reuse, and the provider is
  the one seam that knows both ACP and the webview message shape.
