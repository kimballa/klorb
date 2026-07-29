// © Copyright 2026 Aaron Kimball
import { describe, expect, it, vi } from 'vitest';

import { AcpConnection, type SessionUpdateListener } from 'host/features/acp';
import { SessionControls, type StatusSnapshot } from 'host/features/sessionControls';
import { KlorbServerProcess } from 'host/klorbServerProcess';

import { createMockAgentChild, MockAgent } from '../../../mockAgent';

const OPTIONS = { command: 'klorb', env: {} };

interface Harness {
  agent: MockAgent;
  connection: AcpConnection;
  sessionControls: SessionControls;
  statuses: StatusSnapshot[];
}

function makeHarness(agent: MockAgent = new MockAgent()): Harness {
  const { child } = createMockAgentChild(agent);
  const serverProcess = new KlorbServerProcess(() => child);
  const statuses: StatusSnapshot[] = [];
  // A mutable box, not a reassigned `let`, breaks the forward-reference cycle between the
  // listener (needed to construct `AcpConnection`) and `SessionControls` (which wraps it).
  const box: { sessionControls: SessionControls | undefined } = { sessionControls: undefined };
  const listener: SessionUpdateListener = {
    onAgentText: () => undefined,
    onThoughtText: () => undefined,
    onToolCallStarted: () => undefined,
    onToolCallUpdated: () => undefined,
    postPermissionAsk: () => undefined,
    postQuestionAsk: () => undefined,
    postToolCallLimitAsk: () => undefined,
    onSessionInfo: (info) => box.sessionControls?.applySessionInfo(info),
    onModeChanged: (modeId) => box.sessionControls?.applyModeChanged(modeId),
    onSessionTitleChanged: (title) => box.sessionControls?.applySessionTitleChanged(title),
    onUsageUpdate: (usedTokens, maxTokens, outputTokens) =>
      box.sessionControls?.applyUsageUpdate(usedTokens, maxTokens, outputTokens),
    onTaskListUpdate: () => undefined,
    onMessageQueued: () => undefined,
    onQueuedMessageSent: () => undefined,
    onSessionReplay: () => undefined,
  };
  const connection = new AcpConnection(serverProcess, listener, () => undefined, 500);
  const sessionControls = new SessionControls(
    connection,
    (status) => statuses.push(status),
    () => undefined
  );
  box.sessionControls = sessionControls;
  return { agent, connection, sessionControls, statuses };
}

const SESSION_CONFIG_RESULT = {
  model: { current: 'gpt-5', available: [{ id: 'gpt-5', name: 'gpt-5' }] },
  thinking: { enabled: true, effort: 'high' },
};

describe('SessionControls', () => {
  it('applies session/new state and fetches the initial config in the background', async () => {
    const agent = new MockAgent();
    agent.newSessionResult = {
      modes: { currentModeId: 'ask', availableModes: [{ id: 'ask', name: 'Ask before acting' }] },
      _meta: { klorb: { workspace: { path: '/work', trusted: false }, title: null } },
    };
    agent.onExtMethod = async () => SESSION_CONFIG_RESULT;
    const { connection, sessionControls, statuses } = makeHarness(agent);

    await connection.start(OPTIONS, '/work');

    expect(sessionControls.currentModeId).toBe('ask');
    expect(sessionControls.workspaceTrusted).toBe(false);
    // The eager session/new snapshot, then the background getSessionConfig() fetch.
    expect(statuses[0]).toMatchObject({
      permissionMode: 'ask',
      workspaceTrusted: false,
      sessionTitle: null,
    });
    await vi.waitFor(() => expect(statuses.some((s) => s.model === 'gpt-5')).toBe(true));
    const configured = statuses[statuses.length - 1]!;
    expect(configured).toMatchObject({
      permissionMode: 'ask',
      workspaceTrusted: false,
      model: 'gpt-5',
      thinkingEnabled: true,
      thinkingEffort: 'high',
    });
  });

  it('coalesces statusUpdate from interleaved mode/title/usage pushes into one running snapshot', async () => {
    // No `onExtMethod` scripted: `applySessionInfo`'s own background `getSessionConfig()`
    // fetch (kicked off by `connection.start()`, below) then rejects and is swallowed rather
    // than racing an extra status push in against this test's own three.
    const agent = new MockAgent();
    const { connection, statuses } = makeHarness(agent);
    await connection.start(OPTIONS, '/work');
    statuses.length = 0;

    await agent.sendUpdate('sess-1', {
      sessionUpdate: 'session_info_update',
      title: 'Fix the bug',
    });
    await agent.sendUpdate('sess-1', {
      sessionUpdate: 'current_mode_update',
      currentModeId: 'auto',
    });
    await agent.sendExtNotification('_klorb/usage', {
      usedTokens: 1400,
      maxTokens: 128000,
      outputTokens: 300,
    });

    expect(statuses).toHaveLength(3);
    // Each broadcast carries the full running snapshot, not just its own delta.
    expect(statuses[2]).toMatchObject({
      sessionTitle: 'Fix the bug',
      permissionMode: 'auto',
      usedTokens: 1400,
      maxTokens: 128000,
      outputTokens: 300,
    });
  });

  it('setMode() applies eagerly and sends session/set_mode', async () => {
    const { agent, connection, sessionControls } = makeHarness();
    await connection.start(OPTIONS, '/work');

    await sessionControls.setMode('auto');

    expect(sessionControls.currentModeId).toBe('auto');
    expect(agent.receivedSetSessionModes).toEqual([{ sessionId: 'sess-1', modeId: 'auto' }]);
  });

  it('cyclePermissionMode() advances ask -> auto -> deny -> ask', async () => {
    const { agent, connection, sessionControls } = makeHarness();
    await connection.start(OPTIONS, '/work');

    await sessionControls.cyclePermissionMode();
    expect(sessionControls.currentModeId).toBe('auto');
    await sessionControls.cyclePermissionMode();
    expect(sessionControls.currentModeId).toBe('deny');
    await sessionControls.cyclePermissionMode();
    expect(sessionControls.currentModeId).toBe('ask');
    expect(agent.receivedSetSessionModes.map((r) => r.modeId)).toEqual(['auto', 'deny', 'ask']);
  });

  it('getSessionConfig()/setSessionConfig() round-trip through _klorb/*SessionConfig', async () => {
    const agent = new MockAgent();
    agent.onExtMethod = async () => SESSION_CONFIG_RESULT;
    const { connection, sessionControls } = makeHarness(agent);
    await connection.start(OPTIONS, '/work');

    const config = await sessionControls.getSessionConfig();
    expect(config.model.current).toBe('gpt-5');

    await sessionControls.setSessionConfig({ model: 'gpt-5' });
    expect(agent.receivedExtMethods.map((r) => r.method)).toContain('_klorb/setSessionConfig');
  });

  it('trustWorkspace() round-trips through _klorb/trustWorkspace', async () => {
    const agent = new MockAgent();
    agent.onExtMethod = async () => ({ workspace: { path: '/work', trusted: true } });
    const { connection, sessionControls, statuses } = makeHarness(agent);
    await connection.start(OPTIONS, '/work');
    statuses.length = 0;

    const result = await sessionControls.trustWorkspace();

    expect(result).toEqual({ path: '/work', trusted: true });
    expect(sessionControls.workspaceTrusted).toBe(true);
    expect(statuses).toContainEqual(expect.objectContaining({ workspaceTrusted: true }));
  });

  it('sessionStats()/reloadSkills() round-trip through their ext methods', async () => {
    const agent = new MockAgent();
    agent.onExtMethod = async (method) => {
      if (method === '_klorb/sessionStats') {
        return { user_messages: 3 };
      }
      if (method === '_klorb/reloadSkills') {
        return { skillCount: 5 };
      }
      return {};
    };
    const { connection, sessionControls } = makeHarness(agent);
    await connection.start(OPTIONS, '/work');

    await expect(sessionControls.sessionStats()).resolves.toEqual({ user_messages: 3 });
    await expect(sessionControls.reloadSkills()).resolves.toBe(5);
  });
});
