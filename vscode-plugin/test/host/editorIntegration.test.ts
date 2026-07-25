// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import { EditorIntegration, type EditorIntegrationVsCode } from 'host/editorIntegration';

/** A minimal fake `vscode.Uri`-like value: just enough for `EditorIntegration`'s own use
 * (`.toString()` as a map key) without depending on the real `vscode` module. */
interface FakeUri {
  value: string;
  toString(): string;
}

function fakeUri(value: string): FakeUri {
  return { value, toString: () => value };
}

interface FakeVsCode {
  api: EditorIntegrationVsCode;
  openedDocuments: string[];
  shownDocuments: unknown[];
  revealedLines: number[];
  warnings: string[];
  diffCalls: { oldUri: string; newUri: string; title: string }[];
  contentProviderScheme: string | undefined;
  openTextDocumentShouldThrow: boolean;
}

function makeFakeVsCode(): FakeVsCode {
  const state: FakeVsCode = {
    openedDocuments: [],
    shownDocuments: [],
    revealedLines: [],
    warnings: [],
    diffCalls: [],
    contentProviderScheme: undefined,
    openTextDocumentShouldThrow: false,
    api: undefined as unknown as EditorIntegrationVsCode,
  };
  state.api = {
    fileUri: (path: string) => fakeUri(`file://${path}`) as unknown as never,
    parseUri: (value: string) => fakeUri(value) as unknown as never,
    openTextDocument: async (uri) => {
      if (state.openTextDocumentShouldThrow) {
        throw new Error('no such file');
      }
      state.openedDocuments.push((uri as unknown as FakeUri).value);
      return { uri } as never;
    },
    showTextDocument: async (document) => {
      state.shownDocuments.push(document);
      return { selection: undefined, revealRange: () => undefined } as never;
    },
    revealLine: (_editor, line) => {
      state.revealedLines.push(line);
    },
    showWarningMessage: (message) => {
      state.warnings.push(message);
    },
    registerDiffContentProvider: (scheme) => {
      state.contentProviderScheme = scheme;
      return { dispose: () => undefined };
    },
    openDiffEditor: async (oldUri, newUri, title) => {
      state.diffCalls.push({
        oldUri: (oldUri as unknown as FakeUri).value,
        newUri: (newUri as unknown as FakeUri).value,
        title,
      });
    },
  };
  return state;
}

describe('EditorIntegration', () => {
  it('registers the diff content provider under the klorb-diff scheme on construction', () => {
    const fake = makeFakeVsCode();
    new EditorIntegration(fake.api);
    expect(fake.contentProviderScheme).toBe('klorb-diff');
  });

  it('openLocation opens the document, shows it, and reveals the given line', async () => {
    const fake = makeFakeVsCode();
    const integration = new EditorIntegration(fake.api);

    await integration.openLocation('/tmp/foo.py', 3);

    expect(fake.openedDocuments).toEqual(['file:///tmp/foo.py']);
    expect(fake.shownDocuments).toHaveLength(1);
    expect(fake.revealedLines).toEqual([3]);
    expect(fake.warnings).toEqual([]);
  });

  it('openLocation does not reveal a line when none is given', async () => {
    const fake = makeFakeVsCode();
    const integration = new EditorIntegration(fake.api);

    await integration.openLocation('/tmp/foo.py');

    expect(fake.revealedLines).toEqual([]);
  });

  it('openLocation warns instead of throwing when the document cannot be opened', async () => {
    const fake = makeFakeVsCode();
    fake.openTextDocumentShouldThrow = true;
    const integration = new EditorIntegration(fake.api);

    await integration.openLocation('/tmp/missing.py');

    expect(fake.warnings).toEqual(['Klorb: could not open /tmp/missing.py']);
  });

  it('openDiff shows a real diff for a recorded payload', async () => {
    const fake = makeFakeVsCode();
    const integration = new EditorIntegration(fake.api);

    integration.recordDiff('call-1', { oldText: 'old', newText: 'new' });
    await integration.openDiff('call-1', '/tmp/foo.py');

    expect(fake.diffCalls).toEqual([
      {
        oldUri: 'klorb-diff:/call-1/before/foo.py',
        newUri: 'klorb-diff:/call-1/after/foo.py',
        title: 'foo.py (Klorb edit)',
      },
    ]);
  });

  it('openDiff warns instead of opening anything when no diff was recorded for the call', async () => {
    const fake = makeFakeVsCode();
    const integration = new EditorIntegration(fake.api);

    await integration.openDiff('call-unknown', '/tmp/foo.py');

    expect(fake.diffCalls).toEqual([]);
    expect(fake.warnings).toEqual(['Klorb: no stored diff for this tool call (/tmp/foo.py)']);
  });

  it('evicts the oldest recorded diff once the bounded map is full', async () => {
    const fake = makeFakeVsCode();
    const integration = new EditorIntegration(fake.api);

    for (let i = 0; i < 51; i++) {
      integration.recordDiff(`call-${i}`, { oldText: null, newText: `new-${i}` });
    }

    await integration.openDiff('call-0', '/tmp/evicted.py');
    expect(fake.warnings).toEqual(['Klorb: no stored diff for this tool call (/tmp/evicted.py)']);

    await integration.openDiff('call-50', '/tmp/kept.py');
    expect(fake.diffCalls).toHaveLength(1);
  });
});
