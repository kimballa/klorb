// © Copyright 2026 Aaron Kimball
import js from '@eslint/js';
import prettierConfig from 'eslint-config-prettier';
import importX from 'eslint-plugin-import-x';
import prettierPlugin from 'eslint-plugin-prettier';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import testingLibrary from 'eslint-plugin-testing-library';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  js.configs.recommended,
  ...tseslint.configs.recommended,
  importX.flatConfigs.recommended,
  importX.flatConfigs.typescript,
  {
    // Overrides `flatConfigs.typescript`'s resolver settings: this project splits its `paths`
    // aliases (`shared/*`, `webview/*`, `host/*`) across two tsconfigs (see tsconfig.json and
    // tsconfig.webview.json), so the resolver needs both project files to resolve every alias.
    settings: {
      'import-x/resolver': {
        typescript: {
          project: ['tsconfig.json', 'tsconfig.webview.json'],
          noWarnOnMultipleProjects: true,
        },
      },
      react: { version: 'detect' },
    },
  },
  {
    ...reactHooks.configs['recommended-latest'],
    files: ['src/webview/**/*.tsx'],
  },
  {
    ...react.configs.flat.recommended,
    files: ['src/webview/**/*.tsx'],
  },
  {
    ...react.configs.flat['jsx-runtime'],
    files: ['src/webview/**/*.tsx'],
  },
  {
    // TypeScript already checks prop shapes; react/prop-types is redundant (and can't see TS
    // types anyway).
    files: ['src/webview/**/*.tsx'],
    rules: {
      'react/prop-types': 'off',
    },
  },
  {
    ...testingLibrary.configs['flat/react'],
    files: ['test/**/*.{ts,tsx}'],
    rules: {
      ...testingLibrary.configs['flat/react'].rules,
      // This project's vitest.config.ts doesn't set `test.globals: true`, so
      // @testing-library/react's own afterEach-based auto-cleanup (which relies on detecting
      // a global `afterEach`) never engages — the explicit `afterEach(cleanup)` is genuinely
      // required here, not a redundant manual step.
      'testing-library/no-manual-cleanup': 'off',
    },
  },
  prettierConfig,
  {
    plugins: { prettier: prettierPlugin },
    rules: { 'prettier/prettier': 'error' },
  },
  {
    languageOptions: {
      parserOptions: {
        ecmaVersion: 2022,
        sourceType: 'module',
      },
    },
    rules: {
      '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
      'import-x/order': [
        'error',
        {
          groups: ['builtin', 'external', 'internal', 'parent', 'sibling', 'index', 'object'],
          'newlines-between': 'always',
          alphabetize: {
            order: 'asc',
            caseInsensitive: true,
          },
        },
      ],
      // Feature folders (src/{host,webview}/features/<name>/) only publish their index.ts
      // barrel; reaching past it into a feature's internals from outside couples callers to
      // implementation details the barrel is meant to hide.
      'no-restricted-imports': [
        'error',
        {
          patterns: ['webview/features/*/**', 'host/features/*/**'],
        },
      ],
    },
  },
  {
    ignores: ['out/**', 'node_modules/**', '*.vsix'],
  },
);
