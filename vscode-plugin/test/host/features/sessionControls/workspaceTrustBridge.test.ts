// © Copyright 2026 Aaron Kimball
import { describe, expect, it, vi } from 'vitest';

import { WorkspaceTrustBridge, type WorkspaceTrustVsCode } from 'host/features/sessionControls';

interface FakeVsCode extends WorkspaceTrustVsCode {
  trusted: boolean;
  grantListeners: (() => void)[];
  choice: string | undefined;
  infoMessages: string[];
}

function makeFakeVsCode(trusted: boolean): FakeVsCode {
  const grantListeners: (() => void)[] = [];
  const infoMessages: string[] = [];
  const fake: FakeVsCode = {
    trusted,
    grantListeners,
    choice: undefined,
    infoMessages,
    isTrusted: () => fake.trusted,
    onDidGrantWorkspaceTrust: (listener) => {
      grantListeners.push(listener);
      return { dispose: () => undefined };
    },
    showInformationMessage: async (message: string) => {
      infoMessages.push(message);
      return fake.choice;
    },
  };
  return fake;
}

function fakeSessionControls(workspaceTrusted: boolean | undefined) {
  const trustWorkspace = vi.fn(async () => ({ path: '/work', trusted: true }));
  return { workspaceTrusted, trustWorkspace };
}

describe('WorkspaceTrustBridge', () => {
  it('offers immediately when VS Code is trusted but the klorb workspace is not', async () => {
    const vs = makeFakeVsCode(true);
    vs.choice = 'Trust';
    const sessionControls = fakeSessionControls(false);
    const bridge = new WorkspaceTrustBridge(vs, sessionControls);

    await bridge.offerIfNeeded();

    expect(vs.infoMessages).toHaveLength(1);
    expect(sessionControls.trustWorkspace).toHaveBeenCalledOnce();
  });

  it('does not call trustWorkspace() when the user declines', async () => {
    const vs = makeFakeVsCode(true);
    vs.choice = 'Not now';
    const sessionControls = fakeSessionControls(false);
    const bridge = new WorkspaceTrustBridge(vs, sessionControls);

    await bridge.offerIfNeeded();

    expect(sessionControls.trustWorkspace).not.toHaveBeenCalled();
  });

  it('does not offer when the klorb workspace is already trusted', async () => {
    const vs = makeFakeVsCode(true);
    const sessionControls = fakeSessionControls(true);
    const bridge = new WorkspaceTrustBridge(vs, sessionControls);

    await bridge.offerIfNeeded();

    expect(vs.infoMessages).toHaveLength(0);
  });

  it('does not offer while VS Code itself is in Restricted Mode', async () => {
    const vs = makeFakeVsCode(false);
    const sessionControls = fakeSessionControls(false);
    const bridge = new WorkspaceTrustBridge(vs, sessionControls);

    await bridge.offerIfNeeded();

    expect(vs.infoMessages).toHaveLength(0);
  });

  it('offers once VS Code grants workspace trust after starting restricted', async () => {
    const vs = makeFakeVsCode(false);
    vs.choice = 'Trust';
    const sessionControls = fakeSessionControls(false);
    new WorkspaceTrustBridge(vs, sessionControls);

    vs.trusted = true;
    vs.grantListeners.forEach((listener) => listener());
    await vi.waitFor(() => expect(sessionControls.trustWorkspace).toHaveBeenCalledOnce());
  });

  it('only ever offers once per activation', async () => {
    const vs = makeFakeVsCode(true);
    vs.choice = 'Not now';
    const sessionControls = fakeSessionControls(false);
    const bridge = new WorkspaceTrustBridge(vs, sessionControls);

    await bridge.offerIfNeeded();
    await bridge.offerIfNeeded();

    expect(vs.infoMessages).toHaveLength(1);
  });

  it('logs, rather than throws, when trustWorkspace() fails', async () => {
    const vs = makeFakeVsCode(true);
    vs.choice = 'Trust';
    const sessionControls = fakeSessionControls(false);
    sessionControls.trustWorkspace.mockRejectedValueOnce(new Error('ext method failed'));
    const logs: string[] = [];
    const bridge = new WorkspaceTrustBridge(vs, sessionControls, (message) => logs.push(message));

    await expect(bridge.offerIfNeeded()).resolves.toBeUndefined();

    expect(logs).toEqual([expect.stringContaining('ext method failed')]);
  });
});
