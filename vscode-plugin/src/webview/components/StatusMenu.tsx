// © Copyright 2026 Aaron Kimball
import type { VscodeContextMenu } from '@vscode-elements/elements';
import type { VscContextMenuSelectEvent } from '@vscode-elements/elements/dist/vscode-context-menu/vscode-context-menu';
import { type JSX, useRef } from 'react';

import IconButton from 'webview/components/IconButton';

/** Gap, in pixels, left between the chevron button's top edge and the popup menu's bottom
 * edge when it opens upward above it. */
const MENU_GAP_PX = 4;

export interface StatusMenuProps {
  taskPanelVisible: boolean;
  onPickModel(): void;
  onPickThinking(): void;
  onSetPermissionMode(): void;
  onShowSessionStats(): void;
  onNewSession(): void;
  onReloadSkills(): void;
  onToggleTaskPanel(): void;
}

type StatusMenuAction =
  | 'model'
  | 'thinking'
  | 'permissionMode'
  | 'sessionStats'
  | 'newSession'
  | 'reloadSkills'
  | 'toggleTaskPanel';

/** The task-panel item's own label reflects current visibility -- it's the only way to bring
 * the panel back once its own header pin has hidden it (see `TaskPanel.tsx`'s `onToggleVisibility`
 * and docs/specs/vscode-plugin.md's "Task panel" section), so it reads as an action name rather
 * than a static, state-blind "Toggle Task Panel". */
export function menuItems(taskPanelVisible: boolean): { label: string; value: StatusMenuAction }[] {
  return [
    { label: 'Set Model…', value: 'model' },
    { label: 'Set Thinking…', value: 'thinking' },
    { label: 'Set Permission Mode', value: 'permissionMode' },
    { label: 'Session Stats', value: 'sessionStats' },
    { label: 'New Session', value: 'newSession' },
    { label: 'Reload Skills', value: 'reloadSkills' },
    {
      label: taskPanelVisible ? 'Hide Task Panel' : 'Show Task Panel',
      value: 'toggleTaskPanel',
    },
  ];
}

/**
 * The status row's leading chevron button: pops open a `vscode-context-menu` above the row (it
 * opens upward since the row is docked at the bottom of the panel) listing the session commands
 * that don't otherwise have their own status-row chip -- see docs/specs/vscode-plugin.md's
 * "Status row and session controls" section. The menu is positioned with `position: fixed` and
 * an inline `top`/`left` computed from the button's own `getBoundingClientRect()` rather than
 * CSS alone: `vscode-context-menu`'s own shadow-DOM styles set `:host { position: relative }`,
 * which an ordinary page-level stylesheet rule can't reliably out-specificity, but an inline
 * style on the host element always wins over any stylesheet (shadow-root or page) that doesn't
 * mark itself `!important`. `position: fixed` also sidesteps needing the row (or any ancestor)
 * to be a positioning/overflow container the popup could otherwise be clipped by. The menu
 * element manages its own open/close state (outside click, Escape, item pick); the button only
 * ever imperatively opens it via `menuRef`, so there's no React state to keep in sync with the
 * element's internal visibility.
 */
export default function StatusMenu({
  taskPanelVisible,
  onPickModel,
  onPickThinking,
  onSetPermissionMode,
  onShowSessionStats,
  onNewSession,
  onReloadSkills,
  onToggleTaskPanel,
}: StatusMenuProps): JSX.Element {
  const menuRef = useRef<VscodeContextMenu>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  function openMenu(): void {
    const menu = menuRef.current;
    const button = buttonRef.current;
    if (menu === null || button === null) {
      return;
    }
    const rect = button.getBoundingClientRect();
    menu.style.position = 'fixed';
    menu.style.left = `${rect.left}px`;
    menu.style.bottom = `${window.innerHeight - rect.top + MENU_GAP_PX}px`;
    menu.show = true;
  }

  function handleSelect(event: VscContextMenuSelectEvent): void {
    switch (event.detail.value as StatusMenuAction) {
      case 'model':
        onPickModel();
        break;
      case 'thinking':
        onPickThinking();
        break;
      case 'permissionMode':
        onSetPermissionMode();
        break;
      case 'sessionStats':
        onShowSessionStats();
        break;
      case 'newSession':
        onNewSession();
        break;
      case 'reloadSkills':
        onReloadSkills();
        break;
      case 'toggleTaskPanel':
        onToggleTaskPanel();
        break;
    }
  }

  return (
    <>
      <IconButton
        ref={buttonRef}
        variant="filled"
        title="Klorb session commands"
        onClick={openMenu}>
        <vscode-icon name="chevron-up" />
      </IconButton>
      <vscode-context-menu
        ref={menuRef}
        className="status-menu-popup"
        data={menuItems(taskPanelVisible)}
        onvsc-context-menu-select={handleSelect}
      />
    </>
  );
}
