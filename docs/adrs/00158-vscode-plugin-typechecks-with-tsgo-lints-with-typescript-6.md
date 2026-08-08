# vscode-plugin typechecks with tsgo, lints with <typescript@6.x>

* Date: 2026-07-24 22:35
* Question: `vscode-plugin` wanted to typecheck with `tsgo` (the native, Go-ported TypeScript
  compiler) instead of the classic `tsc`, and considered upgrading the `typescript` package to
  6.0.3 to go with it, on a source article's recommendation. Checking the npm registry showed
  the article was already stale: TypeScript has moved past 6.x to a 7.x major (`7.0.2` is
  `latest`), and `tsgo` itself ships only as nightly dev-tagged prereleases of a separate
  package, `@typescript/native-preview` (no stable, non-dev release exists). Given that, what
  should `typescript`/`typecheck` actually be set to?
* Answer: `typescript` stays pinned to `^6.0.3` (a real, released, non-dev version) as the
  package `typescript-eslint`/`eslint-plugin-import-x` resolve against for linting.
  `@typescript/native-preview` is installed alongside it purely to provide the `tsgo` binary,
  and `package.json`'s `typecheck`/`watch` scripts invoke `tsgo -p ./` /
  `tsgo -p tsconfig.webview.json` instead of `tsc`. `@typescript-eslint/parser` is also pinned
  as an explicit devDependency (not left as a transitive dependency of the `typescript-eslint`
  meta-package) so npm hoists it to `node_modules/@typescript-eslint/parser` — `eslint-plugin-
  import-x`'s cross-module parsing does its own `require('@typescript-eslint/parser')` from its
  own package location, which doesn't find a copy nested only inside `typescript-eslint`'s own
  `node_modules`.
* Reasoning: Trying `typescript@7.0.2` directly (the actual current `latest`) first: `tsgo -p
  ./` and `tsgo -p tsconfig.webview.json` both passed cleanly against it, but `make lint` hard
  crashed — `typescript-eslint@8.65.0` (the newest published version at the time) throws
  `typescript-eslint does not support TS 7.0` at import time, pointing at
  `typescript-eslint/typescript-eslint#10940` (open, tracking TS 7 support) and a TypeScript-team
  blog post on running typescript-eslint side-by-side with a separate TS 6 install for exactly
  this transition period. There is no supported way to lint against TS 7 with today's
  typescript-eslint. Splitting the two tools — a real, lint-tool-compatible `typescript` version
  for ESLint's type-aware rules, and the separate `tsgo` preview binary for the actual
  `make typecheck` gate — gets the user's actual ask (tsgo-based typechecking) working today
  without blocking `make lint`, while keeping the door open to drop `@typescript/native-preview`
  and go back to plain `tsc` if tsgo's preview status turns out to cause other problems, or to
  drop the split once typescript-eslint supports TS 7.
