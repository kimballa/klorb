2026-08-19

## Question

`vscode-plugin`'s webview component tests run under jsdom, but nothing ever registered
`@vscode-elements/elements`'s custom elements (`<vscode-textarea>`, `<vscode-button>`, ...) in
that environment -- only `main.tsx` imports the side-effecting registration modules, and it's
never imported by tests. Every `<vscode-textarea>`/`<vscode-button>`/etc. a test rendered was
therefore an inert, unregistered element: reading back a reflected attribute, a property Lit
doesn't reflect, or a `wrappedElement`/`updateComplete` API just returned `undefined` or
whatever a test manually stubbed onto it, not what the real component does. How should tests
get real custom-element behavior instead?

## Answer

`vscode-plugin/src/webview/registerCustomElements.ts` holds the same side-effecting imports
`main.tsx` used to inline; `main.tsx` now imports that module, and `test/setup.ts` imports it
too (guarded on `typeof customElements !== 'undefined'`, since the same setup file also runs
for host-side tests under plain Node, which has no DOM). Two gaps in jsdom's own DOM
implementation had to be patched in `test/setup.ts` alongside it for the real elements to
actually construct and render:

* jsdom's `ElementInternals` omits the form-association API
  (`setFormValue`/`setValidity`/`checkValidity`/`reportValidity`/`form`/`validity`/
  `validationMessage`/`willValidate`) entirely. `vscode-textarea`/`vscode-button`/and the other
  form-associated components call these unconditionally from their constructors and property
  setters, so without a stub, constructing any of them throws immediately.
* `<vscode-icon>` renders a `<link rel="stylesheet">` into its own shadow DOM, with `href` left
  off entirely when no `#vscode-codicon-stylesheet` link exists on the page to read a URL from.
  jsdom's handling of that hrefless `<link>` leaves behind a broken `CSSStyleSheet` object that
  crashes Node's own `util.inspect`-based error formatter the moment anything tries to print it
  -- which `catchWindowErrors` (Vitest's jsdom environment) does for any propagated `window`
  `error` event, turning an unrelated later exception's default logging into a second, harder
  to diagnose crash. `test/setup.ts` appends a stub `#vscode-codicon-stylesheet` `<link>` to
  `document.head`, matching what `klorbSessionViewProvider.ts`'s webview HTML shell always
  provides in production, which sidesteps the whole path.

Existing tests that had built their own workarounds for the previously-unregistered elements
were updated to use the real element instead: reading a Lit `reflect: true` property
(`disabled`) off the element directly rather than polling `hasAttribute` synchronously (Lit
reflects to the attribute asynchronously, so an immediate `hasAttribute` check is a race even
in a real browser); reading a non-reflecting property (`<vscode-icon>`'s `name`) as a property
instead of an attribute, since it was never reflected in the first place; and, for the one test
driving `<vscode-textarea>`'s real `wrappedElement`/`updateComplete`, awaiting Lit's actual
update cycle instead of the test's own hand-rolled mock of those two members.

## Reasoning

Patching jsdom's gaps in `test/setup.ts` and sharing the registration side effects between
`main.tsx` and tests (rather than only stubbing enough of each element's surface for whatever
one test happened to touch) keeps the real component's actual behavior as the thing under test,
which is the whole point of fixing the registration gap in the first place: a test asserting
against a hand-stubbed element only proves the stub is self-consistent, not that the real
`@vscode-elements/elements` component behaves the way the test assumes.
