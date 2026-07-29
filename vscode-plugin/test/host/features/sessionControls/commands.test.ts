// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import { AcpConnection, type SessionUpdateListener } from 'host/features/acp';
import {
  cyclePermissionModeCommand,
  reloadSkillsCommand,
  selectModelCommand,
  setPermissionModeCommand,
  setThinkingCommand,
  showSessionStatsCommand,
  SessionControls,
  type CommandsVsCode,
  type PickableItem,
  type SessionStatsData,
} from 'host/features/sessionControls';
import { KlorbServerProcess } from 'host/klorbServerProcess';

import { createMockAgentChild, MockAgent } from '../../../mockAgent';

const OPTIONS = { command: 'klorb', env: {} };
const NOOP_LISTENER: SessionUpdateListener = {
  onAgentText: () => undefined,
  onThoughtText: () => undefined,
  onToolCallStarted: () => undefined,
  onToolCallUpdated: () => undefined,
  postPermissionAsk: () => undefined,
  postQuestionAsk: () => undefined,
  postToolCallLimitAsk: () => undefined,
  onSessionInfo: () => undefined,
  onModeChanged: () => undefined,
  onSessionTitleChanged: () => undefined,
  onUsageUpdate: () => undefined,
  onTaskListUpdate: () => undefined,
  onMessageQueued: () => undefined,
  onQueuedMessageSent: () => undefined,
  onSessionReplay: () => undefined,
};

interface FakeVsCode extends CommandsVsCode {
  infoMessages: string[];
  errorMessages: string[];
  postedStats: SessionStatsData[];
  nextPick: PickableItem<unknown> | undefined;
}

function makeFakeVsCode(): FakeVsCode {
  const infoMessages: string[] = [];
  const errorMessages: string[] = [];
  const postedStats: SessionStatsData[] = [];
  const fake: FakeVsCode = {
    infoMessages,
    errorMessages,
    postedStats,
    nextPick: undefined,
    showQuickPick: async <T>(_items: PickableItem<T>[], _options: { placeHolder: string }) =>
      fake.nextPick as PickableItem<T> | undefined,
    showInformationMessage: async (message: string) => {
      infoMessages.push(message);
      return undefined;
    },
    showErrorMessage: async (message: string) => {
      errorMessages.push(message);
      return undefined;
    },
    postSessionStats: (data: SessionStatsData) => postedStats.push(data),
  };
  return fake;
}

async function makeSessionControls(agent: MockAgent): Promise<SessionControls> {
  const { child } = createMockAgentChild(agent);
  const serverProcess = new KlorbServerProcess(() => child);
  const connection = new AcpConnection(serverProcess, NOOP_LISTENER, () => undefined, 500);
  const sessionControls = new SessionControls(
    connection,
    () => undefined,
    () => undefined
  );
  await connection.start(OPTIONS, '/work');
  return sessionControls;
}

const SESSION_CONFIG_RESULT = {
  model: {
    current: 'gpt-5',
    available: [
      { id: 'gpt-5', name: 'gpt-5' },
      { id: 'gpt-5-mini', name: 'gpt-5-mini' },
    ],
  },
  thinking: { enabled: false, effort: 'medium' },
};

describe('selectModelCommand', () => {
  it('sets the picked model', async () => {
    const agent = new MockAgent();
    agent.onExtMethod = async () => SESSION_CONFIG_RESULT;
    const sessionControls = await makeSessionControls(agent);
    const vs = makeFakeVsCode();
    vs.nextPick = { label: 'gpt-5-mini', value: 'gpt-5-mini' };

    await selectModelCommand(sessionControls, vs);

    expect(agent.receivedExtMethods).toContainEqual(
      expect.objectContaining({
        method: '_klorb/setSessionConfig',
        params: expect.objectContaining({ model: 'gpt-5-mini' }),
      })
    );
  });

  it('does nothing when the QuickPick is dismissed', async () => {
    const agent = new MockAgent();
    agent.onExtMethod = async () => SESSION_CONFIG_RESULT;
    const sessionControls = await makeSessionControls(agent);
    const vs = makeFakeVsCode();
    vs.nextPick = undefined;

    await selectModelCommand(sessionControls, vs);

    expect(agent.receivedExtMethods.map((r) => r.method)).not.toContain('_klorb/setSessionConfig');
  });

  it('reports a failure via showErrorMessage', async () => {
    const agent = new MockAgent();
    agent.onExtMethod = async () => {
      throw new Error('boom');
    };
    const sessionControls = await makeSessionControls(agent);
    const vs = makeFakeVsCode();

    await selectModelCommand(sessionControls, vs);

    // A thrown Error crossing the wire loses its original message -- the SDK's own request
    // handling replaces it with a generic JSON-RPC "Internal error".
    expect(vs.errorMessages).toEqual(['Klorb: Internal error']);
  });
});

describe('setThinkingCommand', () => {
  it('enables thinking at the picked effort, in one QuickPick', async () => {
    const agent = new MockAgent();
    agent.onExtMethod = async () => SESSION_CONFIG_RESULT;
    const sessionControls = await makeSessionControls(agent);
    const vs = makeFakeVsCode();
    vs.nextPick = { label: 'High', value: 'high' };

    await setThinkingCommand(sessionControls, vs);

    expect(agent.receivedExtMethods).toContainEqual(
      expect.objectContaining({
        method: '_klorb/setSessionConfig',
        params: expect.objectContaining({ thinking: { enabled: true, effort: 'high' } }),
      })
    );
  });

  it('disables thinking when Off is picked', async () => {
    const agent = new MockAgent();
    agent.onExtMethod = async () => SESSION_CONFIG_RESULT;
    const sessionControls = await makeSessionControls(agent);
    const vs = makeFakeVsCode();
    vs.nextPick = { label: 'Off', value: 'off' };

    await setThinkingCommand(sessionControls, vs);

    expect(agent.receivedExtMethods).toContainEqual(
      expect.objectContaining({
        method: '_klorb/setSessionConfig',
        params: expect.objectContaining({ thinking: { enabled: false } }),
      })
    );
  });

  it('does nothing when the QuickPick is dismissed', async () => {
    const agent = new MockAgent();
    agent.onExtMethod = async () => SESSION_CONFIG_RESULT;
    const sessionControls = await makeSessionControls(agent);
    const vs = makeFakeVsCode();
    vs.nextPick = undefined;

    await setThinkingCommand(sessionControls, vs);

    expect(agent.receivedExtMethods.map((r) => r.method)).not.toContain('_klorb/setSessionConfig');
  });
});

describe('cyclePermissionModeCommand', () => {
  it('sends session/set_mode for the next mode', async () => {
    const agent = new MockAgent();
    const sessionControls = await makeSessionControls(agent);
    const vs = makeFakeVsCode();

    await cyclePermissionModeCommand(sessionControls, vs);

    expect(agent.receivedSetSessionModes).toEqual([{ sessionId: 'sess-1', modeId: 'auto' }]);
  });
});

describe('setPermissionModeCommand', () => {
  it('jumps straight to the picked mode, not just the next one in the cycle', async () => {
    const agent = new MockAgent();
    const sessionControls = await makeSessionControls(agent);
    const vs = makeFakeVsCode();
    vs.nextPick = { label: 'Deny', value: 'deny' };

    await setPermissionModeCommand(sessionControls, vs);

    expect(agent.receivedSetSessionModes).toEqual([{ sessionId: 'sess-1', modeId: 'deny' }]);
  });

  it('does nothing when the QuickPick is dismissed', async () => {
    const agent = new MockAgent();
    const sessionControls = await makeSessionControls(agent);
    const vs = makeFakeVsCode();
    vs.nextPick = undefined;

    await setPermissionModeCommand(sessionControls, vs);

    expect(agent.receivedSetSessionModes).toEqual([]);
  });

  it('reports a failure via showErrorMessage', async () => {
    const agent = new MockAgent();
    const sessionControls = await makeSessionControls(agent);
    const vs = makeFakeVsCode();
    vs.nextPick = { label: 'Deny', value: 'deny' };
    agent.onSetSessionMode = async () => {
      throw new Error('boom');
    };

    await setPermissionModeCommand(sessionControls, vs);

    expect(vs.errorMessages).toEqual(['Klorb: Internal error']);
  });
});

describe('showSessionStatsCommand', () => {
  it('posts the formatted stats for the webview to render', async () => {
    const agent = new MockAgent();
    agent.onExtMethod = async () => ({ user_messages: 2, tool_calls: 1, input_tokens: 100 });
    const sessionControls = await makeSessionControls(agent);
    const vs = makeFakeVsCode();

    await showSessionStatsCommand(sessionControls, vs);

    expect(vs.postedStats).toHaveLength(1);
    expect(vs.postedStats[0]).toMatchObject({
      messageCounts: expect.objectContaining({ 'User messages': 2, 'Total tool calls': 1 }),
      tokenUsage: expect.objectContaining({ 'Input tokens': 100 }),
    });
  });

  it('reports a failure via showErrorMessage', async () => {
    const agent = new MockAgent();
    agent.onExtMethod = async () => {
      throw new Error('boom');
    };
    const sessionControls = await makeSessionControls(agent);
    const vs = makeFakeVsCode();

    await showSessionStatsCommand(sessionControls, vs);

    expect(vs.postedStats).toHaveLength(0);
    expect(vs.errorMessages).toEqual(['Klorb: Internal error']);
  });
});

describe('reloadSkillsCommand', () => {
  it('reports the reloaded skill count', async () => {
    const agent = new MockAgent();
    agent.onExtMethod = async () => ({ skillCount: 7 });
    const sessionControls = await makeSessionControls(agent);
    const vs = makeFakeVsCode();

    await reloadSkillsCommand(sessionControls, vs);

    expect(vs.infoMessages).toEqual(['Klorb: reloaded 7 skill(s).']);
  });
});
