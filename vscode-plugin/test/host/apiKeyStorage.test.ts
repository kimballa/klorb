// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import {
  ApiKeyManager,
  OPENROUTER_API_KEY_SECRET_KEY,
  type ApiKeyVsCode,
} from 'host/apiKeyStorage';

interface FakeVsCode extends ApiKeyVsCode {
  secretsStore: Map<string, string>;
  inputBoxValue: string | undefined;
  infoMessages: string[];
}

function makeFakeVsCode(): FakeVsCode {
  const secretsStore = new Map<string, string>();
  const infoMessages: string[] = [];
  const fake: FakeVsCode = {
    secretsStore,
    inputBoxValue: undefined,
    infoMessages,
    secrets: {
      keys: async () => Array.from(secretsStore.keys()),
      get: async (key: string) => secretsStore.get(key),
      store: async (key: string, value: string) => {
        secretsStore.set(key, value);
      },
      delete: async (key: string) => {
        secretsStore.delete(key);
      },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      onDidChange: (() => ({ dispose: () => undefined })) as any,
    },
    showInputBox: async () => fake.inputBoxValue,
    showInformationMessage: async (message: string) => {
      infoMessages.push(message);
      return undefined;
    },
  };
  return fake;
}

describe('ApiKeyManager.resolve', () => {
  it('returns the stored secret', async () => {
    const vs = makeFakeVsCode();
    vs.secretsStore.set(OPENROUTER_API_KEY_SECRET_KEY, 'secret-key');
    const manager = new ApiKeyManager(vs);

    await expect(manager.resolve()).resolves.toBe('secret-key');
  });

  it('resolves undefined when no secret is set, so the environment passes through', async () => {
    const vs = makeFakeVsCode();
    const manager = new ApiKeyManager(vs);

    await expect(manager.resolve()).resolves.toBeUndefined();
  });
});

describe('ApiKeyManager.setApiKeyCommand', () => {
  it('stores a non-empty value', async () => {
    const vs = makeFakeVsCode();
    vs.inputBoxValue = 'new-key';
    const manager = new ApiKeyManager(vs);

    await manager.setApiKeyCommand();

    expect(vs.secretsStore.get(OPENROUTER_API_KEY_SECRET_KEY)).toBe('new-key');
  });

  it('deletes the stored secret on an empty submission', async () => {
    const vs = makeFakeVsCode();
    vs.secretsStore.set(OPENROUTER_API_KEY_SECRET_KEY, 'old-key');
    vs.inputBoxValue = '';
    const manager = new ApiKeyManager(vs);

    await manager.setApiKeyCommand();

    expect(vs.secretsStore.has(OPENROUTER_API_KEY_SECRET_KEY)).toBe(false);
  });

  it('does nothing when the input box is cancelled', async () => {
    const vs = makeFakeVsCode();
    vs.inputBoxValue = undefined;
    const manager = new ApiKeyManager(vs);

    await manager.setApiKeyCommand();

    expect(vs.secretsStore.has(OPENROUTER_API_KEY_SECRET_KEY)).toBe(false);
    expect(vs.infoMessages).toHaveLength(0);
  });
});

describe('ApiKeyManager.clearApiKeyCommand', () => {
  it('deletes the stored secret', async () => {
    const vs = makeFakeVsCode();
    vs.secretsStore.set(OPENROUTER_API_KEY_SECRET_KEY, 'old-key');
    const manager = new ApiKeyManager(vs);

    await manager.clearApiKeyCommand();

    expect(vs.secretsStore.has(OPENROUTER_API_KEY_SECRET_KEY)).toBe(false);
  });
});
