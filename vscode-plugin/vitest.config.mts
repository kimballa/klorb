// © Copyright 2026 Aaron Kimball
import tsconfigPaths from 'vite-tsconfig-paths';
import { defineConfig } from 'vitest/config';

// Webview TSX tests (App.test.tsx) need the automatic JSX runtime so `<App />` compiles
// without an explicit `import React`, matching tsconfig.webview.json's `jsx: "react-jsx"`.
// tsconfigPaths() reads the `shared/*`/`webview/*`/`host/*` aliases straight out of
// tsconfig.json/tsconfig.webview.json, so tests resolve the same rooted imports as the
// extension-host `tsc` build and the webview `esbuild` bundle, with one canonical mapping.
export default defineConfig({
  // `projects` is required because neither tsconfig's `include` covers `test/` — the plugin's
  // default per-importer tsconfig lookup wouldn't otherwise know which config's `paths` govern
  // a test file, so both are named explicitly.
  plugins: [tsconfigPaths({ projects: ['tsconfig.json', 'tsconfig.webview.json'] })],
  esbuild: {
    jsx: 'automatic',
    jsxImportSource: 'react',
  },
  test: {
    setupFiles: ['./test/setup.ts'],
  },
});
