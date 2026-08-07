// © Copyright 2026 Aaron Kimball
import { errorMessage, type AcpConnection, type LogFn, type SessionInfo } from 'host/features/acp';
import type { SkillEntry, ThinkingEffort } from 'shared/webviewMessages';

/** Every `PermissionFramework` value, in the order the badge cycles through -- mirrors
 * `klorb.tui.constants.PERMISSION_FRAMEWORK_CYCLE`. */
export const PERMISSION_MODE_CYCLE = ['ask', 'auto', 'deny'] as const;

export interface SessionModelInfo {
  id: string;
  name: string;
}

/** The `_klorb/getSessionConfig`/`_klorb/setSessionConfig` result shape -- see
 * docs/specs/klorb-server.md's "Model and thinking session config" section. */
export interface SessionConfigResult {
  model: { current: string; available: SessionModelInfo[] };
  thinking: { enabled: boolean; effort: ThinkingEffort };
  activeModelVision: boolean;
}

/** A `_klorb/setSessionConfig` request's params: every field left unset is left unchanged by
 * the server. */
export interface SessionConfigUpdateParams {
  model?: string;
  thinking?: { enabled?: boolean; effort?: ThinkingEffort };
}

export interface WorkspaceTrustResult {
  path: string;
  trusted: boolean;
}

/** The status row's data -- see `shared/webviewMessages.ts`'s `StatusUpdateMessage`, which this
 * mirrors minus the message envelope. */
export interface StatusSnapshot {
  model?: string;
  thinkingEnabled?: boolean;
  thinkingEffort?: ThinkingEffort;
  permissionMode?: string;
  usedTokens?: number;
  maxTokens?: number | null;
  outputTokens?: number;
  sessionTitle?: string | null;
  workspaceTrusted?: boolean;
  enqueueMessageCapable?: boolean;
  /** Whether the currently active model supports image input -- gates the prompt input's
   * attach-image affordance (see `webview/features/promptInput`). Fetched alongside
   * `model`/`thinking` via `_klorb/getSessionConfig`, since the active model can change
   * mid-session. */
  activeModelVision?: boolean;
  /** Whether the connected server advertised `_klorb/subagentTree` -- gates whether the
   * subagents panel polls/renders at all (see `AcpConnection.subagentsCapable`). */
  subagentsCapable?: boolean;
}

export type StatusListener = (status: StatusSnapshot) => void;

function isThinkingEffort(value: unknown): value is ThinkingEffort {
  return value === 'low' || value === 'medium' || value === 'high';
}

function isSessionModelInfo(value: unknown): value is SessionModelInfo {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  return typeof v.id === 'string' && typeof v.name === 'string';
}

function parseSessionConfigResult(value: Record<string, unknown>): SessionConfigResult {
  const model = value.model;
  const thinking = value.thinking;
  if (
    typeof model !== 'object' ||
    model === null ||
    typeof thinking !== 'object' ||
    thinking === null
  ) {
    throw new Error('klorb server returned a malformed session config result');
  }
  const m = model as Record<string, unknown>;
  const t = thinking as Record<string, unknown>;
  if (
    typeof m.current !== 'string' ||
    !Array.isArray(m.available) ||
    !m.available.every(isSessionModelInfo) ||
    typeof t.enabled !== 'boolean' ||
    !isThinkingEffort(t.effort) ||
    typeof value.activeModelVision !== 'boolean'
  ) {
    throw new Error('klorb server returned a malformed session config result');
  }
  return {
    model: { current: m.current, available: m.available },
    thinking: { enabled: t.enabled, effort: t.effort },
    activeModelVision: value.activeModelVision,
  };
}

function parseWorkspaceTrustResult(value: Record<string, unknown>): WorkspaceTrustResult {
  const workspace = value.workspace;
  if (typeof workspace !== 'object' || workspace === null) {
    throw new Error('klorb server returned a malformed trustWorkspace result');
  }
  const w = workspace as Record<string, unknown>;
  if (typeof w.path !== 'string' || typeof w.trusted !== 'boolean') {
    throw new Error('klorb server returned a malformed trustWorkspace result');
  }
  return { path: w.path, trusted: w.trusted };
}

/**
 * A thin, typed layer over `AcpConnection` exposing increment 008's control-plane surface
 * (session modes, model/thinking config, session stats, workspace trust, skills reload) and
 * coalescing the various ACP notifications that report control-plane state
 * (`current_mode_update`, `session_info_update`, `_klorb/usage`, a `_klorb/getSessionConfig`/
 * `_klorb/setSessionConfig` round trip) into one running `StatusSnapshot`, broadcast to
 * `onStatus` in full every time it changes -- see docs/specs/vscode-plugin.md's status row
 * section. The snapshot is always the *complete* currently-known state, not a delta, so a
 * fresh session (`applySessionInfo`) can reset stale fields (token tally, title) simply by
 * including them in the merge with a fresh value.
 */
export class SessionControls {
  private readonly _connection: AcpConnection;
  private readonly _onStatus: StatusListener;
  private readonly _log: LogFn;
  private _snapshot: StatusSnapshot = {};
  private _currentModeId: string | undefined;
  private _workspacePath: string | undefined;
  private _workspaceTrusted: boolean | undefined;

  public constructor(connection: AcpConnection, onStatus: StatusListener, log: LogFn) {
    this._connection = connection;
    this._onStatus = onStatus;
    this._log = log;
  }

  public get currentModeId(): string | undefined {
    return this._currentModeId;
  }

  public get workspaceTrusted(): boolean | undefined {
    return this._workspaceTrusted;
  }

  public get workspacePath(): string | undefined {
    return this._workspacePath;
  }

  /** Re-broadcasts the currently-known snapshot -- called when the webview is recreated (e.g.
   * after a reload) so the fresh instance sees the current status instead of the placeholder
   * defaults, mirroring `KlorbAcpClient.repostPendingInteraction()`. */
  public postSnapshot(): void {
    this._onStatus({ ...this._snapshot });
  }

  /** Applies `session/new`'s response state (mode, workspace trust, title), resets the
   * transient per-turn fields (token tally) and the not-yet-fetched config fields (model,
   * thinking) to unknown, then kicks off a background `getSessionConfig()` fetch to populate
   * them promptly -- called once per `session/new` (including `klorb.newSession`). */
  public applySessionInfo(info: SessionInfo): void {
    this._currentModeId = info.modeId;
    this._workspaceTrusted = info.workspaceTrusted;
    this._workspacePath = info.workspacePath;
    this._merge({
      permissionMode: info.modeId,
      workspaceTrusted: info.workspaceTrusted,
      sessionTitle: info.title ?? null,
      enqueueMessageCapable: info.enqueueMessageCapable,
      subagentsCapable: info.subagentsCapable,
      usedTokens: undefined,
      maxTokens: undefined,
      outputTokens: undefined,
      model: undefined,
      thinkingEnabled: undefined,
      thinkingEffort: undefined,
      activeModelVision: undefined,
    });
    void this.getSessionConfig().catch((err: unknown) => {
      this._log(`klorb: failed to fetch session config: ${errorMessage(err)}`);
    });
  }

  /** Applies a pushed `current_mode_update` -- see `applySessionInfo`'s own eager update for
   * why this is idempotent with a `setMode()`/`cyclePermissionMode()` call the client itself
   * just made. */
  public applyModeChanged(modeId: string): void {
    this._setCurrentMode(modeId);
  }

  public applySessionTitleChanged(title: string | null): void {
    this._merge({ sessionTitle: title });
  }

  public applyUsageUpdate(
    usedTokens: number,
    maxTokens: number | null,
    outputTokens: number
  ): void {
    this._merge({ usedTokens, maxTokens, outputTokens });
  }

  /** Reads the session's current model/thinking config (`_klorb/getSessionConfig`). */
  public async getSessionConfig(): Promise<SessionConfigResult> {
    const raw = await this._connection.extMethod('_klorb/getSessionConfig', {});
    const config = parseSessionConfigResult(raw);
    this._applyConfig(config);
    return config;
  }

  /** Changes the session's model/thinking config (`_klorb/setSessionConfig`); a field left
   * unset in `update` is left unchanged by the server. */
  public async setSessionConfig(update: SessionConfigUpdateParams): Promise<SessionConfigResult> {
    const raw = await this._connection.extMethod('_klorb/setSessionConfig', { ...update });
    const config = parseSessionConfigResult(raw);
    this._applyConfig(config);
    return config;
  }

  /** Changes the session's permission mode (`session/set_mode`). */
  public async setMode(modeId: string): Promise<void> {
    await this._connection.setSessionMode(modeId);
    this._setCurrentMode(modeId);
  }

  /** Advances the permission mode to the next value in `PERMISSION_MODE_CYCLE`, wrapping
   * around -- the badge-click/command behavior mirroring the TUI's Shift+Tab. */
  public async cyclePermissionMode(): Promise<void> {
    const current = this._currentModeId ?? 'ask';
    const index = PERMISSION_MODE_CYCLE.indexOf(current as (typeof PERMISSION_MODE_CYCLE)[number]);
    const next = PERMISSION_MODE_CYCLE[(index + 1) % PERMISSION_MODE_CYCLE.length]!;
    await this.setMode(next);
  }

  /** Fetches the `SessionStatistics` payload (`_klorb/sessionStats`), unparsed -- the caller
   * (a command handler) formats it for display. */
  public async sessionStats(): Promise<Record<string, unknown>> {
    return this._connection.extMethod('_klorb/sessionStats', {});
  }

  /** Trusts the session's workspace (`_klorb/trustWorkspace`). */
  public async trustWorkspace(): Promise<WorkspaceTrustResult> {
    const raw = await this._connection.extMethod('_klorb/trustWorkspace', {});
    const result = parseWorkspaceTrustResult(raw);
    this._workspaceTrusted = result.trusted;
    this._workspacePath = result.path;
    this._merge({ workspaceTrusted: result.trusted });
    return result;
  }

  /** Rebuilds the skill catalog (`_klorb/reloadSkills`), returning the resulting skill count. */
  public async reloadSkills(): Promise<number> {
    const raw = await this._connection.extMethod('_klorb/reloadSkills', {});
    const count = raw.skillCount;
    if (typeof count !== 'number') {
      throw new Error('klorb server returned a malformed _klorb/reloadSkills result');
    }
    return count;
  }

  /** Fetches every discoverable skill's namespace, name, and description (`_klorb/listSkills`). */
  public async listSkills(): Promise<SkillEntry[]> {
    const raw = await this._connection.extMethod('_klorb/listSkills', {});
    const entries = raw.skills;
    if (!Array.isArray(entries)) {
      throw new Error('klorb server returned a malformed _klorb/listSkills result');
    }
    return entries as SkillEntry[];
  }

  private _setCurrentMode(modeId: string): void {
    this._currentModeId = modeId;
    this._merge({ permissionMode: modeId });
  }

  private _applyConfig(config: SessionConfigResult): void {
    this._merge({
      model: config.model.current,
      thinkingEnabled: config.thinking.enabled,
      thinkingEffort: config.thinking.effort,
      activeModelVision: config.activeModelVision,
    });
  }

  private _merge(update: StatusSnapshot): void {
    this._snapshot = { ...this._snapshot, ...update };
    this._onStatus({ ...this._snapshot });
  }
}
