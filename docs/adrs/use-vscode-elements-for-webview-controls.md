# Use `@vscode-elements/elements` web components for the webview's interactive controls

* Date: 2026-07-24 15:30
* Question: The stub webview's chat panel used plain HTML (`<textarea>`, `<button>`) styled
  by hand against VS Code's `--vscode-*` CSS custom properties in `media/main.css`. Rebuilding
  the panel for streaming chat, and later increments' approval panels, question forms, and
  status controls, needs a real component set. Should the plugin keep hand-rolling styled
  native elements, or adopt a component library, and if the latter, which one?
* Answer: `@vscode-elements/elements` (the community-maintained successor to the
  `@bendera/vscode-webview-elements` project, itself the spiritual continuation of Microsoft's
  now-archived `@vscode/webview-ui-toolkit`), rendered directly as custom elements from React
  19 JSX (`<vscode-textarea>`, `<vscode-button>`, ...) with no wrapper package — React 19
  passes props straight through to custom elements' properties/attributes, so no
  `@vscode-elements/react` adapter package is needed. The custom elements' TypeScript JSX
  typings are vendored into `vscode-plugin/types/global.d.ts` from the vscode-elements
  examples repo's own `global.d.ts` (declaring the `react`-module `JSX.IntrinsicElements`
  additions), rather than hand-writing prop types per element as they're adopted.
* Reasoning: `@vscode-elements/elements` is Lit-based web components that read VS Code's own
  theme CSS custom properties out of the box, so `<vscode-button>`/`<vscode-textarea>` render
  correctly themed in light, dark, and high-contrast without the plugin re-deriving VS Code's
  visual language by hand — a real cost the original stub's hand-rolled `.bubble`/
  `#prompt-input`/`#submit-button` CSS already showed signs of (colors mixed manually via
  `color-mix()` against `--vscode-sideBar-background` to approximate a "raised surface" VS
  Code's own toolkit gets automatically). Later increments in this plan need option grids
  (permission asks), radio/select controls (model/thinking pickers), and collapsible sections
  (task panel) — `@vscode-elements/elements` covers all of these as one coherent, actively
  maintained set rather than assembling ad hoc widgets increment by increment.
  `@vscode/webview-ui-toolkit` itself was considered and rejected: Microsoft archived it,
  meaning no future compatibility or security fixes.  As a web-components library (not a React
  component library), `@vscode-elements/elements` bundles fine through the existing esbuild
  pipeline (unminified IIFE, per `docs/adrs/bundle-webview-script-with-esbuild-not-es-modules.md`)
  with no build-tool changes; the only new piece is the vendored JSX typings, since the library
  itself ships TypeScript element classes but not React's `IntrinsicElements` declarations.
