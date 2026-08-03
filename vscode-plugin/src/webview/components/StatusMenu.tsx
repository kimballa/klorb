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
  subagentsPanelVisible: boolean;
  /** Whether the active model supports image input -- shows/hides the "Attach Image…" item
   * (see `PromptInput`'s `imagesCapable` prop for the drag/paste side of this same gate). */
  activeModelVision?: boolean;
  onPickModel(): void;
  onPickThinking(): void;
  onSetPermissionMode(): void;
  onShowSessionStats(): void;
  onNewSession(): void;
  onReloadSkills(): void;
  onToggleTaskPanel(): void;
  onToggleSubagentsPanel(): void;
  onAttachImage(): void;
}

type StatusMenuAction =
  | 'model'
  | 'thinking'
  | 'permissionMode'
  | 'sessionStats'
  | 'newSession'
  | 'reloadSkills'
  | 'toggleTaskPanel'
  | 'toggleSubagentsPanel'
  | 'attachImage';

/** The task-panel/subagents-panel items' own labels reflect current visibility -- each is the
 * only way to bring its panel back once its own header pin has hidden it (see `TaskPanel.tsx`'s
 * `onToggleVisibility` and docs/specs/vscode-plugin.md's "Task panel"/"Subagents panel"
 * sections), so they read as action names rather than a static, state-blind "Toggle …Panel".
 * `attachImage` is included only when `activeModelVision` is `true` -- attaching against a
 * non-vision model can only fail server-side, so the item is hidden rather than shown-but-erroring. */
export function menuItems(
  taskPanelVisible: boolean,
  subagentsPanelVisible: boolean,
  activeModelVision: boolean
): { label: string; value: StatusMenuAction }[] {
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
    {
      label: subagentsPanelVisible ? 'Hide Subagents Panel' : 'Show Subagents Panel',
      value: 'toggleSubagentsPanel',
    },
    ...(activeModelVision ? [{ label: 'Attach Image…', value: 'attachImage' as const }] : []),
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
  subagentsPanelVisible,
  activeModelVision = false,
  onPickModel,
  onPickThinking,
  onSetPermissionMode,
  onShowSessionStats,
  onNewSession,
  onReloadSkills,
  onToggleTaskPanel,
  onToggleSubagentsPanel,
  onAttachImage,
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
      case 'toggleSubagentsPanel':
        onToggleSubagentsPanel();
        break;
      case 'attachImage':
        onAttachImage();
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
        data={menuItems(taskPanelVisible, subagentsPanelVisible, activeModelVision)}
        onvsc-context-menu-select={handleSelect}
      />
    </>
  );
}
