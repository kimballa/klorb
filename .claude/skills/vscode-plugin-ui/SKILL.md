---
name: vscode-plugin-ui
description: Build or review webview UI in vscode-plugin/src/webview -- choosing between a vscode-elements custom element, a plain HTML control, or a hand-rolled component; choosing a codicon vs. a custom SVG icon; and deciding whether a component belongs in the top-level webview/components/ folder or under a webview/features/<name>/components/ folder. Use when adding a button, icon, or other interactive control, or when a component's file placement is unclear.
---

# vscode-plugin webview UI conventions

Every new piece of webview UI raises three independent questions: which control primitive to
build it from, which icon system to draw from, and which folder it belongs in. This skill covers
all three; `docs/specs/vscode-plugin.md`'s "Component library" section and the
`vscode-plugin-architecture` skill are the underlying references this skill operationalizes.

## 1. Control primitive: vscode-element, plain HTML, or hand-rolled

`@vscode-elements/elements` custom elements (`<vscode-button>`, `<vscode-textarea>`,
`<vscode-textfield>`, `<vscode-icon>`, `<vscode-context-menu>`, `<vscode-progress-ring>`,
`<vscode-badge>`, ...) are rendered directly as JSX with no wrapper package -- React 19 passes
props straight through to custom-element properties/attributes. See
`docs/adrs/use-vscode-elements-for-webview-controls.md` for why this library was chosen over the
alternatives. Reach for one of these first whenever a matching element exists.

Two situations call for something else instead:

* **Plain HTML, precedented and hand-styled.** `<details>`/`<summary>` is the disclosure used for
  the thinking block, the approval panel's "Show full command", the approval/question panels'
  "Other…" redirects, and `TaskPanel`. A bare `<button>` is used for controls whose exact box
  model `vscode-button` doesn't offer and would need fighting to reproduce: `StatusRow`'s
  model/thinking/permission-mode chips and `ToolCallChip`/`BashToolCallChip`'s inline title-link
  buttons (all variable-width text that becomes a link only when clickable, `color: inherit`
  otherwise -- nothing like a "real button"), and the shared `IconButton`
  (`webview/components/IconButton.tsx`) for 18x18 icon-only affordances (`PanelHeader`'s session
  actions, `StatusMenu`'s chevron) -- `vscode-button`'s own icon-only mode has a 24x26px minimum
  footprint and its own border/padding/border-radius defaults, which is close but not exact.
* **`vscode-button`, for CTA-style actions.** Send/Stop (`PromptInput`), Continue/Deny
  (`ToolCallLimitPanel`), the approval/question option buttons, "Open diff" -- anything that
  should read as a normal clickable action with the theme's standard button padding and coloring.

Rule of thumb: if it's a normal-looking action button, start with `vscode-button`; every
deviation from its default box model needs its own justification, the same way `IconButton`'s
doc comment explains why it exists instead of just using `vscode-button` with `iconOnly`.

Hand-rolling isn't a one-way door, and it isn't scoped to buttons: whenever a new piece of
hand-rolled HTML/CSS would render out identical or near-identical to something already
hand-rolled elsewhere, don't add another copy -- extract the existing pattern into a shared
component under `webview/components/` and point both call sites at it. `IconButton` is the
worked example: `PanelHeader`'s and `StatusMenu`'s icon buttons were two separately hand-rolled
18x18 `<button>`s with their own near-duplicate CSS classes before being unified. Unlike a
duplicated pure function, duplicated markup/CSS tends to drift silently (`.panel-header-icon-button`
and `.status-menu-button` had already picked up different `color`/`border` treatment despite being
"the same kind of thing"), so two hand-rolled occurrences of the same control is reason enough to
consolidate -- don't wait for a third.

## 2. Icon: codicon or custom SVG

Prefer a [codicon](https://microsoft.github.io/vscode-codicons/dist/codicon.html) via
`<vscode-icon name="...">` whenever the desired glyph exists -- it themes automatically and stays
visually consistent with the rest of VS Code's chrome. **Verify what a codicon name actually
draws before trusting it** -- names are sometimes aliases for an unrelated glyph. For example,
`@vscode/codicons/src/template/mapping.json` maps codepoint `60039` to `["error", "stop"]`: the
`stop` codicon is the `error` glyph (a circle with an X), not a filled square. Check
`node_modules/@vscode/codicons/src/icons/<name>.svg` (following the alias if the name isn't the
canonical one in `mapping.json`) rather than assuming the name describes the shape.

Only hand-draw an inline SVG (see `webview/components/klorbIcons.tsx`'s `PlayMediaIcon`/
`StopMediaIcon`, used by `PromptInput`'s Send/Stop buttons) when codicons has no equivalent glyph
at all. Give it `fill="currentColor"` and no explicit `color` so it inherits whichever foreground
color applies at its mount point (see below), and add new shared icons to `klorbIcons.tsx` rather
than defining another one-off `function FooIcon()` inside an unrelated component file.

## 3. The `currentColor` / codicon-color gotcha

A hand-drawn SVG's `fill="currentColor"` resolves to the CSS `color` computed for the SVG element
itself. That's ordinary inheritance, and it flows through the flattened DOM tree -- including
through a custom element's `<slot>` -- so an SVG slotted into `vscode-button`'s default slot picks
up `.base`'s `color: var(--vscode-button-foreground)` (or `--vscode-button-secondaryForeground`)
for free, with zero extra CSS.

`<vscode-icon>` does **not** behave the same way. Its own shadow styles set
`:host { color: var(--vscode-icon-foreground, #cccccc) }` explicitly -- a hardcoded default meant
for an icon sitting on the editor/sidebar background, not `color: inherit`. Drop a bare
`<vscode-icon>` inside anything with its own background color (a filled/primary-colored button)
and it renders in that generic muted gray instead of the button's actual foreground, unless
something patches it back to `color: inherit`.

`vscode-button` already carries that patch, for both ways a `vscode-icon` can end up inside it:
`.icon, .icon-after { color: inherit; }` covers its own `icon`/`iconAfter` props, and
`::slotted(vscode-icon) { color: inherit; }` covers one passed as a child through the default
slot. A bare `<button>` has neither. That's exactly the bug `IconButton`'s
`.icon-button vscode-icon { color: inherit; }` rule (`media/main.css`) fixes -- reuse `IconButton`
for a new icon-only bare-button control rather than rediscovering this per call site.

## 4. Component placement: `webview/components/` vs. `webview/features/<name>/components/`

The `vscode-plugin-architecture` skill is the authoritative rule; the tell in practice
is: **would this component make sense imported from a feature it isn't already part of?**

* If yes, it belongs in the top-level `webview/components/` (no feature folder) -- imported
  directly by path (`webview/components/IconButton`), since there's no barrel at that level.
  `VsCodeApiProvider`, `ErrorBoundary`, `PanelHeader`, `PromptInput`, and `IconButton`/
  `klorbIcons` are the unambiguous cases: nothing about any of them is tied to one feature's own
  data model.

  `ApprovalPanel`/`QuestionPanel`, `StatusRow`/`StatusMenu`, and `TaskPanel` live in
  `webview/components/` today too, but are less clear-cut and shouldn't be read as settled
  precedent -- each renders enough of its own dedicated state that it arguably deserves its own
  `features/<name>/` folder and may get moved there someday (`TaskPanel` in particular is a
  reasonable candidate for `features/tasks/components/TaskPanel.tsx`, given it already has its
  own `TaskListSnapshot` data model). `docs/specs/vscode-plugin.md` documents `TaskPanel` as a
  top-level component specifically because it isn't part of the `history` feature, not because
  top-level is its ideal permanent home -- rendering *some* feature's data isn't by itself
  disqualifying for top-level placement, but a component with a whole dedicated data
  model/reducer of its own is a signal pointing the other way, toward carving out a new feature.
* If no -- it only ever renders one feature's own model types and has no reason to exist without
  it -- it belongs under that feature's own `components/` folder, e.g.
  `webview/features/history/components/HistoryView.tsx`/`ToolCallChip.tsx`/
  `BashToolCallChip.tsx`/`SessionStatsCard.tsx`, all of which render `historyModel.ts` entry
  types. Only the feature's `index.ts` barrel may be imported from outside the feature
  (`webview/features/history`, never a deep import like
  `webview/features/history/components/HistoryView`) -- this is enforced by `eslint.config.mjs`'s
  `no-restricted-imports` rule.

## Checklist when adding a new webview control

- [ ] Does a `@vscode-elements/elements` custom element already do this? Use it before reaching
      for plain HTML or a hand-rolled component.
- [ ] If hand-rolling any control (button or otherwise): does its target size/shape/behavior
      actually deviate from what an existing vscode-elements element or component offers, or
      could an existing one already do the job?
- [ ] Is another part of the codebase already hand-rolling something identical or
      near-identical to what you're about to add? If so, roll both into one shared component
      under `webview/components/` instead of adding a second (or third) copy.
- [ ] If it needs an icon: does a codicon exist, and have you actually checked its `.svg` (or its
      canonical alias in `mapping.json`) rather than assuming the name matches the shape you want?
- [ ] If it's a custom SVG icon: does it use `fill="currentColor"` with no explicit `color`, and
      does it live in `klorbIcons.tsx` rather than inline in an unrelated component?
- [ ] If it wraps a `<vscode-icon>` inside a bare `<button>` (not `vscode-button`): does the
      surrounding CSS force `color: inherit` on it, or reuse `IconButton` instead of duplicating
      that fix?
- [ ] New component file: would another feature plausibly import this? Top-level
      `webview/components/`. Otherwise: does it only render one feature's own model types? That
      feature's own `components/` folder.
