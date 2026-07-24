// © Copyright 2026 Aaron Kimball
import { defineConfig } from 'vitest/config';

// Webview TSX tests (App.test.tsx) need the automatic JSX runtime so `<App />` compiles
// without an explicit `import React`, matching tsconfig.webview.json's `jsx: "react-jsx"`.
export default defineConfig({
  esbuild: {
    jsx: 'automatic',
    jsxImportSource: 'react',
  },
});
