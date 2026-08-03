// © Copyright 2026 Aaron Kimball
import { type JSX, useEffect, useState } from 'react';

import type { SubagentNodeInfo } from 'shared/webviewMessages';

import { rowMarker } from '../subagentsModel';

export interface SubagentsPanelProps {
  nodes: SubagentNodeInfo[];
  /** `null` selects the root session; a real id selects that subagent -- mirrors
   * `SelectSubagentMessage.sessionId`. */
  selectedSessionId: string | null;
  /** The session id an outstanding ask belongs to, if it isn't the one currently selected --
   * `undefined`/`null` when nothing is waiting, or when the waiting session is already selected
   * (its ask renders directly in the interaction area, so no row marker is needed for it). */
  attentionSessionId?: string | null;
  onSelect(sessionId: string | null): void;
  onToggleVisibility(): void;
}

/** How long each blink phase lasts, mirroring the TUI's `_PANEL_TICK_INTERVAL_SECONDS` (0.6s)
 * -- see `klorb.tui.mixins.subagents_panel.SubagentsPanelMixin._tick_subagents_panel`. */
const BLINK_INTERVAL_MS = 600;

/** A ticking boolean flipped every `BLINK_INTERVAL_MS` -- the panel's own self-contained blink
 * timer, not lifted to `App` state since nothing outside this component reads it. */
function useBlinkPhase(): boolean {
  const [blinkOn, setBlinkOn] = useState(true);
  useEffect(() => {
    const timer = setInterval(() => setBlinkOn((prev) => !prev), BLINK_INTERVAL_MS);
    return () => clearInterval(timer);
  }, []);
  return blinkOn;
}

/**
 * A strip docked at the top of the sidebar, above `TaskPanel`, listing every session in the live
 * tree rooted at the root session -- the VSCode adaptation of the TUI's Ctrl+G right-hand panel
 * (`klorb.tui.widgets.subagents_panel.SubagentsPanel`). Renders nothing until the first
 * `subagentTreeUpdate` arrives (no dead chrome, mirroring `TaskPanel`). Each row shows its
 * address and title, with a leading `!`/`*` marker (see `rowMarker`); the footer shows the
 * selected row's role. Selection is click-driven only (no roving-tabindex arrow-key nav, a
 * deliberate scope reduction from the TUI's `OptionList`-based up/down navigation) -- see
 * docs/specs/vscode-plugin.md's "Subagents panel" section.
 */
export default function SubagentsPanel({
  nodes,
  selectedSessionId,
  attentionSessionId,
  onSelect,
  onToggleVisibility,
}: SubagentsPanelProps): JSX.Element | null {
  const blinkOn = useBlinkPhase();

  if (nodes.length === 0) {
    return null;
  }

  const selectedNode = nodes.find((node) =>
    selectedSessionId === null ? node.parentId === null : node.id === selectedSessionId
  );

  return (
    <div className="subagents-panel">
      <div className="subagents-panel-summary">
        <vscode-icon className="subagents-panel-icon" name="type-hierarchy" />
        <span className="subagents-panel-summary-text">Subagents</span>
        <vscode-icon
          className="subagents-panel-pin"
          name="pin"
          label="Hide subagents panel"
          title="Unpin subagents panel"
          actionIcon
          onClick={onToggleVisibility}
        />
      </div>
      <div className="subagents-panel-list" role="listbox" aria-label="Subagents">
        {nodes.map((node) => {
          const isRoot = node.parentId === null;
          const isSelected = isRoot ? selectedSessionId === null : selectedSessionId === node.id;
          const marker = rowMarker(node, attentionSessionId, blinkOn);
          return (
            <button
              key={node.id}
              type="button"
              role="option"
              aria-selected={isSelected}
              className={`subagents-panel-row${isSelected ? ' subagents-panel-row-selected' : ''}`}
              onClick={() => onSelect(isRoot ? null : node.id)}>
              <span className="subagents-panel-row-marker">{marker ?? ''}</span>
              <span className="subagents-panel-row-address">{node.address}</span>
              <span className="subagents-panel-row-title">{node.title ?? 'New session…'}</span>
            </button>
          );
        })}
      </div>
      <div className="subagents-panel-footer">Role: {selectedNode?.role ?? '...'}</div>
    </div>
  );
}
