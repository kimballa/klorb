// © Copyright 2026 Aaron Kimball
import { errorMessage } from 'host/features/acp';
import type { ThinkingEffort } from 'shared/webviewMessages';

import { formatSessionStats, type SessionStatsData } from './formatSessionStats';
import type { SessionControls } from './sessionControls';

/** One selectable `showQuickPick` item, carrying the value it resolves to alongside its
 * display label -- VS Code's real `QuickPickItem` supports exactly this shape. */
export interface PickableItem<T> {
  label: string;
  description?: string;
  value: T;
}

/** The subset of live `vscode` APIs the command handlers below need, injected so this module
 * never imports the real `vscode` runtime value (see host/editorIntegration.ts's own doc
 * comment for why -- the same seam lets this load under vitest without a running extension
 * host). `postSessionStats` isn't a `vscode` API at all, but the same injectable-facade seam
 * is what lets `showSessionStatsCommand` post a `sessionStats` message to the webview without
 * this module depending on `KlorbSessionViewProvider` directly. */
export interface CommandsVsCode {
  showQuickPick<T>(
    items: PickableItem<T>[],
    options: { placeHolder: string }
  ): Thenable<PickableItem<T> | undefined>;
  showInformationMessage(message: string): Thenable<string | undefined>;
  showErrorMessage(message: string): Thenable<string | undefined>;
  postSessionStats(data: SessionStatsData): void;
}

const EFFORT_LABEL: Record<ThinkingEffort, string> = { low: 'Low', medium: 'Medium', high: 'High' };
const THINKING_EFFORTS: readonly ThinkingEffort[] = ['low', 'medium', 'high'];

/** A `Thinking` QuickPick's resolved choice: `'off'` disables thinking outright, an effort
 * level enables it at that effort -- see `pickThinkingState()`'s own doc comment for why
 * `enabled` and `effort` are offered as one four-way choice instead of two separate ones. */
type ThinkingChoice = 'off' | ThinkingEffort;

/** Presents thinking's on/off state and its effort level as one QuickPick -- `Off`, `Low`,
 * `Medium`, `High` -- with the currently-active one marked `current`, rather than two separate
 * questions (an enable/disable toggle, then a follow-up effort picker only when enabling).
 * One control makes the four states mutually exclusive and
 * equally reachable in a single selection. */
async function pickThinkingState(
  vs: CommandsVsCode,
  current: { enabled: boolean; effort: ThinkingEffort }
): Promise<ThinkingChoice | undefined> {
  const currentChoice: ThinkingChoice = current.enabled ? current.effort : 'off';
  const items: PickableItem<ThinkingChoice>[] = [
    { label: 'Off', description: currentChoice === 'off' ? 'current' : undefined, value: 'off' },
    ...THINKING_EFFORTS.map((effort) => ({
      label: EFFORT_LABEL[effort],
      description: effort === currentChoice ? 'current' : undefined,
      value: effort,
    })),
  ];
  const picked = await vs.showQuickPick(items, { placeHolder: 'Thinking' });
  return picked?.value;
}

/** `klorb.selectModel`: QuickPick of the session's available models, current one marked. */
export async function selectModelCommand(
  sessionControls: SessionControls,
  vs: CommandsVsCode
): Promise<void> {
  try {
    const config = await sessionControls.getSessionConfig();
    const items: PickableItem<string>[] = config.model.available.map((model) => ({
      label: model.name,
      description: model.id === config.model.current ? 'current' : undefined,
      value: model.id,
    }));
    const picked = await vs.showQuickPick(items, { placeHolder: 'Select a model' });
    if (picked === undefined) {
      return;
    }
    await sessionControls.setSessionConfig({ model: picked.value });
  } catch (err) {
    await vs.showErrorMessage(`Klorb: ${errorMessage(err)}`);
  }
}

/** `klorb.setThinking`: one QuickPick (`Off`/`Low`/`Medium`/`High`) covering both whether
 * thinking is enabled and its effort level -- see `pickThinkingState()`'s own doc comment. */
export async function setThinkingCommand(
  sessionControls: SessionControls,
  vs: CommandsVsCode
): Promise<void> {
  try {
    const config = await sessionControls.getSessionConfig();
    const choice = await pickThinkingState(vs, config.thinking);
    if (choice === undefined) {
      return;
    }
    await sessionControls.setSessionConfig(
      choice === 'off'
        ? { thinking: { enabled: false } }
        : { thinking: { enabled: true, effort: choice } }
    );
  } catch (err) {
    await vs.showErrorMessage(`Klorb: ${errorMessage(err)}`);
  }
}

/** `klorb.cyclePermissionMode` (also reachable from the status row badge). */
export async function cyclePermissionModeCommand(
  sessionControls: SessionControls,
  vs: CommandsVsCode
): Promise<void> {
  try {
    await sessionControls.cyclePermissionMode();
  } catch (err) {
    await vs.showErrorMessage(`Klorb: ${errorMessage(err)}`);
  }
}

/** `klorb.showSessionStats`: renders a `SessionStatsCard` into the history, mirroring the
 * TUI's own "Show session stats" notice rather than dumping to the output channel. */
export async function showSessionStatsCommand(
  sessionControls: SessionControls,
  vs: CommandsVsCode
): Promise<void> {
  try {
    const stats = await sessionControls.sessionStats();
    vs.postSessionStats(formatSessionStats(stats));
  } catch (err) {
    await vs.showErrorMessage(`Klorb: ${errorMessage(err)}`);
  }
}

/** `klorb.reloadSkills`. */
export async function reloadSkillsCommand(
  sessionControls: SessionControls,
  vs: CommandsVsCode
): Promise<void> {
  try {
    const count = await sessionControls.reloadSkills();
    await vs.showInformationMessage(`Klorb: reloaded ${count} skill(s).`);
  } catch (err) {
    await vs.showErrorMessage(`Klorb: ${errorMessage(err)}`);
  }
}
