// © Copyright 2026 Aaron Kimball
import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    ...reactHooks.configs['recommended-latest'],
    files: ['src/webview/**/*.tsx'],
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
    },
  },
  {
    ignores: ['out/**', 'node_modules/**', '*.vsix'],
  },
);
