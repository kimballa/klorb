/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import StatusMenu, { type StatusMenuProps } from 'webview/components/StatusMenu';

afterEach(cleanup);

function mountStatusMenu(overrides: Partial<StatusMenuProps> = {}): StatusMenuProps {
  const callbacks: StatusMenuProps = {
    onPickModel: vi.fn(),
    onPickThinking: vi.fn(),
    onSetPermissionMode: vi.fn(),
    onShowSessionStats: vi.fn(),
    onNewSession: vi.fn(),
    onReloadSkills: vi.fn(),
    ...overrides,
  };
  render(<StatusMenu {...callbacks} />);
  return callbacks;
}

/** Dispatches the `vsc-context-menu-select` custom event `<vscode-context-menu>` fires when an
 * item is picked, mirroring what the real web component does on click/Enter -- there's no real
 * `@vscode-elements/elements` custom element definition loaded under jsdom (only `main.tsx`
 * registers it, for the real webview), so tests drive the same event contract directly. */
function selectMenuItem(value: string): void {
  // eslint-disable-next-line testing-library/no-node-access
  const menu = document.querySelector('vscode-context-menu');
  expect(menu).not.toBeNull();
  fireEvent(
    menu as Element,
    new CustomEvent('vsc-context-menu-select', {
      detail: { value, label: '', keybinding: '', separator: false, tabindex: 0 },
    })
  );
}

describe('StatusMenu', () => {
  it('opens the popup menu when the chevron button is clicked', () => {
    mountStatusMenu();

    // eslint-disable-next-line testing-library/no-node-access
    const menu = document.querySelector('vscode-context-menu');
    expect((menu as unknown as { show: boolean }).show).toBeFalsy();

    fireEvent.click(screen.getByLabelText('Klorb session commands'));
    expect((menu as unknown as { show: boolean }).show).toBe(true);
  });

  it('dispatches the matching callback for each menu action', () => {
    const callbacks = mountStatusMenu();

    selectMenuItem('model');
    expect(callbacks.onPickModel).toHaveBeenCalledOnce();

    selectMenuItem('thinking');
    expect(callbacks.onPickThinking).toHaveBeenCalledOnce();

    selectMenuItem('permissionMode');
    expect(callbacks.onSetPermissionMode).toHaveBeenCalledOnce();

    selectMenuItem('sessionStats');
    expect(callbacks.onShowSessionStats).toHaveBeenCalledOnce();

    selectMenuItem('newSession');
    expect(callbacks.onNewSession).toHaveBeenCalledOnce();

    selectMenuItem('reloadSkills');
    expect(callbacks.onReloadSkills).toHaveBeenCalledOnce();
  });
});
