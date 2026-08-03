// © Copyright 2026 Aaron Kimball
import { type ForwardedRef, type JSX, type ReactNode, forwardRef } from 'react';

export type IconButtonVariant = 'toolbar' | 'filled' | 'toggle' | 'outlined';

interface IconButtonProps {
  /** The icon shown inside the button: a `<vscode-icon name="..." />` for a codicon, or a
   * custom SVG icon component (see `webview/components/klorbIcons`) when no codicon fits. */
  children: ReactNode;
  /** 'toolbar' (the default) is borderless with a hover background, for an icon that sits
   * directly on the panel background (`PanelHeader`'s session-history/new-session buttons).
   * 'filled' is a solid `--vscode-button-background` square, for an icon that should read as a
   * primary action (`StatusMenu`'s chevron).
   * 'toggle' is a bordered button that switches between an outline (inactive) and a solid
   * filled background (active) -- used by status-row panel toggles.
   * 'outlined' is a bordered button with a transparent background and no active/inactive
   * state -- the static-border counterpart to 'toggle'. */
  variant?: IconButtonVariant;
  /** When `variant` is 'toggle', controls whether the button renders with a solid background
   * (`true`) or an outline border only (`false`). Ignored for other variants. */
  active?: boolean;
  /** Doubles as the accessible name (`aria-label`) and, via the native `title` attribute, the
   * host's hover tooltip. */
  title: string;
  onClick(): void;
}

/**
 * An 18x18 icon-only affordance button. Shared by every place in the panel chrome that wants
 * one instead of a full `vscode-button` -- see docs/specs/vscode-plugin-ui.md's "Buttons"
 * section for when this applies over `vscode-button` or a bare status chip.
 */
const IconButton = forwardRef(function IconButton(
  { children, variant = 'toolbar', active = false, title, onClick }: IconButtonProps,
  ref: ForwardedRef<HTMLButtonElement>
): JSX.Element {
  const classes = `icon-button icon-button-${variant}${variant === 'toggle' && active ? ' icon-button-toggle-active' : ''}`;
  return (
    <button
      ref={ref}
      type="button"
      className={classes}
      title={title}
      aria-label={title}
      aria-pressed={variant === 'toggle' ? active : undefined}
      onClick={onClick}>
      {children}
    </button>
  );
});

export default IconButton;
