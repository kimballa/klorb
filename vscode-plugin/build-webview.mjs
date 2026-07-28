// © Copyright 2026 Aaron Kimball
//
// Bundles src/webview/main.tsx with esbuild's JS API rather than its CLI, since applying the
// React Compiler (babel-plugin-react-compiler) requires a Babel pass and esbuild plugins --
// which run the Babel pass ahead of esbuild's own built-in TS/JSX transform -- are only
// available through the JS API. See
// docs/adrs/run-react-compiler-through-babel-esbuild-plugin.md.

import * as esbuild from 'esbuild';
import babelPlugin from 'esbuild-plugin-babel';

const prod = process.argv.includes('--prod');

const reactCompilerBabel = babelPlugin({
  // Matches only this project's own src/webview/** and src/shared/** files -- esbuild's onLoad
  // filter compiles to a Go (RE2) regular expression, which has no lookaround to express "not
  // under node_modules" directly, so this instead positively matches the two source directories
  // reachable from src/webview/main.tsx. Routing react/react-dom/react-markdown/
  // @vscode-elements/elements' own bundled source through the React Compiler and TS/JSX presets
  // too would be both wasted work and a needless risk of Babel choking on syntax those packages'
  // own build already accounted for.
  filter: /[\\/](?:webview|shared)[\\/].*\.[jt]sx?$/,
  config: {
    presets: [
      ['@babel/preset-typescript', { isTSX: true, allExtensions: true }],
      ['@babel/preset-react', { runtime: 'automatic' }],
    ],
    plugins: [['babel-plugin-react-compiler', { target: '19' }]],
  },
});

async function buildWebview() {
  await esbuild.build({
    entryPoints: ['src/webview/main.tsx'],
    tsconfig: 'tsconfig.webview.json',
    bundle: true,
    outfile: 'out/webview/main.js',
    format: 'iife',
    platform: 'browser',
    target: 'es2022',
    plugins: [reactCompilerBabel],
    define: {
      'process.env.NODE_ENV': prod ? '"production"' : '"development"',
    },
    ...(prod
      ? {
          minify: true,
          sourcemap: 'linked',
          sourcesContent: false,
          legalComments: 'linked',
        }
      : {
          sourcemap: true,
        }),
  });
}

buildWebview().catch((error) => {
  console.error(error);
  process.exit(1);
});
