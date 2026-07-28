# Run the React Compiler through a Babel esbuild plugin, not esbuild's native transform

**Date:** 2026-07-28

**Question:** How should the webview build apply the React Compiler's automatic memoization to
`src/webview/**/*.tsx` components, given `build:webview` bundles with `esbuild`, and
`babel-plugin-react-compiler` only ships as a Babel plugin?

**Answer:** Replace the webview's direct `esbuild` CLI invocation with `build-webview.mjs`, a
small Node script that calls esbuild's JS API and installs `esbuild-plugin-babel` so Babel runs
ahead of esbuild's own built-in TS/JSX transform. The plugin's config runs
`@babel/preset-typescript` (`isTSX: true, allExtensions: true`), then `@babel/preset-react`
(`runtime: 'automatic'`), then `babel-plugin-react-compiler` (`target: '19'`, matching the pinned
`react`/`react-dom` major) over every `.ts`/`.tsx` file under `src/webview/**`/`src/shared/**`
before esbuild bundles the (by then plain-JS) result. `build:extension`/`build:extension:prod`
are untouched — the extension host has no JSX/React to compile.

**Reasoning:** esbuild's built-in TS/JSX transform has no plugin hook a compiler pass like
`babel-plugin-react-compiler` can attach to — the only place it can run is inside a Babel
pipeline, and the only way to get a Babel pipeline inside an esbuild bundle without switching
bundlers is a plugin that shells out to Babel per file. `esbuild-plugin-babel`'s `onLoad` hook
does exactly that: it reads each matched file and returns Babel's transformed output, which is
already plain JS by the time execution would otherwise reach esbuild's own loader, so esbuild's
built-in TS/JSX handling for those files is bypassed rather than layered underneath. `esbuild`'s
CLI has no way to register a plugin (plugins are JS-API-only), so the previous inline
`esbuild ...` command line couldn't host this — hence `build-webview.mjs`, which otherwise just
reproduces the same bundle options the CLI invocations used (`--bundle`, `--tsconfig`,
`--format=iife`, `--platform=browser`, `--target=es2022`, the dev/prod sourcemap and `NODE_ENV`
define, the prod-only `--minify`/`--legal-comments=linked`).

The plugin's `filter` matches only `src/webview/**` and `src/shared/**` paths
(`/[\\/](?:webview|shared)[\\/].*\.[jt]sx?$/`) rather than every `.ts`/`.tsx`/`.js`/`.jsx` file
esbuild loads: `esbuild`'s `onLoad` filter compiles to a Go (RE2) regular expression, which has no
lookaround to express "not under node_modules" directly, so excluding `node_modules` (where
`react`, `react-dom`, `react-markdown`, and `@vscode-elements/elements`'s own bundled source
live) isn't expressible as a negative pattern — a positive match on this project's own two source
directories does the same job. Routing vendored dependency source through the React Compiler and
TS/JSX presets would be wasted work and risks Babel choking on syntax those packages' own
published builds already account for (observed directly: an unfiltered first pass triggered
Babel's "code generator has deoptimised" warning on `react-dom`'s bundled source).

`isTSX: true, allExtensions: true` parses every matched file — `.ts` and `.tsx` alike — as TSX so
the React Compiler sees JSX in the same pass regardless of extension. This is safe here because
neither `src/webview/**` nor `src/shared/**` uses the old angle-bracket type-cast syntax
(`<Foo>value`), which would otherwise be ambiguous with JSX under forced TSX parsing.
`esbuild-plugin-babel` only intercepts file *contents* via `onLoad`, not module *resolution*, so
the `tsconfig.webview.json`-driven `shared/*`/`webview/*` path-alias resolution esbuild already
performed is unaffected.
