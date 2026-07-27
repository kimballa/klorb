// © Copyright 2026 Aaron Kimball
import { type JSX, useEffect, useRef, useState } from 'react';

import type { ThinkingEffort } from 'shared/webviewMessages';
import StatusMenu from 'webview/components/StatusMenu';
import { formatTokenCount } from 'webview/formatTokenCount';

export interface StatusRowProps {
  model?: string;
  thinkingEnabled?: boolean;
  thinkingEffort?: ThinkingEffort;
  permissionMode?: string;
  usedTokens?: number;
  maxTokens?: number | null;
  outputTokens?: number;
  taskPanelVisible: boolean;
  onPickModel(): void;
  onPickThinking(): void;
  onCyclePermissionMode(): void;
  onSetPermissionMode(): void;
  onShowSessionStats(): void;
  onNewSession(): void;
  onReloadSkills(): void;
  onToggleTaskPanel(): void;
}

const EFFORT_LABEL: Record<ThinkingEffort, string> = {
  low: 'Low',
  medium: 'Medium',
  high: 'High',
};

/** How long the permission badge's change-highlight flash lasts, mirroring the TUI's
 * `PermissionBadge.flash_to()` spirit without matching its exact timings (see
 * docs/specs/vscode-plugin.md). */
const FLASH_DURATION_MS = 500;

/** The thinking chip's label -- `Off`, `Low`, `Medium`, or `High`, mirroring the single
 * Off/Low/Medium/High QuickPick `onPickThinking` opens (see `commands.ts`'s
 * `pickThinkingState()`) -- or `...` before the first `_klorb/getSessionConfig` reply
 * (`enabled`/`effort` not yet known). */
function thinkingLabel(enabled: boolean | undefined, effort: ThinkingEffort | undefined): string {
  if (enabled === undefined || effort === undefined) {
    return '...';
  }
  return enabled ? EFFORT_LABEL[effort] : 'Off';
}

/** Renders the context-window tally (`↑ <used> / <limit>`, its `/ <limit>` omitted when `max`
 * is `null`) and the running output-token count (`↓ <output>`) as one space-joined chip;
 * either half is omitted until its own count is known. */
function tokenTally(
  used: number | undefined,
  max: number | null | undefined,
  output: number | undefined
): string | undefined {
  const parts: string[] = [];
  if (used !== undefined) {
    parts.push(
      max == null
        ? `↑ ${formatTokenCount(used)}`
        : `↑ ${formatTokenCount(used)} / ${formatTokenCount(max)}`
    );
  }
  if (output !== undefined) {
    parts.push(`↓ ${formatTokenCount(output)}`);
  }
  return parts.length > 0 ? parts.join('  ') : undefined;
}

/**
 * The docked status row under the prompt input: a leading chevron button opening the session
 * commands menu (`StatusMenu`), the model chip (click to pick a model), the thinking chip
 * (click to pick Off/Low/Medium/High), the permission-mode badge (click to cycle ask -> auto
 * -> deny -> ask, flashing briefly on change), and the token tally -- see
 * docs/specs/vscode-plugin.md.
 */
export default function StatusRow({
  model,
  thinkingEnabled,
  thinkingEffort,
  permissionMode,
  usedTokens,
  maxTokens,
  outputTokens,
  taskPanelVisible,
  onPickModel,
  onPickThinking,
  onCyclePermissionMode,
  onSetPermissionMode,
  onShowSessionStats,
  onNewSession,
  onReloadSkills,
  onToggleTaskPanel,
}: StatusRowProps): JSX.Element {
  const [flashing, setFlashing] = useState(false);
  const previousMode = useRef(permissionMode);

  useEffect(() => {
    const changed = previousMode.current !== undefined && previousMode.current !== permissionMode;
    previousMode.current = permissionMode;
    if (!changed) {
      return undefined;
    }
    setFlashing(true);
    const timer = setTimeout(() => setFlashing(false), FLASH_DURATION_MS);
    return () => clearTimeout(timer);
  }, [permissionMode]);

  const mode = permissionMode ?? 'ask';
  const tally = tokenTally(usedTokens, maxTokens, outputTokens);

  return (
    <div id="status-row">
      <StatusMenu
        taskPanelVisible={taskPanelVisible}
        onPickModel={onPickModel}
        onPickThinking={onPickThinking}
        onSetPermissionMode={onSetPermissionMode}
        onShowSessionStats={onShowSessionStats}
        onNewSession={onNewSession}
        onReloadSkills={onReloadSkills}
        onToggleTaskPanel={onToggleTaskPanel}
      />
      <button type="button" className="status-chip status-model" onClick={onPickModel}>
        {model ?? '...'}
      </button>
      <button type="button" className="status-chip status-thinking" onClick={onPickThinking}>
        {thinkingLabel(thinkingEnabled, thinkingEffort)}
      </button>
      <button
        type="button"
        className={`status-chip status-permission-badge status-permission-${mode}${flashing ? ' status-permission-badge-flash' : ''}`}
        onClick={onCyclePermissionMode}>
        [{mode}]
      </button>
      {tally !== undefined ? <span className="status-chip status-tokens">{tally}</span> : null}
    </div>
  );
}
